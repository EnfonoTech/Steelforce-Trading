# Copyright (c) 2026, Enfono Technologies and contributors
# For license information, please see license.txt

"""Entries by User: how many vouchers each user has made, and of which type.

An entry is a document -- a voucher -- and its user is whoever CREATED it (`owner`), not whoever later edited or
submitted it. One grouped query per voucher type counts them by user and docstatus, so Draft, Submitted and
Cancelled sit side by side; Total is all three. The dates are each document's own date (posting date, or
transaction date for orders and quotations), and the company is the document's company.

Two views of the same counts: one row per user and voucher type (with the share of that user's entries it is),
or a pivot with one row per user and one column per voucher type. Users are ranked by their total. Only
Accounts Managers and System Managers may open it: it ranks people.
"""

import frappe
from frappe import _
from frappe.query_builder.functions import Count
from frappe.utils import add_days, getdate

ALLOWED_ROLES = {"Accounts Manager", "System Manager"}

#: the voucher types counted. A doctype this site does not have is skipped; add a line to count another.
VOUCHER_TYPES = (
	"Sales Invoice", "Purchase Invoice", "Payment Entry", "Journal Entry", "Stock Entry", "Delivery Note",
	"Purchase Receipt", "Sales Order", "Purchase Order", "Quotation", "Stock Reconciliation",
	"Landed Cost Voucher", "Payment Advice", "Material Request",
)
#: a document's own date: the first of these it has (creation is on every document)
DATE_FIELDS = ("posting_date", "transaction_date", "creation")

BY_USER_AND_TYPE = "By User and Voucher Type"
PIVOT = "Pivot (User x Voucher Type)"


def execute(filters=None):
	filters = frappe._dict(filters or {})
	if not (frappe.session.user == "Administrator" or ALLOWED_ROLES & set(frappe.get_roles())):
		frappe.throw(_("Only an Accounts Manager can see how many entries each user makes."), frappe.PermissionError)
	if not filters.company:
		frappe.throw(_("Pick a Company."))

	types = _types(filters)
	counts = {voucher_type: _counts(voucher_type, filters) for voucher_type in types}
	agg = aggregate(counts)
	names = _full_names({user for user, _vt in agg})
	pivot = filters.view == PIVOT
	rows = pivot_rows(agg, names) if pivot else detail_rows(agg, names)
	columns = _pivot_columns(agg) if pivot else _detail_columns()
	return columns, rows, None, _chart(agg, names), _summary(agg, names)


# ── The data ────────────────────────────────────────────────────────────────────────

def _types(filters) -> list:
	"""The voucher types to count: the one picked, or every one this site has."""
	if filters.get("voucher_type"):
		if filters.voucher_type not in VOUCHER_TYPES:
			frappe.throw(_("{0} is not a voucher type counted here.").format(frappe.bold(filters.voucher_type)))
		wanted = [filters.voucher_type]
	else:
		wanted = list(VOUCHER_TYPES)
	return [vt for vt in wanted if frappe.db.exists("DocType", vt) and frappe.db.table_exists(vt)]


def date_field(meta) -> str:
	return next((f for f in DATE_FIELDS if f == "creation" or meta.has_field(f)), "creation")


def count_query(voucher_type, filters, field, has_company):
	"""Documents of one type in the dates and company, counted by creator and docstatus."""
	table = frappe.qb.DocType(voucher_type)
	query = (
		frappe.qb.from_(table)
		.select(table.owner, table.docstatus, Count("*").as_("n"))
		.groupby(table.owner, table.docstatus)
	)
	if has_company:
		query = query.where(table.company == filters.company)
	if filters.get("from_date"):
		query = query.where(table[field] >= getdate(filters.from_date))
	if filters.get("to_date"):
		# a Datetime (creation) includes the whole last day
		last = add_days(getdate(filters.to_date), 1)
		query = query.where(table[field] < last) if field == "creation" else query.where(table[field] <= getdate(filters.to_date))
	if filters.get("user"):
		query = query.where(table.owner == filters.user)
	return query


def _counts(voucher_type, filters) -> list:
	meta = frappe.get_meta(voucher_type)
	query = count_query(voucher_type, filters, date_field(meta), meta.has_field("company"))
	return [(row.owner, row.docstatus, row.n) for row in query.run(as_dict=True)]


def _full_names(users) -> dict:
	users = [u for u in users if u]
	if not users:
		return {}
	return dict(frappe.get_all("User", filters={"name": ["in", users]}, fields=["name", "full_name"], as_list=True))


# ── The counts, ranked (pure: counts in, rows out) ──────────────────────────────────

def aggregate(counts) -> dict:
	"""{(user, voucher type): [draft, submitted, cancelled]} from {voucher type: [(user, docstatus, n)]}."""
	agg = {}
	for voucher_type, rows in counts.items():
		for user, docstatus, n in rows:
			if docstatus not in (0, 1, 2):
				continue
			agg.setdefault((user, voucher_type), [0, 0, 0])[docstatus] += n
	return agg


def user_totals(agg) -> dict:
	totals = {}
	for (user, _voucher_type), cells in agg.items():
		totals[user] = totals.get(user, 0) + sum(cells)
	return totals


def _ranked_users(agg) -> list:
	totals = user_totals(agg)
	return sorted(totals, key=lambda u: (-totals[u], u))


def _share(part, whole) -> str:
	return "%.1f%%" % (part * 100.0 / whole) if whole else ""


def detail_rows(agg, names) -> list:
	"""One row per user and voucher type, the busiest user first and each user's biggest voucher type first."""
	totals = user_totals(agg)
	rows = []
	for rank, user in enumerate(_ranked_users(agg), start=1):
		mine = sorted(((vt, cells) for (u, vt), cells in agg.items() if u == user), key=lambda item: (-sum(item[1]), item[0]))
		for voucher_type, (draft, submitted, cancelled) in mine:
			total = draft + submitted + cancelled
			rows.append(
				{
					"rank": str(rank),
					"user": user,
					"full_name": names.get(user) or user,
					"voucher_type": voucher_type,
					"draft": draft,
					"submitted": submitted,
					"cancelled": cancelled,
					"total": total,
					"share": _share(total, totals[user]),
				}
			)
	return rows


def _types_used(agg) -> list:
	used = {voucher_type for (_user, voucher_type) in agg}
	return [vt for vt in VOUCHER_TYPES if vt in used]


def pivot_rows(agg, names) -> list:
	"""One row per user, one column per voucher type (only the types somebody made), and Total."""
	types = _types_used(agg)
	rows = []
	for rank, user in enumerate(_ranked_users(agg), start=1):
		row = {"rank": str(rank), "user": user, "full_name": names.get(user) or user}
		total = 0
		for voucher_type in types:
			count = sum(agg.get((user, voucher_type), [0, 0, 0]))
			row[frappe.scrub(voucher_type)] = count
			total += count
		row["total"] = total
		rows.append(row)
	return rows


# ── Columns, chart, summary ─────────────────────────────────────────────────────────

def _column(label, fieldname, fieldtype="Int", width=100, **extra):
	return {"label": _(label), "fieldname": fieldname, "fieldtype": fieldtype, "width": width, **extra}


def _detail_columns():
	return [
		_column("Rank", "rank", "Data", 60),
		_column("User", "user", "Link", 200, options="User"),
		_column("Name", "full_name", "Data", 160),
		_column("Voucher Type", "voucher_type", "Data", 170),
		_column("Draft", "draft", width=80),
		_column("Submitted", "submitted", width=95),
		_column("Cancelled", "cancelled", width=95),
		_column("Total", "total", width=85),
		_column("Share Of User's Entries", "share", "Data", 160),
	]


def _pivot_columns(agg):
	columns = [_column("Rank", "rank", "Data", 60), _column("User", "user", "Link", 200, options="User"), _column("Name", "full_name", "Data", 160)]
	columns += [_column(voucher_type, frappe.scrub(voucher_type), width=max(95, len(voucher_type) * 8)) for voucher_type in _types_used(agg)]
	columns.append(_column("Total", "total", width=90))
	return columns


def _chart(agg, names):
	totals = user_totals(agg)
	top = _ranked_users(agg)[:10]
	if not top:
		return None
	return {
		"data": {"labels": [names.get(u) or u for u in top], "datasets": [{"name": _("Entries"), "values": [totals[u] for u in top]}]},
		"type": "bar",
	}


def _summary(agg, names):
	totals = user_totals(agg)
	by_type = {}
	for (_user, voucher_type), cells in agg.items():
		by_type[voucher_type] = by_type.get(voucher_type, 0) + sum(cells)
	busiest_user = _ranked_users(agg)[0] if totals else None
	busiest_type = max(by_type, key=lambda vt: (by_type[vt], vt)) if by_type else None
	return [
		{"value": len(totals), "label": _("Users"), "datatype": "Int", "indicator": "Blue"},
		{"value": sum(totals.values()), "label": _("Entries"), "datatype": "Int", "indicator": "Green"},
		{"value": sum(cells[0] for cells in agg.values()), "label": _("Still Draft"), "datatype": "Int", "indicator": "Orange"},
		{"value": sum(cells[2] for cells in agg.values()), "label": _("Cancelled"), "datatype": "Int", "indicator": "Red"},
		{"value": (names.get(busiest_user) or busiest_user) if busiest_user else "-", "label": _("Busiest User"), "datatype": "Data", "indicator": "Blue"},
		{"value": busiest_type or "-", "label": _("Busiest Voucher Type"), "datatype": "Data", "indicator": "Blue"},
	]
