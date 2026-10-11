# sf_trading/landed_cost.py
"""Landed costs tied to the expense entry that booked them.

A landed cost is booked twice over in ERPNext: once when the expense is entered -- a freight
agent's Purchase Invoice, a customs Journal Entry, a Payment Entry -- debiting a landed-cost
account, and again when it is put into the stock it belongs to, crediting that same account: a
Landed Cost Voucher charge row, or a "Valuation" charge on the goods' own Purchase Invoice /
Purchase Receipt. Nothing in core says which charge absorbed which expense, so an expense can be
absorbed twice, or never, and nobody can tell.

Every such charge row now names its Expense Entry (`custom_expense_doctype` /
`custom_expense_entry`), and the row is held to it:

* the ledger -- the expense entry must have debited the charge row's own account;
* the amount -- all the charges naming one expense entry, on one account, may not absorb more than
  that entry booked there (drafts count, so two drafts cannot both take the same money);
* the transaction -- the expense entry is submitted, in the same company, and is not one of the
  very documents the charge is spread over.

Whether a charge row MUST name one is SF Trading Settings > Landed Costs. The Landed Cost
Reconciliation report lists what is still unallocated, pending or unmatched on either side. An
expense entry that debits a landed-cost account for something else (outward freight, say) can be
ticked "Not a Landed Cost" and drops off that report.

Only a document that moves stock absorbs a cost: a Purchase Receipt, or a Purchase Invoice with
Update Stock (a Landed Cost Voucher works through those). A Purchase Invoice that does not update
stock is read together with the receipt it bills, or that was made from it (`classify`): ERPNext
copies the receipt's charge rows onto that invoice, and posts nothing for one the receipt already
booked, so such a row is the receipt's own charge seen again -- counting it too absorbed the same
expense twice and refused the invoice. An invoice whose receipt does not exist yet holds its charge
as pending; one whose receipt never carried the charge is flagged "Not Into Stock" (ERPNext books it
to Stock Received But Not Billed, not into the stock's value).
"""

import frappe
from frappe import _
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields
from frappe.utils import cint, flt

SETTINGS = "SF Trading Settings"
EXPENSE_DOCTYPES = ("Purchase Invoice", "Journal Entry", "Payment Entry")
DOCTYPE_FIELD = "custom_expense_doctype"
ENTRY_FIELD = "custom_expense_entry"
EXCLUDE_FIELD = "custom_not_landed_cost"
VALUATION_CATEGORIES = ("Valuation", "Valuation and Total")
CHARGE_PARENTS = ("Purchase Invoice", "Purchase Receipt")
LCV_CHARGES = "Landed Cost Taxes and Charges"
PURCHASE_CHARGES = "Purchase Taxes and Charges"
TOLERANCE = 0.0005
RECEIPT, INVOICE = "Purchase Receipt", "Purchase Invoice"

#: how a charge row on a Purchase Receipt / Invoice stands once the receipt and the invoices linked
#: to it are read as one purchase (see classify)
STOCK = "stock"  # on a receipt, or an invoice that updates stock: it absorbs the cost
ON_RECEIPT = "on_receipt"  # an invoice repeating the charge its receipt carries: not counted again
AWAITING_RECEIPT = "awaiting_receipt"  # an invoice whose receipt does not exist yet: held as pending
NOT_INTO_STOCK = "not_into_stock"  # an invoice whose receipt never carried it: booked, but not into stock


def ensure_custom_fields():
	"""after_migrate: the expense-entry link on both kinds of charge row, and the opt-out flag."""
	kind = {
		"fieldname": DOCTYPE_FIELD,
		"label": "Expense Entry Type",
		"fieldtype": "Select",
		"options": "\n" + "\n".join(EXPENSE_DOCTYPES),
	}
	entry = {
		"fieldname": ENTRY_FIELD,
		"label": "Expense Entry",
		"fieldtype": "Dynamic Link",
		"options": DOCTYPE_FIELD,
		"insert_after": DOCTYPE_FIELD,
		"description": "The invoice, journal or payment that booked this cost.",
	}
	valuation = "eval:['Valuation','Valuation and Total'].includes(doc.category)"
	exclude = {
		"fieldname": EXCLUDE_FIELD,
		"label": "Not a Landed Cost",
		"fieldtype": "Check",
		"allow_on_submit": 1,
		"no_copy": 1,
		"description": "Tick when this entry debits a landed-cost account for something that never goes into "
			"stock (outward freight, say). It then drops off the Landed Cost Reconciliation.",
	}
	create_custom_fields(
		{
			LCV_CHARGES: [dict(kind, insert_after="expense_account"), dict(entry, in_list_view=1, columns=2)],
			PURCHASE_CHARGES: [dict(kind, insert_after="account_head", depends_on=valuation),
				dict(entry, depends_on=valuation)],
			"Journal Entry": [dict(exclude, insert_after="voucher_type")],
			"Payment Entry": [dict(exclude, insert_after="mode_of_payment")],
			"Purchase Invoice": [dict(exclude, insert_after="supplier")],
		},
		ignore_validate=True,
		update=True,
	)


def settings() -> frappe._dict:
	values = frappe._dict(frappe.db.get_singles_dict(SETTINGS) or {})
	on_lcv = values.get("landed_cost_require_on_lcv")
	values.require_on_lcv = 1 if on_lcv in (None, "") else cint(on_lcv)
	values.require_on_purchase = cint(values.get("landed_cost_require_on_purchase"))
	return values


# ─── which documents move stock, and how a receipt and its invoices connect ────────────────────


def stock_invoices(names) -> set:
	"""The Purchase Invoices among `names` that update stock themselves."""
	names = [n for n in set(names or ()) if n]
	if not names:
		return set()
	return set(frappe.get_all(INVOICE, filters={"name": ["in", names], "update_stock": 1}, pluck="name"))


def linked_receipts(invoices) -> dict:
	"""{invoice: {receipts}}: the receipts an invoice bills (its rows' Purchase Receipt) and the ones
	made from it (their rows' Purchase Invoice). Cancelled documents are left out."""
	out = {n: set() for n in set(invoices or ()) if n}
	if not out:
		return out
	names = list(out)
	for r in frappe.get_all("Purchase Invoice Item", filters={"parenttype": INVOICE, "parent": ["in", names],
			"purchase_receipt": ["is", "set"], "docstatus": ["<", 2]}, fields=["parent", "purchase_receipt"]):
		out[r.parent].add(r.purchase_receipt)
	for r in frappe.get_all("Purchase Receipt Item", filters={"purchase_invoice": ["in", names], "docstatus": ["<", 2]},
			fields=["parent", "purchase_invoice"]):
		out[r.purchase_invoice].add(r.parent)
	return out


def linked_invoices(receipts) -> dict:
	"""{receipt: {invoices}}: the same links as linked_receipts, read from the receipt's side."""
	out = {n: set() for n in set(receipts or ()) if n}
	if not out:
		return out
	names = list(out)
	for r in frappe.get_all("Purchase Invoice Item", filters={"parenttype": INVOICE, "purchase_receipt": ["in", names],
			"docstatus": ["<", 2]}, fields=["parent", "purchase_receipt"]):
		out[r.purchase_receipt].add(r.parent)
	for r in frappe.get_all("Purchase Receipt Item", filters={"parent": ["in", names], "purchase_invoice": ["is", "set"],
			"docstatus": ["<", 2]}, fields=["parent", "purchase_invoice"]):
		out[r.parent].add(r.purchase_invoice)
	return out


def classify(rows, key, look_up_receipts=False) -> list:
	"""How each charge row on a Purchase Receipt / Invoice stands (STOCK, ON_RECEIPT,
	AWAITING_RECEIPT, NOT_INTO_STOCK). `rows` carry parenttype and parent; `key(row)` names the cost
	a row carries (expense entry and account), so an invoice row is the receipt's own charge when a
	linked receipt carries a row with the same key. `look_up_receipts` reads the charges of linked
	receipts that are not among `rows` (a report limited to a period or an account)."""
	invoices = {r.parent for r in rows if r.parenttype == INVOICE}
	stocked = stock_invoices(invoices)
	links = linked_receipts(invoices - stocked)
	on_receipts = {(r.parent, key(r)) for r in rows if r.parenttype == RECEIPT}
	if look_up_receipts:
		missing = set().union(*links.values()) - {r.parent for r in rows if r.parenttype == RECEIPT} if links else set()
		if missing:
			fields = ["parent", "account_head"]
			if frappe.db.has_column(PURCHASE_CHARGES, ENTRY_FIELD):
				fields += [DOCTYPE_FIELD, ENTRY_FIELD]
			for r in frappe.get_all(PURCHASE_CHARGES, filters={"parenttype": RECEIPT, "parent": ["in", list(missing)],
					"docstatus": ["<", 2], "category": ["in", VALUATION_CATEGORIES], "add_deduct_tax": "Add"},
					fields=fields):
				on_receipts.add((r.parent, key(r)))
	out = []
	for r in rows:
		if r.parenttype != INVOICE or r.parent in stocked:
			out.append(STOCK)
			continue
		receipts = links.get(r.parent) or set()
		if any((receipt, key(r)) in on_receipts for receipt in receipts):
			out.append(ON_RECEIPT)
		elif not receipts:
			out.append(AWAITING_RECEIPT)
		else:
			out.append(NOT_INTO_STOCK)
	return out


# ─── what an expense entry booked, and what has absorbed it ─────────────────────────────────────


def booked(doctype, name, account=None) -> dict:
	"""{account: net debit} the expense entry posted to the ledger (positive = booked as expense)."""
	filters = {"voucher_type": doctype, "voucher_no": name, "is_cancelled": 0}
	if account:
		filters["account"] = account
	out = {}
	for row in frappe.get_all("GL Entry", filters=filters, fields=["account", "debit", "credit"]):
		out[row.account] = flt(out.get(row.account, 0) + flt(row.debit) - flt(row.credit), 3)
	return out


def absorbed(doctype, name, account, exclude_parent=None, skip_parents=()) -> frappe._dict:
	"""What the landed-cost charges naming this expense entry take from it on one account.

	Every live charge row counts -- submitted and draft apart -- so two drafts cannot both take the
	same money. An invoice repeating its receipt's charge is not counted again, and one whose
	receipt is not made yet counts as pending (classify). `skip_parents` are documents the caller
	knows to repeat its own charge (the invoices of a receipt being saved).
	"""
	result = frappe._dict(submitted=0.0, draft=0.0, documents=[])
	if not frappe.db.has_column(LCV_CHARGES, ENTRY_FIELD):
		return result
	rows = [
		frappe._dict(r, amount=r.base_amount, state=STOCK)
		for r in frappe.get_all(
			LCV_CHARGES,
			filters={DOCTYPE_FIELD: doctype, ENTRY_FIELD: name, "expense_account": account, "docstatus": ["<", 2],
				"parenttype": "Landed Cost Voucher"},
			fields=["parent", "parenttype", "docstatus", "base_amount"],
		)
	]
	if frappe.db.has_column(PURCHASE_CHARGES, ENTRY_FIELD):
		purchase = [
			frappe._dict(r, amount=r.base_tax_amount)
			for r in frappe.get_all(
				PURCHASE_CHARGES,
				filters={DOCTYPE_FIELD: doctype, ENTRY_FIELD: name, "account_head": account, "docstatus": ["<", 2],
					"parenttype": ["in", CHARGE_PARENTS], "category": ["in", VALUATION_CATEGORIES],
					"add_deduct_tax": "Add"},
				fields=["parent", "parenttype", "docstatus", "base_tax_amount"],
			)
		]
		# every row here carries this very entry and account, so they all share one key
		for r, state in zip(purchase, classify(purchase, key=lambda _r: 1)):
			r.state = state
		rows += purchase
	skip = {tuple(p) for p in skip_parents or ()}
	for r in rows:
		if exclude_parent and (r.parenttype, r.parent) == tuple(exclude_parent):
			continue
		if (r.parenttype, r.parent) in skip or r.state == ON_RECEIPT:
			continue
		if cint(r.docstatus) == 1 and r.state != AWAITING_RECEIPT:
			result.submitted = flt(result.submitted + flt(r.amount), 3)
		else:
			result.draft = flt(result.draft + flt(r.amount), 3)
		if (r.parenttype, r.parent) not in result.documents:
			result.documents.append((r.parenttype, r.parent))
	return result


@frappe.whitelist()
def get_expense_entry_lines(expense_doctype: str, expense_entry: str, parenttype: str = None, parent: str = None) -> list:
	"""The landed-cost lines an expense entry can still give: for the charge-row picker."""
	if expense_doctype not in EXPENSE_DOCTYPES:
		frappe.throw(_("An expense entry is a Purchase Invoice, Journal Entry or Payment Entry."))
	frappe.has_permission(expense_doctype, "read", doc=expense_entry, throw=True)
	exclude = (parenttype, parent) if parenttype and parent else None
	lines = []
	for account, amount in booked(expense_doctype, expense_entry).items():
		if amount <= TOLERANCE:
			continue
		taken = absorbed(expense_doctype, expense_entry, account, exclude)
		lines.append({"account": account, "booked": amount, "absorbed": flt(taken.submitted + taken.draft, 3),
			"available": flt(amount - taken.submitted - taken.draft, 3),
			"documents": [d[1] for d in taken.documents]})
	return lines


@frappe.whitelist()
@frappe.validate_and_sanitize_search_inputs
def expense_entry_query(doctype, txt, searchfield, start, page_len, filters):
	"""Link query for the Expense Entry field: submitted entries of the company, newest first."""
	if doctype not in EXPENSE_DOCTYPES:
		return []
	filters = frappe._dict(filters or {})
	conditions = {"docstatus": 1}
	if filters.get("company"):
		conditions["company"] = filters.company
	if txt:
		conditions["name"] = ["like", "%" + txt + "%"]
	fields = ["name", "posting_date"]
	if doctype == "Purchase Invoice":
		fields += ["supplier_name", "base_grand_total"]
	elif doctype == "Payment Entry":
		fields += ["party_name", "base_paid_amount"]
	else:
		fields += ["user_remark", "total_debit"]
	rows = frappe.get_list(doctype, filters=conditions, fields=fields, order_by="posting_date desc",
		limit_start=start, limit_page_length=page_len)
	return [[str(r.get(f) or "")[:60] for f in fields] for r in rows]


# ─── the controls ────────────────────────────────────────────────────────────────────────────────


def charge_rows(doc):
	"""(row, account, amount) for every landed-cost charge row on the document."""
	if doc.doctype == "Landed Cost Voucher":
		return [(row, row.expense_account, flt(row.base_amount) or flt(row.amount)) for row in doc.get("taxes") or []]
	return [
		(row, row.account_head, flt(row.base_tax_amount) or flt(row.tax_amount))
		for row in doc.get("taxes") or []
		if row.category in VALUATION_CATEGORIES and (row.add_deduct_tax or "Add") == "Add"
	]


def _goods_documents(doc):
	if doc.doctype == "Landed Cost Voucher":
		return {(r.receipt_document_type, r.receipt_document) for r in doc.get("purchase_receipts") or []}
	return {(doc.doctype, doc.name)}


def _row_label(row):
	return _("Row #") + str(row.idx)


def _is_new(doc) -> bool:
	getter = getattr(doc, "is_new", None)
	return bool(getter()) if callable(getter) else not doc.get("name")


def _receipt_links(doc) -> tuple:
	"""(documents repeating this one's charges, costs this one repeats from its receipts).

	A receipt: the non-stock invoices linked to it carry copies of its rows -- they are skipped when
	counting what is already taken. A non-stock invoice: the costs its linked receipts already carry
	are those receipts' charges, so its own copies of them take nothing more."""
	items = doc.get("items") or []
	saved = not _is_new(doc) and doc.get("name")
	if doc.doctype == RECEIPT:
		invoices = {it.get("purchase_invoice") for it in items if it.get("purchase_invoice")}
		if saved:
			invoices |= linked_invoices([doc.name]).get(doc.name, set())
		invoices -= stock_invoices(invoices)
		return {(INVOICE, n) for n in invoices}, set()
	if doc.doctype == INVOICE and not cint(doc.get("update_stock")):
		receipts = {it.get("purchase_receipt") for it in items if it.get("purchase_receipt")}
		if saved:
			receipts |= linked_receipts([doc.name]).get(doc.name, set())
		carried = set()
		if receipts and frappe.db.has_column(PURCHASE_CHARGES, ENTRY_FIELD):
			for r in frappe.get_all(PURCHASE_CHARGES, filters={"parenttype": RECEIPT, "parent": ["in", list(receipts)],
					"docstatus": ["<", 2], "category": ["in", VALUATION_CATEGORIES], "add_deduct_tax": "Add"},
					fields=[DOCTYPE_FIELD, ENTRY_FIELD, "account_head"]):
				carried.add((r.get(DOCTYPE_FIELD), r.get(ENTRY_FIELD), r.account_head))
		return set(), carried
	return set(), set()


def validate_charges(doc, method=None):
	"""validate: every charge row naming an expense entry is held to it (ledger, amount, transaction)."""
	goods = _goods_documents(doc)
	repeats_this, carried = _receipt_links(doc)
	taken_here = {}
	for row, account, amount in charge_rows(doc):
		expense_dt, expense = row.get(DOCTYPE_FIELD), row.get(ENTRY_FIELD)
		if not expense:
			if expense_dt:
				row.set(DOCTYPE_FIELD, None)
			continue
		label = _row_label(row)
		if expense_dt not in EXPENSE_DOCTYPES:
			frappe.throw(label + ": " + _("choose the Expense Entry Type (Purchase Invoice, Journal Entry or Payment Entry)."))
		values = frappe.db.get_value(expense_dt, expense, ["docstatus", "company"], as_dict=True)
		if not values:
			frappe.throw(label + ": " + _("{0} {1} does not exist.").format(_(expense_dt), frappe.bold(expense)))
		if cint(values.docstatus) != 1:
			frappe.throw(label + ": " + _("{0} {1} is not submitted, so it has booked nothing yet.").format(
				_(expense_dt), frappe.bold(expense)))
		if values.company != doc.company:
			frappe.throw(label + ": " + _("{0} belongs to {1}, not {2}.").format(frappe.bold(expense), values.company,
				doc.company))
		if (expense_dt, expense) in goods:
			frappe.throw(label + ": " + _("{0} is the purchase this cost is spread over, not the expense that booked it.")
				.format(frappe.bold(expense)))

		# the ledger: the expense entry must have debited this very account
		on_account = booked(expense_dt, expense, account).get(account, 0.0)
		if on_account <= TOLERANCE:
			elsewhere = [a for a, v in booked(expense_dt, expense).items() if v > TOLERANCE]
			frappe.throw(
				label + ": " + _("{0} booked nothing to {1}. It debited: {2}. Use the account the expense was booked on.")
				.format(frappe.bold(expense), frappe.bold(account), ", ".join(elsewhere) or _("no expense account")),
				title=_("Expense Ledger Mismatch"),
			)

		# the amount: never more than the entry booked there, across every charge naming it
		key = (expense_dt, expense, account)
		if key in carried:
			# this invoice repeats its receipt's own charge: the receipt already took it
			continue
		taken = absorbed(expense_dt, expense, account, exclude_parent=(doc.doctype, doc.name),
			skip_parents=repeats_this)
		available = flt(on_account - taken.submitted - taken.draft - taken_here.get(key, 0.0), 3)
		if amount - available > TOLERANCE:
			others = ", ".join(d[1] for d in taken.documents)
			frappe.throw(
				label + ": " + _("{0} booked {1} to {2}; {3} of it is already taken{4}, so at most {5} is left for this charge.")
				.format(frappe.bold(expense), on_account, account, flt(taken.submitted + taken.draft + taken_here.get(key, 0.0), 3),
					(" (" + others + ")") if others else "", max(available, 0)),
				title=_("More Than the Expense"),
			)
		taken_here[key] = flt(taken_here.get(key, 0.0) + amount, 3)


def require_links(doc, method=None):
	"""before_submit: charge rows name their expense entry, where the settings demand it."""
	if doc.doctype == INVOICE and not cint(doc.get("update_stock")):
		# its charges reach stock through its receipt (or a Landed Cost Voucher), held to it there
		return
	conf = settings()
	required = conf.require_on_lcv if doc.doctype == "Landed Cost Voucher" else conf.require_on_purchase
	if not required:
		return
	missing = [str(row.idx) for row, _account, amount in charge_rows(doc) if amount and not row.get(ENTRY_FIELD)]
	if missing:
		frappe.throw(
			_("Name the Expense Entry each landed cost was booked on (charge rows {0}).").format(", ".join(missing)),
			title=_("Expense Entry Required"),
		)
