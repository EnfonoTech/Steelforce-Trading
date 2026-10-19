# sf_trading/alternative_cost_center.py
"""Alternative Cost Center -- charging a voucher to a cost centre of ANOTHER company as well as its own.

The need. A company often pays for something that belongs to a sister company (a freight bill, rent, a
salary). The voucher stays in the paying company's books, with its own cost centre, but the management
accounts of the other company must show it too. The user picks, on the voucher or on any of its rows, a cost
centre of the other company; the MIS Profit and Loss (sf_trading/mis_profit_loss.py) then shows it in that
company's figures, separately marked, with the vouchers behind it.

How it is held. The choice is an ERPNext **Accounting Dimension**, which is the one mechanism that carries a
value from a voucher row to its General Ledger entry (the row's value wins over the header's, and rows with
different values are never merged into one ledger line). ERPNext does not let a Cost Center itself be a
dimension, so every leaf cost centre of every company has a mirror master, `Alternative Cost Center`, kept in
step here (insert, change, disable, rename, delete), and the dimension points at that.

The mirror's company is held in `cc_company`, never `company`: ERPNext refuses a voucher whose dimension value
belongs to a different company than the voucher (`validate_company_in_accounting_dimension` checks every dimension
whose doctype has a field called `company`), which is exactly what this dimension exists to allow.

What is checked on a voucher. The alternative cost centre must belong to a company other than the voucher's
own, be enabled and not be a group: charging a cost centre of the same company is what the ordinary cost
centre is for, and a group cannot be a place costs land.

What it does to the books. Nothing: the voucher posts exactly as before. The dimension is an extra column on
the ledger entry; only the MIS reports read it.
"""

import frappe
from frappe import _
from frappe.utils import cint, cstr

DOCTYPE = "Alternative Cost Center"
LABEL = "Alternative Cost Center"
FIELD = "alternative_cost_center"
COST_CENTER = "Cost Center"

#: the vouchers that post profit and loss lines and so can carry the choice; its rows are checked with it
VOUCHERS = (
	"Journal Entry",
	"Sales Invoice",
	"Purchase Invoice",
	"Payment Entry",
	"Expense Claim",
	"Stock Entry",
	"Purchase Receipt",
	"Delivery Note",
)


# ─── the mirror master ──────────────────────────────────────────────────────────────────────────


def _wanted(cost_center) -> dict:
	"""What the mirror of a cost centre should say: a group or a disabled cost centre is not offered."""
	return {
		"cost_center": cost_center.name,
		"cost_center_name": cost_center.get("cost_center_name"),
		"cc_company": cost_center.get("company"),
		"disabled": 1 if cint(cost_center.get("is_group")) or cint(cost_center.get("disabled")) else 0,
	}


def sync_cost_center(doc, method=None):
	"""Cost Center after_insert / on_update: the mirror exists and says what the cost centre says."""
	if doc.doctype != COST_CENTER or not frappe.db.table_exists(DOCTYPE):
		return
	want = _wanted(doc)
	if frappe.db.exists(DOCTYPE, doc.name):
		frappe.db.set_value(DOCTYPE, doc.name, {k: v for k, v in want.items() if k != "cost_center"}, update_modified=False)
	else:
		frappe.get_doc(dict(want, doctype=DOCTYPE)).insert(ignore_permissions=True)


def drop_cost_center(doc, method=None):
	"""Cost Center on_trash: the mirror goes too, unless a voucher still points at it; then it is disabled."""
	if doc.doctype != COST_CENTER or not frappe.db.table_exists(DOCTYPE) or not frappe.db.exists(DOCTYPE, doc.name):
		return
	try:
		frappe.delete_doc(DOCTYPE, doc.name, ignore_permissions=True)
	except frappe.LinkExistsError:
		frappe.db.set_value(DOCTYPE, doc.name, "disabled", 1, update_modified=False)


def follow_rename(doc, method=None, old=None, new=None, merge=False):
	"""Cost Center after_rename: the mirror takes the new name, and every voucher that names it follows."""
	if doc.doctype != COST_CENTER or not old or old == new or not frappe.db.table_exists(DOCTYPE):
		return
	if not frappe.db.exists(DOCTYPE, old):
		return
	if merge or frappe.db.exists(DOCTYPE, new):
		return
	from frappe.model.rename_doc import rename_doc

	rename_doc(DOCTYPE, old, new, force=True, ignore_permissions=True, show_alert=False)
	frappe.db.set_value(DOCTYPE, new, "cost_center", new, update_modified=False)


def backfill() -> int:
	"""Make the mirror of every cost centre that has none, and bring the others up to date. Returns how many it touched."""
	if not frappe.db.table_exists(DOCTYPE):
		return 0
	have = {r.name: r for r in frappe.get_all(DOCTYPE, fields=["name", "cost_center_name", "cc_company", "disabled"], limit_page_length=0)}
	touched = 0
	for cc in frappe.get_all(COST_CENTER, fields=["name", "cost_center_name", "company", "is_group", "disabled"], limit_page_length=0):
		want = _wanted(cc)
		row = have.get(cc.name)
		if not row:
			frappe.get_doc(dict(want, doctype=DOCTYPE)).insert(ignore_permissions=True)
			touched += 1
		elif (row.cost_center_name, row.cc_company, cint(row.disabled)) != (want["cost_center_name"], want["cc_company"], want["disabled"]):
			frappe.db.set_value(DOCTYPE, cc.name, {k: v for k, v in want.items() if k != "cost_center"}, update_modified=False)
			touched += 1
	return touched


# ─── the accounting dimension ──────────────────────────────────────────────────────────────────


def ensure_dimension():
	"""after_migrate: every cost centre mirrored, and the Accounting Dimension that carries the choice, made now
	(ERPNext would make its fields in a worker after the commit; a migrate must leave the site finished)."""
	if not frappe.db.table_exists(DOCTYPE):
		return
	backfill()
	name = frappe.db.get_value("Accounting Dimension", {"document_type": DOCTYPE}, "name")
	if name:
		dimension = frappe.get_doc("Accounting Dimension", name)
	else:
		dimension = frappe.get_doc({"doctype": "Accounting Dimension", "document_type": DOCTYPE, "label": LABEL, "fieldname": FIELD})
		dimension.insert(ignore_permissions=True)
	from erpnext.accounts.doctype.accounting_dimension.accounting_dimension import make_dimension_in_accounting_doctypes

	make_dimension_in_accounting_doctypes(doc=dimension)
	frappe.flags.accounting_dimensions = None
	frappe.flags.accounting_dimensions_details = None
	if frappe.db.has_column("GL Entry", FIELD):
		frappe.db.add_index("GL Entry", [FIELD])
	# the mirror's first version called its company column "company"; the field is gone, the column is not
	if frappe.db.has_column(DOCTYPE, "company"):
		frappe.db.sql_ddl("alter table `tab" + DOCTYPE + "` drop column `company`")


# ─── what a voucher may say ─────────────────────────────────────────────────────────────────────


def places(doc) -> list:
	"""[(label, alternative cost centre)] for the header and every row of the voucher that names one."""
	found = []
	meta = frappe.get_meta(doc.doctype)
	if meta.has_field(FIELD) and cstr(doc.get(FIELD)).strip():
		found.append((None, doc.get(FIELD)))
	for table in meta.get_table_fields():
		if not frappe.get_meta(table.options).has_field(FIELD):
			continue
		for row in doc.get(table.fieldname) or []:
			if cstr(row.get(FIELD)).strip():
				found.append((_("Row %s of %s") % (row.get("idx"), _(table.label or table.fieldname)), row.get(FIELD)))
	return found


def validate_voucher(doc, method=None):
	"""validate (every document, cheaply): an alternative cost centre is an enabled leaf of ANOTHER company."""
	if doc.doctype not in VOUCHERS or not doc.get("company"):
		return
	named = places(doc)
	if not named:
		return
	info = {
		r.name: r
		for r in frappe.get_all(
			DOCTYPE, filters={"name": ["in", sorted({n for _l, n in named})]}, fields=["name", "cc_company", "disabled"], limit_page_length=0
		)
	}
	for where, name in named:
		prefix = (where + ": ") if where else ""
		row = info.get(name)
		if not row:
			frappe.throw(prefix + _("%s is not an Alternative Cost Center.") % name, title=_("Alternative Cost Center"))
		if row.cc_company == doc.company:
			frappe.throw(
				prefix + _("%s belongs to %s, the company of this voucher. An alternative cost centre is one of ANOTHER company: use the ordinary Cost Center for this one.") % (name, doc.company),
				title=_("Alternative Cost Center"),
			)
		if cint(row.disabled):
			frappe.throw(prefix + _("%s is disabled or a group; choose a cost centre that can take costs.") % name, title=_("Alternative Cost Center"))
