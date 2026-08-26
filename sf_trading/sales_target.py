# sf_trading/sales_target.py
"""Branch and sales-person targets, and the actuals they are judged against.

WHERE A TARGET IS SET
---------------------
One place: the **Sales Target** list. A record names a fiscal year, a dimension (Branch or
Sales Person), the thing it belongs to, and twelve monthly amounts. A person's record may
optionally name a branch, which is what makes a cross-branch seller workable -- on this site
Prakash and Shihab Ck both sell out of SFSB and SFSS, so either they get one record for their
whole number, or one record per branch. Both shapes are read the same way here.

WHY NOT ERPNEXT'S OWN TARGETS
-----------------------------
Core hangs targets off the Sales Person master as `Target Detail` rows: one annual figure per
item group, spread over the year by a `Monthly Distribution`. It cannot express a Branch target
at all (Branch is an HR doctype with no target concept), and the annual-plus-distribution shape
cannot hold "March is the quiet month, give it 40". Both were empty on this site anyway
(0 Target Detail, 0 Monthly Distribution), so nothing is being replaced.

ONE ENGINE, EVERY SURFACE
-------------------------
Reports, number cards and dashboard charts all read `actuals()` and `targets()` from here. Two
surfaces disagreeing about "August sales" is the failure this avoids -- the DCR/VAT split taught
that lesson already. The rules, in one place:

  * an actual is a SUBMITTED Sales Invoice, and credit notes count against it. A return carries
    its totals negative, so summing over `is_return` 0 and 1 together gives net sales without a
    special case.
  * amounts are company currency (`base_*`), because a target is a company number.
  * Net of VAT uses `base_net_total`, Gross uses `base_grand_total`. Net is the default: VAT is
    not revenue.
  * a group Sales Person ("Sales Team" here) is never a row -- it is a tree node, and its
    two stray invoices would otherwise read as a seventh salesman.
  * Branch user permissions are applied, so a branch head sees their own branch and nobody
    else's, on every surface at once.
"""

import json

import frappe
from frappe import _
from frappe.utils import add_months, flt, get_first_day, get_last_day, getdate, nowdate

TARGET_DOCTYPE = "Sales Target"
MONTHS = [
	"January", "February", "March", "April", "May", "June",
	"July", "August", "September", "October", "November", "December",
]
DIMENSIONS = {
	"Branch": {"doctype": "Branch", "column": "si.branch"},
	"Sales Person": {"doctype": "Sales Person", "column": "si.custom_sales_person"},
}
BASIS_FIELD = {"Net of VAT": "si.base_net_total", "Gross": "si.base_grand_total"}
PERIOD_SIZE = {"Monthly": 1, "Quarterly": 3, "Half-Yearly": 6, "Yearly": 12}
UNASSIGNED = "Unassigned"


# ─── Calendar ─────────────────────────────────────────────────────────────────

def fiscal_year_bounds(fiscal_year: str):
	fy = frappe.db.get_value(
		"Fiscal Year", fiscal_year, ["year_start_date", "year_end_date"], as_dict=True
	)
	if not fy:
		frappe.throw(_("Fiscal Year {0} does not exist").format(fiscal_year))
	return getdate(fy.year_start_date), getdate(fy.year_end_date)


def month_slots(fiscal_year: str) -> list:
	"""The fiscal year's twelve months, walked from its own start.

	Read from the Fiscal Year rather than assuming January: this company runs a calendar year
	today, but a April-March year would silently mis-bucket every figure if hardcoded.
	"""
	start, end = fiscal_year_bounds(fiscal_year)
	slots, cursor = [], get_first_day(start)
	for _i in range(12):
		slots.append(
			frappe._dict(
				month=MONTHS[cursor.month - 1],
				month_no=cursor.month,
				start=max(cursor, start),
				end=min(get_last_day(cursor), end),
			)
		)
		cursor = get_first_day(add_months(cursor, 1))
	return slots


def buckets(fiscal_year: str, period: str = "Monthly") -> list:
	"""Report columns: one bucket per month, quarter, half or year."""
	size = PERIOD_SIZE.get(period or "Monthly", 1)
	slots = month_slots(fiscal_year)
	out = []
	for i in range(0, 12, size):
		group = slots[i : i + size]
		if size == 1:
			label = group[0].month
		elif size == 12:
			label = fiscal_year
		else:
			label = f"{group[0].month[:3]} - {group[-1].month[:3]}"
		out.append(
			frappe._dict(
				key=f"b{i // size}",
				label=label,
				months=[g.month for g in group],
				start=group[0].start,
				end=group[-1].end,
			)
		)
	return out


# ─── Permissions ──────────────────────────────────────────────────────────────

def allowed_branches() -> list | None:
	"""Branches this user may see, or None for no restriction.

	Branch is already a user-permission dimension on this site, so honouring it here means
	every report and card inherits it without each one remembering to.
	"""
	if frappe.session.user == "Administrator":
		return None
	perms = (frappe.defaults.get_user_permissions() or {}).get("Branch") or []
	values = [p.get("doc") for p in perms if p.get("doc")]
	return values or None


@frappe.whitelist()
@frappe.validate_and_sanitize_search_inputs
def branch_query(doctype, txt, searchfield, start, page_len, filters):
	"""Branches belonging to a company, for the Sales Target form.

	Core's Branch carries no `company` field -- filtering a link on one returns
	`Unknown column 'tabBranch.company'` and the picker dies. What ties a branch to a company
	here is this app's own Branch Configuration, so ask that. Sites that have not configured a
	branch yet still get the full list rather than an empty picker.
	"""
	company = (filters or {}).get("company")
	like = f"%{txt or ''}%"
	if company and frappe.db.exists("Branch Configuration", {"company": company}):
		return frappe.db.sql(
			"""
			select b.name from `tabBranch` b
			where b.name like %(txt)s
			  and exists (
				select 1 from `tabBranch Configuration` bc
				where bc.branch = b.name and bc.company = %(company)s
			  )
			order by b.name limit %(start)s, %(page_len)s
			""",
			{"txt": like, "company": company, "start": start, "page_len": page_len},
		)
	return frappe.db.sql(
		"""select name from `tabBranch` where name like %(txt)s order by name
		   limit %(start)s, %(page_len)s""",
		{"txt": like, "start": start, "page_len": page_len},
	)


# ─── Actuals ──────────────────────────────────────────────────────────────────

def actuals(company: str, fiscal_year: str, dimension: str, basis: str = "Net of VAT",
            branch: str | None = None, include_unassigned: bool = True) -> dict:
	"""{(dimension value, month name): amount} of net sales."""
	if dimension not in DIMENSIONS:
		frappe.throw(_("Unknown dimension {0}").format(dimension))
	start, end = fiscal_year_bounds(fiscal_year)
	amount = BASIS_FIELD.get(basis or "Net of VAT", BASIS_FIELD["Net of VAT"])
	column = DIMENSIONS[dimension]["column"]

	conditions = ["si.docstatus = 1", "si.company = %(company)s",
	              "si.posting_date between %(start)s and %(end)s"]
	values = {"company": company, "start": start, "end": end}

	branches = allowed_branches()
	if branch:
		conditions.append("si.branch = %(branch)s")
		values["branch"] = branch
	elif branches:
		conditions.append("si.branch in %(branches)s")
		values["branches"] = branches

	join = ""
	if dimension == "Sales Person":
		# a tree node is not a salesman
		join = "left join `tabSales Person` sp on sp.name = si.custom_sales_person"
		conditions.append("(sp.name is null or sp.is_group = 0)")
		if not include_unassigned:
			conditions.append("si.custom_sales_person is not null and si.custom_sales_person != ''")

	rows = frappe.db.sql(
		f"""
		select {column} as dimension_value, month(si.posting_date) as month_no,
		       sum({amount}) as amount
		from `tabSales Invoice` si {join}
		where {" and ".join(conditions)}
		group by dimension_value, month_no
		""",
		values,
		as_dict=True,
	)
	out = {}
	for r in rows:
		key = r.dimension_value or UNASSIGNED
		out[(key, MONTHS[r.month_no - 1])] = flt(r.amount)
	return out


# ─── Targets ──────────────────────────────────────────────────────────────────

def targets(company: str, fiscal_year: str, dimension: str, branch: str | None = None) -> dict:
	"""{(dimension value, month name): amount} from the Sales Target records.

	With no branch asked for, a person's branch-split records and their whole-number record are
	added together -- which is the same total either way of setting them up, so the report reads
	right whichever shape the user chose. Ask for one branch and only that branch's records
	answer; a whole-number target cannot be split after the fact and is deliberately not guessed.
	"""
	conditions = ["st.company = %(company)s", "st.fiscal_year = %(fiscal_year)s",
	              "st.dimension_type = %(dimension)s", "ifnull(st.disabled, 0) = 0"]
	values = {"company": company, "fiscal_year": fiscal_year, "dimension": dimension}
	if branch:
		conditions.append("st.branch = %(branch)s")
		values["branch"] = branch

	rows = frappe.db.sql(
		f"""
		select st.dimension_value, m.month, sum(m.target_amount) as amount
		from `tab{TARGET_DOCTYPE}` st
		join `tabSales Target Month` m on m.parent = st.name
		where {" and ".join(conditions)}
		group by st.dimension_value, m.month
		""",
		values,
		as_dict=True,
	)
	return {(r.dimension_value, r.month): flt(r.amount) for r in rows}


# ─── Shared report body ───────────────────────────────────────────────────────

def variance_dataset(filters, dimension: str):
	"""(columns, data) for a target-vs-actual report. Both reports are this function."""
	filters = frappe._dict(filters or {})
	company = filters.company or frappe.defaults.get_user_default("Company")
	fiscal_year = filters.fiscal_year or frappe.defaults.get_user_default("fiscal_year")
	if not company or not fiscal_year:
		frappe.throw(_("Company and Fiscal Year are both needed"))
	basis = filters.basis or "Net of VAT"
	branch = filters.branch
	period = filters.period or "Monthly"

	# The branch filter reads differently per dimension, and getting this backwards returns a
	# silently empty grid: a BRANCH target carries no `branch` field of its own (the branch IS
	# the dimension value), so it must be matched by name, while a PERSON target may carry one.
	act = actuals(company, fiscal_year, dimension, basis, branch)
	tgt = targets(company, fiscal_year, dimension,
	              branch if dimension == "Sales Person" else None)
	currency = frappe.get_cached_value("Company", company, "default_currency")

	label = _("Branch") if dimension == "Branch" else _("Sales Person")
	columns = [
		{"label": label, "fieldname": "dimension_value", "fieldtype": "Link",
		 "options": DIMENSIONS[dimension]["doctype"], "width": 180},
	]
	for b in buckets(fiscal_year, period):
		for suffix, field in ((_("Target"), "target"), (_("Actual"), "actual"), (_("Variance"), "variance")):
			columns.append({
				"label": f"{b.label} {suffix}", "fieldname": f"{b.key}_{field}",
				"fieldtype": "Currency", "options": "currency", "width": 130,
			})
	columns += [
		{"label": _("Total Target"), "fieldname": "total_target", "fieldtype": "Currency",
		 "options": "currency", "width": 140},
		{"label": _("Total Actual"), "fieldname": "total_actual", "fieldtype": "Currency",
		 "options": "currency", "width": 140},
		{"label": _("Variance"), "fieldname": "total_variance", "fieldtype": "Currency",
		 "options": "currency", "width": 130},
		{"label": _("Achieved %"), "fieldname": "achieved_pct", "fieldtype": "Percent", "width": 110},
		{"label": _("Currency"), "fieldname": "currency", "fieldtype": "Link", "options": "Currency",
		 "hidden": 1, "width": 80},
	]

	names = {k[0] for k in act} | {k[0] for k in tgt}
	if branch and dimension == "Branch":
		names = {n for n in names if n == branch}
	data = []
	for name in sorted(names):
		row = {"dimension_value": name, "currency": currency}
		total_t = total_a = 0.0
		for b in buckets(fiscal_year, period):
			t = sum(flt(tgt.get((name, m))) for m in b.months)
			a = sum(flt(act.get((name, m))) for m in b.months)
			row[f"{b.key}_target"] = t
			row[f"{b.key}_actual"] = a
			row[f"{b.key}_variance"] = a - t
			total_t += t
			total_a += a
		row["total_target"] = total_t
		row["total_actual"] = total_a
		row["total_variance"] = total_a - total_t
		row["achieved_pct"] = (total_a / total_t * 100) if total_t else 0.0
		data.append(row)

	data.sort(key=lambda r: r["total_actual"], reverse=True)
	return columns, data


# ─── Scorecard (month to date / year to date) ─────────────────────────────────

def scorecard(company: str, fiscal_year: str, dimension: str, basis: str = "Net of VAT",
              as_on: str | None = None, branch: str | None = None) -> list:
	as_on = getdate(as_on or nowdate())
	act = actuals(company, fiscal_year, dimension, basis, branch)
	tgt = targets(company, fiscal_year, dimension, branch)
	slots = month_slots(fiscal_year)
	this_month = [s for s in slots if s.start <= as_on <= s.end]
	current = this_month[0].month if this_month else None
	elapsed = [s.month for s in slots if s.start <= as_on]

	# a month in progress is credited pro rata, so a target is not "missed" on the 2nd
	fraction = 1.0
	if this_month:
		days = (this_month[0].end - this_month[0].start).days + 1
		fraction = ((as_on - this_month[0].start).days + 1) / days

	rows = []
	for name in sorted({k[0] for k in act} | {k[0] for k in tgt}):
		mtd_t = flt(tgt.get((name, current))) if current else 0.0
		mtd_a = flt(act.get((name, current))) if current else 0.0
		ytd_t = sum(flt(tgt.get((name, m))) for m in elapsed)
		ytd_a = sum(flt(act.get((name, m))) for m in elapsed)
		rows.append({
			"dimension_value": name,
			"mtd_target": mtd_t, "mtd_actual": mtd_a,
			"mtd_target_to_date": mtd_t * fraction,
			"mtd_pct": (mtd_a / mtd_t * 100) if mtd_t else 0.0,
			"ytd_target": ytd_t, "ytd_actual": ytd_a,
			"ytd_pct": (ytd_a / ytd_t * 100) if ytd_t else 0.0,
			"variance": ytd_a - ytd_t,
		})
	rows.sort(key=lambda r: r["ytd_actual"], reverse=True)
	return rows


# ─── Number cards ─────────────────────────────────────────────────────────────
#
# A Custom number card calls its method with the dashboard's filters and takes back either a
# formatted string or {"value": …, "fieldtype": …} (frappe/public/js/frappe/widgets/
# number_card_widget.js: get_number_for_custom_card). Currency is returned explicitly because
# this company reports in BHD at three decimals, and a card rounds whatever it is given.

def _card_context(filters):
	if isinstance(filters, str):
		filters = json.loads(filters or "{}")
	filters = frappe._dict(filters or {})
	company = filters.get("company") or frappe.defaults.get_user_default("Company")
	as_on = getdate(filters.get("as_on") or nowdate())
	fiscal_year = filters.get("fiscal_year") or frappe.db.get_value(
		"Fiscal Year", {"year_start_date": ["<=", as_on], "year_end_date": [">=", as_on]}, "name"
	)
	dimension = filters.get("dimension_type") or "Branch"
	return frappe._dict(
		company=company, fiscal_year=fiscal_year, as_on=as_on, dimension=dimension,
		basis=filters.get("basis") or "Net of VAT", branch=filters.get("branch"),
		currency=frappe.get_cached_value("Company", company, "default_currency") if company else None,
	)


def _totals(filters):
	c = _card_context(filters)
	if not (c.company and c.fiscal_year):
		return c, {}
	rows = scorecard(c.company, c.fiscal_year, c.dimension, c.basis, c.as_on, c.branch)
	agg = {k: sum(flt(r[k]) for r in rows) for k in
	       ("mtd_target", "mtd_actual", "ytd_target", "ytd_actual")}
	return c, agg


def _money(c, value):
	return {"value": flt(value), "fieldtype": "Currency", "currency": c.currency}


def _percent(value):
	return {"value": flt(value), "fieldtype": "Percent"}


@frappe.whitelist()
def card_mtd_actual(filters=None):
	c, agg = _totals(filters)
	return _money(c, agg.get("mtd_actual"))


@frappe.whitelist()
def card_mtd_target(filters=None):
	c, agg = _totals(filters)
	return _money(c, agg.get("mtd_target"))


@frappe.whitelist()
def card_mtd_achievement(filters=None):
	_c, agg = _totals(filters)
	t = flt(agg.get("mtd_target"))
	return _percent((flt(agg.get("mtd_actual")) / t * 100) if t else 0)


@frappe.whitelist()
def card_ytd_actual(filters=None):
	c, agg = _totals(filters)
	return _money(c, agg.get("ytd_actual"))


@frappe.whitelist()
def card_ytd_target(filters=None):
	c, agg = _totals(filters)
	return _money(c, agg.get("ytd_target"))


@frappe.whitelist()
def card_ytd_achievement(filters=None):
	_c, agg = _totals(filters)
	t = flt(agg.get("ytd_target"))
	return _percent((flt(agg.get("ytd_actual")) / t * 100) if t else 0)


@frappe.whitelist()
def card_ytd_shortfall(filters=None):
	c, agg = _totals(filters)
	return _money(c, flt(agg.get("ytd_actual")) - flt(agg.get("ytd_target")))
