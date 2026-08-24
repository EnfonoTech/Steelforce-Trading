# sf_trading/branch_price_list.py
"""Branch-wise pricing, through the Price List rather than around it.

The ask was branch-wise Item Price. Item Price already keys on a Price List, and ERPNext
resolves a document's rates from the Price List named on that document — so the way to price a
branch differently is to give the branch its own price list, not to teach Item Price about
branches. Doing it the other way means patching `erpnext.stock.get_item_details.get_item_price`
and overriding Item Price's duplicate check, and then every upgrade is a risk and every other
feature that reads a price (Pricing Rules, the POS, the minimum-margin check, the item search
dropdown) has to be taught the same trick separately. Priced through the Price List, all of
that keeps working untouched.

The mapping is modelled on ERPNext's own **Applicable for Countries** on Price List, and reads
the same way:

* A price list with **no** branches applies everywhere. That is every price list on the site
  today, which is why nothing changes until somebody fills the table in.
* A price list naming branches is *the* list for those branches. One list can serve several
  branches, which is the common case — three branches on one retail price, one branch on its
  own.
* A branch may not be claimed by two enabled selling lists (nor by two buying lists). That is
  refused when the price list is saved, naming the other list, because an ambiguous answer here
  would be a silent wrong price.

Branch is never mandatory. A document with no branch, or a branch no list names, is priced
exactly as before.

A branch list holds differences, not a whole catalogue
------------------------------------------------------
ERPNext prices a row from ONE list. Swap the list and any item that list does not price returns
nothing -- `get_price_list_rate_for` gives None and the row keeps whatever it had, which on a new
row is zero. A branch that prices twenty items differently would therefore zero-rate the other
sixteen thousand.

So a row the branch's list is silent about falls back to the list the document would otherwise
have used. That is what "this branch charges differently for these items" means to the people
using it, and it is the difference between a price list that holds a handful of overrides and one
that has to be maintained in full for every branch.

What gets replaced, and what does not
-------------------------------------
Applied at `before_validate`, which is the last point before ERPNext prices the rows. It sets
the price list when the document is carrying the *default* one — empty, or the party's default,
or the group's, or the global Selling/Buying Settings default, or another branch's list. It
leaves a list somebody chose deliberately alone: overriding that would make the field
unusable. On the form the switch is immediate, on `branch` change, so nobody is surprised at
save time.
"""

import frappe
from frappe import _
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields
from frappe.utils import flt

BRANCH_TABLE = "custom_branches"
CHILD_DOCTYPE = "Price List Branch"

# The price list field each side of the business keeps its answer in.
PRICE_LIST_FIELD = {"selling": "selling_price_list", "buying": "buying_price_list"}

SELLING_DOCTYPES = ("Quotation", "Sales Order", "Delivery Note", "Sales Invoice")
BUYING_DOCTYPES = ("Supplier Quotation", "Purchase Order", "Purchase Receipt", "Purchase Invoice")


def ensure_custom_fields():
	"""after_migrate: the branch table on Price List, right under Applicable for Countries."""
	create_custom_fields(
		{
			"Price List": [
				{
					"fieldname": BRANCH_TABLE,
					"label": "Applicable for Branches",
					"fieldtype": "Table",
					"options": CHILD_DOCTYPE,
					"insert_after": "countries",
					"description": (
						"Leave empty and this price list applies to every branch. Name branches and "
						"it becomes the price list for those branches — their documents are priced "
						"from it instead of the default list."
					),
				}
			]
		},
		ignore_validate=True,
	)


# ─── Resolution ───────────────────────────────────────────────────────────────

def kind_for(doctype: str) -> str | None:
	if doctype in SELLING_DOCTYPES:
		return "selling"
	if doctype in BUYING_DOCTYPES:
		return "buying"
	return None


def mapped_price_lists(kind: str) -> dict:
	"""{branch: price list} for every enabled price list of this kind that names branches."""
	# A Table field puts no column on the parent, so the child table is the thing to test — and
	# it is absent on a bench that has pulled this code but not migrated yet.
	if not frappe.db.table_exists(CHILD_DOCTYPE):
		return {}

	rows = frappe.get_all(
		CHILD_DOCTYPE,
		filters={"parenttype": "Price List", "parentfield": BRANCH_TABLE, "branch": ["is", "set"]},
		fields=["branch", "parent"],
		ignore_permissions=True,
	)
	if not rows:
		return {}

	enabled = set(
		frappe.get_all(
			"Price List",
			filters={"enabled": 1, kind: 1, "name": ["in", list({r.parent for r in rows})]},
			pluck="name",
			ignore_permissions=True,
		)
	)

	mapping = {}
	for row in rows:
		if row.parent in enabled:
			# validate_price_list guarantees one list per branch per kind; first wins if a site
			# somehow has two, rather than raising while somebody is only saving an invoice
			mapping.setdefault(row.branch, row.parent)
	return mapping


@frappe.whitelist()
def get_branch_price_list(branch: str, kind: str = "selling") -> str | None:
	"""The price list this branch is priced from, or nothing. What the form asks."""
	if not branch or kind not in PRICE_LIST_FIELD:
		return None
	return mapped_price_lists(kind).get(branch)


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
	"""before_validate: price the document from its branch's list, if it has one.

	before_validate on purpose -- it is the last point before the controller prices the rows.
	A branch that only gets resolved later in validate (from the warehouse, see
	inter_branch.auto_set_branch_from_warehouse) therefore takes effect on the next save, not
	this one; the form sets the list the moment the branch is chosen, so this is the API's
	safety net rather than the everyday path.
	"""
	kind = kind_for(doc.doctype)
	if not kind:
		return

	branch = doc.get("branch")
	if not branch:
		return

	fieldname = PRICE_LIST_FIELD[kind]
	mapping = mapped_price_lists(kind)
	wanted = mapping.get(branch)
	if not wanted:
		return

	current = doc.get(fieldname)
	if current == wanted:
		return

	# Replaceable: the default list, or another branch's list (the branch was switched). Not
	# replaceable: anything a person went and picked.
	replaceable = current in _default_price_lists(doc, kind) or current in set(mapping.values())
	if not replaceable:
		return

	doc.set(fieldname, wanted)
	# the currency and conversion rate belong to the list, so let the controller re-read them
	doc.set("price_list_currency", None)
	doc.set("plc_conversion_rate", None)

	_fill_gaps_from(doc, fallback=current, branch_list=wanted)


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
		# only where the branch has nothing to say: a rate somebody typed is never overwritten,
		# because a row already carrying one is left exactly as the controller would leave it
		if flt(row.get("price_list_rate")) and flt(row.get("rate")):
			continue
		row.price_list_rate = rate
		if not flt(row.get("rate")):
			row.rate = rate


# ─── Guarding the mapping itself ───────────────────────────────────────────────

def validate_price_list(doc, method=None):
	"""A branch may be claimed by one enabled selling list and one enabled buying list.

	Two lists claiming the same branch would each be a valid answer to "what is this branch
	priced from", and the document would take whichever the query happened to return first.
	Refused here, where somebody is looking at the price list, rather than later on an invoice.
	"""
	rows = doc.get(BRANCH_TABLE) or []
	if not rows:
		return

	branches = [row.branch for row in rows if row.branch]
	if not branches:
		return

	seen = set()
	for branch in branches:
		if branch in seen:
			frappe.throw(
				_("Branch {0} is listed twice on this price list.").format(frappe.bold(branch)),
				title=_("Duplicate Branch"),
			)
		seen.add(branch)

	if not frappe.db.exists("DocType", CHILD_DOCTYPE):
		return

	for kind in ("selling", "buying"):
		if not doc.get(kind):
			continue
		for branch, price_list in mapped_price_lists(kind).items():
			if branch in seen and price_list != doc.name:
				frappe.throw(
					_("Branch {0} is already priced from {1}, which is also a {2} price list. "
					  "A branch can only be priced from one {2} price list.").format(
						frappe.bold(branch), frappe.bold(price_list), _(kind)
					),
					title=_("Branch Already Priced"),
				)
