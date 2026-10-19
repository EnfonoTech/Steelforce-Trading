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

What is checked, and where. The alternative cost centre must belong to a company other than the document's
own, be enabled and not be a group: charging a cost centre of the same company is what the ordinary cost
centre is for, and a group cannot be a place costs land. This is checked on EVERY document type that carries
the field in its header or in a table (`carriers`: the vouchers, the orders and quotations that flow into them,
the tax tables and their templates, the POS profile and the other defaults), not on a list kept by hand, so a
document type ERPNext adds later is covered at the next migrate. The ledger tables themselves (GL Entry and the
like) are filled by posting and are not checked again row by row.

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

#: the ledgers: ERPNext writes the value into them when a voucher posts; they are not entered, so not checked
LEDGERS = ("GL Entry", "Payment Ledger Entry", "Account Closing Balance")
CARRIERS_KEY = "sf_alternative_cost_center_carriers"


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
	frappe.cache.delete_value(CARRIERS_KEY)
	frappe.local.sf_alternative_cost_center_carriers = None
	if frappe.db.has_column("GL Entry", FIELD):
		frappe.db.add_index("GL Entry", [FIELD])
	# the mirror's first version called its company column "company"; the field is gone, the column is not
	if frappe.db.has_column(DOCTYPE, "company"):
		frappe.db.sql_ddl("alter table `tab" + DOCTYPE + "` drop column `company`")


# ─── which document types carry it ─────────────────────────────────────────────────────────────


def _parents_of(children) -> set:
	"""The document types that hold one of these child doctypes in a table (standard and custom table fields)."""
	if not children:
		return set()
	found = set()
	for doctype, parent_field in (("DocField", "parent"), ("Custom Field", "dt")):
		rows = frappe.get_all(
			doctype,
			filters={"fieldtype": ["in", ["Table", "Table MultiSelect"]], "options": ["in", sorted(children)]},
			fields=[parent_field],
			limit_page_length=0,
		)
		found |= {r.get(parent_field) for r in rows}
	return found


def _carriers() -> list:
	"""Every entered document type that has the field in its header or in a row of a table, ledgers left out."""
	have = set(frappe.get_all("Custom Field", filters={"fieldname": FIELD}, pluck="dt", limit_page_length=0))
	if not have:
		return []
	info = {r.name: cint(r.istable) for r in frappe.get_all("DocType", filters={"name": ["in", sorted(have)]}, fields=["name", "istable"], limit_page_length=0)}
	children = {dt for dt in have if info.get(dt)}
	headers = {dt for dt in have if dt in info and not info[dt]}
	return sorted((headers | _parents_of(children)) - set(LEDGERS))


def carriers() -> list:
	"""The document types the field is checked on and offered on, cached until the next clear-cache."""
	known = getattr(frappe.local, "sf_alternative_cost_center_carriers", None)
	if known is not None:
		return known
	value = frappe.cache.get_value(CARRIERS_KEY)
	if value is None:
		value = _carriers()
		frappe.cache.set_value(CARRIERS_KEY, value)
	frappe.local.sf_alternative_cost_center_carriers = value
	return value


def boot_carriers(bootinfo):
	"""boot_session: tell the desk which forms carry the field, so its picker is filtered on every one of them."""
	if frappe.db.table_exists(DOCTYPE):
		bootinfo["sf_alt_cc_doctypes"] = carriers()


# ─── what a document may say ────────────────────────────────────────────────────────────────────


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
	"""validate (every document, cheaply): an alternative cost centre is an enabled leaf of ANOTHER company.
	A document type that does not carry the field costs one lookup in a cached list and nothing else."""
	if doc.doctype not in carriers() or not doc.get("company"):
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
