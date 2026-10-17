# sf_trading/branch_price_list.py
"""Branch-wise pricing, owned by Branch Configuration.

The ask was branch-wise Item Price. Item Price already keys on a Price List and ERPNext prices a
document from the list named on it, so the way to price a branch differently is to give the branch
its own price list -- not to teach Item Price about branches, which would mean patching
`erpnext.stock.get_item_details.get_item_price`, overriding Item Price's duplicate check, and then
re-teaching Pricing Rules, the minimum-margin check, the item-search dropdown and the POS the same
trick one at a time.

**Where the mapping lives.** On Branch Configuration, beside the branch's modes of payment, its
warehouses, its cost centers and its users. That is already the one place a branch is described and
the one place its access is controlled, so a price list belongs there too -- and it lets a branch
carry several lists with one marked **Default**, which a field on Price List could not express.

  * No rows on the branch -> nothing changes. The branch is priced from the customer's or the
    company's default list and every price list stays selectable, exactly as before.
  * Rows, one marked Default -> documents for that branch are priced from the default, and the
    Price List field only offers the branch's own lists. A branch with a single row needs no tick:
    one list is its own default.
  * A branch may hold one default selling list and one default buying list, no more. Two defaults
    of a kind would each be a valid answer to "what is this branch priced from", and the document
    would take whichever the query returned first.

The same relationship from the Price List side
----------------------------------------------
A Price List also carries an **Applicable Branches** table (Custom Field `custom_applicable_branches`).
Empty, which is every list until somebody fills it, means the list is open to every branch: no
filter, exactly as before. With rows, only those branches may use it -- on the document's Price
List field, and on the per-row Price List of a Sales Invoice. It limits; it does not make the list
a branch's default, because two lists naming one branch would leave "what is this branch priced
from" with two answers. The default is still the ticked row on Branch Configuration.

The two sides may not contradict each other: a list a branch's configuration names cannot leave
that branch out of its Applicable Branches (checked from both forms on save).

A branch list holds differences, not a whole catalogue
------------------------------------------------------
ERPNext prices a row from ONE list. Swap the list and any item that list does not price returns
nothing -- `get_price_list_rate_for` gives None and the row keeps whatever it had, which on a new
row is zero. A branch pricing twenty items differently would therefore zero-rate the other sixteen
thousand. So a row the branch's list is silent about falls back to the list the document would
otherwise have used.

What gets replaced, and what does not
-------------------------------------
Applied at `before_validate`, the last point before the controller prices the rows. It sets the
price list when the document is carrying a *default* one -- empty, or the party's, or the group's,
or the global Selling/Buying Settings default, or another branch's list. A list somebody chose
deliberately from the branch's own set is left alone. On the form the switch is immediate, on
`branch` change, so nobody types against prices that move at save.
"""

import frappe
from frappe import _
from frappe.utils import cint, flt

CHILD = "Branch Configuration Price List"
TABLE_FIELD = "price_list"

# The Price List side: the branches a list is limited to. No rows = open to every branch.
LIMIT_CHILD = "Price List Branch"
LIMIT_FIELD = "custom_applicable_branches"

# The price list field each side of the business keeps its answer in.
PRICE_LIST_FIELD = {"selling": "selling_price_list", "buying": "buying_price_list"}

SELLING_DOCTYPES = ("Quotation", "Sales Order", "Delivery Note", "Sales Invoice")
BUYING_DOCTYPES = ("Supplier Quotation", "Purchase Order", "Purchase Receipt", "Purchase Invoice")


def kind_for(doctype: str) -> str | None:
	if doctype in SELLING_DOCTYPES:
		return "selling"
	if doctype in BUYING_DOCTYPES:
		return "buying"
	return None


# ─── Reading the configuration ────────────────────────────────────────────────

def _rows(branch: str) -> list:
	"""The branch's price list rows, or nothing when the branch names none."""
	if not branch or not frappe.db.table_exists(CHILD):
		return []
	return frappe.get_all(
		CHILD,
		filters={"parent": branch, "parenttype": "Branch Configuration"},
		fields=["price_list", "is_default", "idx"],
		order_by="idx asc",
		ignore_permissions=True,
	)


def _of_kind(rows: list, kind: str) -> list:
	"""Only the rows whose price list is enabled and serves this side of the business."""
	names = [row.price_list for row in rows if row.price_list]
	if not names:
		return []

	usable = set(
		frappe.get_all(
			"Price List",
			filters={"name": ["in", names], "enabled": 1, kind: 1},
			pluck="name",
			ignore_permissions=True,
		)
	)
	return [row for row in rows if row.price_list in usable]


def limited_lists() -> dict:
	"""{price list: {branches}} for every list that names branches. A list that names none is open."""
	if not frappe.db.table_exists(LIMIT_CHILD):
		return {}

	limited = {}
	for row in frappe.get_all(
		LIMIT_CHILD,
		filters={"parenttype": "Price List", "parentfield": LIMIT_FIELD},
		fields=["parent", "branch"],
		ignore_permissions=True,
	):
		if row.branch:
			limited.setdefault(row.parent, set()).add(row.branch)
	return limited


def open_to_branch(price_list: str, branch: str, limited: dict | None = None) -> bool:
	"""May a document of this branch use this list? Only a list that names branches can say no."""
	limited = limited_lists() if limited is None else limited
	branches = limited.get(price_list)
	return not branches or not branch or branch in branches


def excluded_price_lists(branch: str, kind: str) -> list:
	"""Enabled lists of this kind that are limited to other branches and so are hidden from this one."""
	if not branch or kind not in PRICE_LIST_FIELD:
		return []

	away = [name for name, branches in limited_lists().items() if branch not in branches]
	if not away:
		return []
	return frappe.get_all(
		"Price List",
		filters={"name": ["in", away], "enabled": 1, kind: 1},
		pluck="name",
		ignore_permissions=True,
	)


def _open_rows(rows: list, branch: str) -> list:
	"""Only the rows whose list is not limited away from this branch."""
	limited = limited_lists()
	return [row for row in rows if open_to_branch(row.price_list, branch, limited)]


def allowed_price_lists(branch: str, kind: str) -> list:
	"""Every price list this branch may use on this side. Empty means "no opinion"."""
	if kind not in PRICE_LIST_FIELD:
		return []
	return [row.price_list for row in _open_rows(_of_kind(_rows(branch), kind), branch)]


def default_price_list(branch: str, kind: str) -> str | None:
	"""What this branch is priced from: its default row, or its only row."""
	rows = _open_rows(_of_kind(_rows(branch), kind), branch)
	if not rows:
		return None

	default = next((row for row in rows if cint(row.is_default)), None)
	if default:
		return default.price_list
	# a branch with a single list of this kind needs no tick to mean it
	return rows[0].price_list if len(rows) == 1 else None


def branch_managed_lists(kind: str) -> set:
	"""Every price list any branch names, so a switch of branch can replace another's list.

	A list limited to some branches by its own Applicable Branches table is every bit as much one
	branch's list, so it counts too.
	"""
	managed = set()

	if frappe.db.table_exists(CHILD):
		rows = frappe.get_all(
			CHILD,
			filters={"parenttype": "Branch Configuration", "price_list": ["is", "set"]},
			fields=["price_list", "is_default", "idx"],
			ignore_permissions=True,
		)
		managed |= {row.price_list for row in _of_kind(rows, kind)}

	limited = list(limited_lists())
	if limited:
		managed |= set(
			frappe.get_all(
				"Price List",
				filters={"name": ["in", limited], "enabled": 1, kind: 1},
				pluck="name",
				ignore_permissions=True,
			)
		)
	return managed


@frappe.whitelist()
def get_branch_price_list(branch: str, kind: str = "selling") -> dict:
	"""What the form asks when a branch is chosen: the default, and what may be picked."""
	if not branch or kind not in PRICE_LIST_FIELD:
		return {"default": None, "allowed": []}
	return {"default": default_price_list(branch, kind), "allowed": allowed_price_lists(branch, kind)}


@frappe.whitelist()
@frappe.validate_and_sanitize_search_inputs
def branch_price_list_query(doctype, txt, searchfield, start, page_len, filters):
	"""Link query: the price lists a branch may use, for the Price List field on a document.

	Falls back to every enabled list of that kind when the branch names none, so a site that has
	not configured anything sees exactly what it always saw.
	"""
	filters = filters or {}
	kind = filters.get("kind") or "selling"
	branch = filters.get("branch")
	# a row's own Price List is not held to the branch's configured set, only to the lists that
	# their Applicable Branches table keeps away from this branch
	allowed = [] if filters.get("row") else allowed_price_lists(branch, kind)

	conditions = [["enabled", "=", 1], [kind, "=", 1]]
	if allowed:
		conditions.append(["name", "in", [a for a in allowed if not txt or txt.lower() in a.lower()]])
	else:
		if txt:
			conditions.append(["name", "like", f"%{txt}%"])
		excluded = excluded_price_lists(branch, kind)
		if excluded:
			conditions.append(["name", "not in", excluded])

	rows = frappe.get_all(
		"Price List",
		filters=conditions,
		fields=["name", "currency"],
		order_by="name asc",
		limit_start=start,
		limit_page_length=page_len,
	)
	return [[row.name, row.currency] for row in rows]


# ─── Applying it to a document ────────────────────────────────────────────────

def _default_price_lists(doc, kind: str) -> set:
	"""Price lists that count as "nobody chose this" on this document."""
	defaults = {None, ""}

	if kind == "selling":
		defaults.add(frappe.db.get_single_value("Selling Settings", "selling_price_list"))
		party = doc.get("customer") or doc.get("party_name")
		if party and frappe.db.exists("Customer", party):
			customer = frappe.get_cached_value(
				"Customer", party, ["default_price_list", "customer_group"], as_dict=True
			)
			defaults.add(customer.default_price_list)
			if customer.customer_group:
				defaults.add(
					frappe.get_cached_value("Customer Group", customer.customer_group, "default_price_list")
				)
	else:
		defaults.add(frappe.db.get_single_value("Buying Settings", "buying_price_list"))
		party = doc.get("supplier")
		if party and frappe.db.exists("Supplier", party):
			supplier = frappe.get_cached_value(
				"Supplier", party, ["default_price_list", "supplier_group"], as_dict=True
			)
			defaults.add(supplier.default_price_list)
			if supplier.supplier_group:
				defaults.add(
					frappe.get_cached_value("Supplier Group", supplier.supplier_group, "default_price_list")
				)

	return defaults


def apply_branch_price_list(doc, method=None):
	"""before_validate: price the document from its branch's default list, if it has one."""
	kind = kind_for(doc.doctype)
	if not kind:
		return

	branch = doc.get("branch")
	if not branch:
		return

	fieldname = PRICE_LIST_FIELD[kind]
	wanted = default_price_list(branch, kind)
	if not wanted:
		return

	current = doc.get(fieldname)
	if current == wanted:
		return

	# Replaceable: the default list, or a list some other branch manages (the branch was switched).
	# Not replaceable: a list from this branch's own set, which somebody picked on purpose.
	if current in allowed_price_lists(branch, kind):
		return
	if not (current in _default_price_lists(doc, kind) or current in branch_managed_lists(kind)):
		return

	doc.set(fieldname, wanted)
	# the currency and conversion rate belong to the list, so let the controller re-read them
	doc.set("price_list_currency", None)
	doc.set("plc_conversion_rate", None)

	_fill_gaps_from(doc, fallback=current, branch_list=wanted)


def validate_price_list_allowed(doc, method=None):
	"""validate: a document may only use a price list its branch is configured for.

	Only bites when the branch names lists at all -- a branch with none has no opinion, and every
	document on a site that has configured nothing passes untouched.
	"""
	kind = kind_for(doc.doctype)
	if not kind:
		return

	branch = doc.get("branch")
	if not branch:
		return

	limited = limited_lists()
	chosen = doc.get(PRICE_LIST_FIELD[kind])

	if chosen:
		allowed = allowed_price_lists(branch, kind)
		if allowed:
			if chosen not in allowed:
				frappe.throw(
					_("%s is not one of the price lists branch %s may use: %s.")
					% (frappe.bold(chosen), frappe.bold(branch), ", ".join(allowed)),
					title=_("Price List Not Allowed for This Branch"),
				)
		elif not open_to_branch(chosen, branch, limited):
			_refuse_limited(chosen, branch, limited)

	# a Sales Invoice row may carry a price list of its own; the same limit applies to it
	for row in doc.get("items") or []:
		own = row.get("custom_price_list")
		if own and not open_to_branch(own, branch, limited):
			_refuse_limited(own, branch, limited, idx=row.get("idx"))


def _refuse_limited(price_list: str, branch: str, limited: dict, idx=None):
	where = (_("Row %s: ") % idx) if idx else ""
	frappe.throw(
		where
		+ _("%s is limited to branches %s; branch %s is not one of them.")
		% (frappe.bold(price_list), ", ".join(sorted(limited.get(price_list, ()))), frappe.bold(branch)),
		title=_("Price List Not Allowed for This Branch"),
	)


def _fill_gaps_from(doc, fallback: str, branch_list: str):
	"""Price the rows the branch's list says nothing about, from the list it replaced.

	Runs at before_validate, so the controller prices the rest immediately afterwards and simply
	finds these rows already carrying a rate -- `get_price_list_rate` leaves a row alone when it
	finds no Item Price, rather than zeroing it.
	"""
	if not fallback or fallback == branch_list:
		return

	rows = [row for row in (doc.get("items") or []) if row.get("item_code")]
	if not rows:
		return

	priced_on_branch = set(
		frappe.get_all(
			"Item Price",
			filters={
				"price_list": branch_list,
				"item_code": ["in", list({row.item_code for row in rows})],
			},
			pluck="item_code",
			ignore_permissions=True,
		)
	)

	gaps = [row for row in rows if row.item_code not in priced_on_branch]
	if not gaps:
		return

	fallback_rates = {
		price.item_code: flt(price.price_list_rate)
		for price in frappe.get_all(
			"Item Price",
			filters={
				"price_list": fallback,
				"item_code": ["in", list({row.item_code for row in gaps})],
			},
			fields=["item_code", "price_list_rate"],
			order_by="valid_from desc",
			ignore_permissions=True,
		)
	}
	if not fallback_rates:
		return

	for row in gaps:
		rate = fallback_rates.get(row.item_code)
		if not rate:
			continue
		# only where the branch has nothing to say: a rate somebody typed is never overwritten
		if flt(row.get("price_list_rate")) and flt(row.get("rate")):
			continue
		row.price_list_rate = rate
		if not flt(row.get("rate")):
			row.rate = rate


# ─── Guarding the configuration itself ────────────────────────────────────────

def validate_branch_price_lists(doc, method=None):
	"""Called from the Branch Configuration controller: one default per side, no repeats."""
	rows = doc.get(TABLE_FIELD) or []
	if not rows:
		return

	branch = doc.get("branch") or doc.get("name")
	limited = limited_lists()

	seen = set()
	for row in rows:
		if not row.price_list:
			continue
		if not open_to_branch(row.price_list, branch, limited):
			frappe.throw(
				_("Price list %s is limited to branches %s. Add branch %s to its Applicable Branches first, or take it off this branch.")
				% (
					frappe.bold(row.price_list),
					", ".join(sorted(limited.get(row.price_list, ()))),
					frappe.bold(branch),
				),
				title=_("Price List Limited to Other Branches"),
			)
		if row.price_list in seen:
			frappe.throw(
				_("Price list {0} is listed twice on this branch.").format(frappe.bold(row.price_list)),
				title=_("Duplicate Price List"),
			)
		seen.add(row.price_list)

		if not frappe.db.get_value("Price List", row.price_list, "enabled"):
			frappe.throw(
				_("Price list {0} is disabled.").format(frappe.bold(row.price_list)),
				title=_("Price List Disabled"),
			)

	for kind in ("selling", "buying"):
		defaults = [
			row.price_list
			for row in rows
			if cint(row.is_default)
			and row.price_list
			and frappe.db.get_value("Price List", row.price_list, kind)
		]
		if len(defaults) > 1:
			frappe.throw(
				_("Only one {0} price list can be the default for this branch. {1} are both ticked.").format(
					_(kind), frappe.bold(", ".join(defaults))
				),
				title=_("More Than One Default"),
			)


def validate_price_list_branches(doc, method=None):
	"""Price List validate: no branch twice, and no branch left out that its own configuration names."""
	rows = doc.get(LIMIT_FIELD) or []
	if not rows:
		return

	seen = set()
	for row in rows:
		if not row.get("branch"):
			continue
		if row.branch in seen:
			frappe.throw(
				_("Branch %s is listed twice in Applicable Branches.") % frappe.bold(row.branch),
				title=_("Duplicate Branch"),
			)
		seen.add(row.branch)

	if not seen or not doc.get("name") or not frappe.db.table_exists(CHILD):
		return

	left_out = [
		branch
		for branch in frappe.get_all(
			CHILD,
			filters={"parenttype": "Branch Configuration", "price_list": doc.name},
			pluck="parent",
			ignore_permissions=True,
		)
		if branch not in seen
	]
	if left_out:
		frappe.throw(
			_("Branch %s names this price list in its Branch Configuration, so it cannot be left out of Applicable Branches. Add it here, or take the list off that branch.")
			% frappe.bold(", ".join(sorted(set(left_out)))),
			title=_("Branch Left Out"),
		)
