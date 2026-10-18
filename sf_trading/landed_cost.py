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

A Landed Cost Voucher that is itself still a draft may name an expense entry that is still a draft too -- the
freight bill and the import it belongs to are often approved together. Its booking is then READ from the
document (`booked_by_draft`: a Journal Entry's debits, a Purchase Invoice's non-stock rows and Total taxes; a
draft Payment Entry is refused), the same ledger, amount and company checks apply to that expected booking, and
the draft is marked as such. The voucher cannot be submitted until every entry it names is submitted and has
booked what was expected (`booked`): the ledger, not the draft, has the last word.

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
from frappe.query_builder.functions import Sum
from frappe.utils import cint, flt

SETTINGS = "SF Trading Settings"
EXPENSE_DOCTYPES = ("Purchase Invoice", "Journal Entry", "Payment Entry")
#: expense entries a DRAFT Landed Cost Voucher may name while they are themselves still drafts: what they are
#: going to book can be read from the document. A Payment Entry cannot (see booked_by_draft)
DRAFT_EXPENSE_DOCTYPES = ("Purchase Invoice", "Journal Entry")
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


def _receipts_by_order_rows(order_rows) -> dict:
	"""{purchase order row: {receipts}} -- live, non-return receipts received against those rows."""
	out = {}
	order_rows = [r for r in set(order_rows or ()) if r]
	if not order_rows:
		return out
	rows = frappe.get_all("Purchase Receipt Item", filters={"purchase_order_item": ["in", order_rows], "docstatus": ["<", 2]},
		fields=["parent", "purchase_order_item"])
	returns = set(frappe.get_all(RECEIPT, filters={"name": ["in", list({r.parent for r in rows}) or [""]], "is_return": 1},
		pluck="name"))
	for r in rows:
		if r.parent not in returns:
			out.setdefault(r.purchase_order_item, set()).add(r.parent)
	return out


def _invoices_by_order_rows(order_rows) -> dict:
	"""{purchase order row: {invoices}} -- live invoices billing those rows straight from the order
	(no receipt on the invoice row)."""
	out = {}
	order_rows = [r for r in set(order_rows or ()) if r]
	if not order_rows:
		return out
	for r in frappe.get_all("Purchase Invoice Item", filters={"parenttype": INVOICE, "po_detail": ["in", order_rows],
			"docstatus": ["<", 2]}, fields=["parent", "po_detail", "purchase_receipt"]):
		if not r.purchase_receipt:
			out.setdefault(r.po_detail, set()).add(r.parent)
	return out


def linked_receipts(invoices) -> dict:
	"""{invoice: {receipts}} for live receipts tied to the invoice in any of the ways ERPNext ties them:
	the receipt the invoice row bills (Purchase Invoice Item.purchase_receipt), the receipt made from
	the invoice (Purchase Receipt Item.purchase_invoice), and a receipt and invoice both made from the
	same Purchase Order row (po_detail / purchase_order_item) -- how core itself bills a receipt
	against an order-made invoice. A debit note is read with the receipts of the invoice it returns."""
	out = {n: set() for n in set(invoices or ()) if n}
	if not out:
		return out
	names = list(out)
	order_rows = {}
	for r in frappe.get_all("Purchase Invoice Item", filters={"parenttype": INVOICE, "parent": ["in", names],
			"docstatus": ["<", 2]}, fields=["parent", "purchase_receipt", "po_detail"]):
		if r.purchase_receipt:
			out[r.parent].add(r.purchase_receipt)
		elif r.po_detail:
			order_rows.setdefault(r.po_detail, set()).add(r.parent)
	for r in frappe.get_all("Purchase Receipt Item", filters={"purchase_invoice": ["in", names], "docstatus": ["<", 2]},
			fields=["parent", "purchase_invoice"]):
		out[r.purchase_invoice].add(r.parent)
	for order_row, receipts in _receipts_by_order_rows(order_rows).items():
		for invoice in order_rows.get(order_row, ()):
			out[invoice] |= receipts
	returns = frappe.get_all(INVOICE, filters={"name": ["in", names], "is_return": 1, "return_against": ["is", "set"]},
		fields=["name", "return_against"])
	if returns:
		# return_against always names an invoice that is not itself a return, so this goes one level
		origin = linked_receipts({r.return_against for r in returns})
		for r in returns:
			out[r.name] |= origin.get(r.return_against, set())
	return out


def linked_invoices(receipts) -> dict:
	"""{receipt: {invoices}}: the same ties as linked_receipts, read from the receipt's side."""
	out = {n: set() for n in set(receipts or ()) if n}
	if not out:
		return out
	names = list(out)
	for r in frappe.get_all("Purchase Invoice Item", filters={"parenttype": INVOICE, "purchase_receipt": ["in", names],
			"docstatus": ["<", 2]}, fields=["parent", "purchase_receipt"]):
		out[r.purchase_receipt].add(r.parent)
	order_rows = {}
	for r in frappe.get_all("Purchase Receipt Item", filters={"parent": ["in", names], "docstatus": ["<", 2]},
			fields=["parent", "purchase_invoice", "purchase_order_item"]):
		if r.purchase_invoice:
			out[r.parent].add(r.purchase_invoice)
		if r.purchase_order_item:
			order_rows.setdefault(r.purchase_order_item, set()).add(r.parent)
	if order_rows:
		returns = set(frappe.get_all(RECEIPT, filters={"name": ["in", names], "is_return": 1}, pluck="name"))
		for order_row, invoices in _invoices_by_order_rows(order_rows).items():
			for receipt in order_rows.get(order_row, ()):
				if receipt not in returns:
					out[receipt] |= invoices
	return out


def receipt_charge_keys(receipts, key) -> dict:
	"""{receipt: {key(row)}} for the live landed-cost charge rows on those receipts."""
	out = {n: set() for n in set(receipts or ()) if n}
	if not out:
		return out
	fields = ["parent", "account_head"]
	if frappe.db.has_column(PURCHASE_CHARGES, ENTRY_FIELD):
		fields += [DOCTYPE_FIELD, ENTRY_FIELD]
	for r in frappe.get_all(PURCHASE_CHARGES, filters={"parenttype": RECEIPT, "parent": ["in", list(out)],
			"docstatus": ["<", 2], "category": ["in", VALUATION_CATEGORIES], "add_deduct_tax": "Add"}, fields=fields):
		out[r.parent].add(key(r))
	return out


def classify(rows, key, look_up_receipts=False) -> list:
	"""How each charge row on a Purchase Receipt / Invoice stands (STOCK, ON_RECEIPT,
	AWAITING_RECEIPT, NOT_INTO_STOCK). `rows` carry parenttype and parent; `key(row)` names the cost
	a row carries (expense entry and account). An invoice row is its receipts' own charge only when
	EVERY receipt tied to the invoice carries a row with the same key: ERPNext decides per invoice
	row, and books the charge itself for a row whose receipt never carried it. `look_up_receipts`
	reads the charges of tied receipts that are not among `rows` (a report limited to a period or an
	account)."""
	invoices = {r.parent for r in rows if r.parenttype == INVOICE}
	stocked = stock_invoices(invoices)
	links = linked_receipts(invoices - stocked)
	carried = {}
	for r in rows:
		if r.parenttype == RECEIPT:
			carried.setdefault(r.parent, set()).add(key(r))
	if look_up_receipts:
		missing = set().union(*links.values()) - set(carried) if links else set()
		for receipt, keys in receipt_charge_keys(missing, key).items():
			carried.setdefault(receipt, set()).update(keys)
	out = []
	for r in rows:
		if r.parenttype != INVOICE or r.parent in stocked:
			out.append(STOCK)
			continue
		receipts = links.get(r.parent) or set()
		if not receipts:
			out.append(AWAITING_RECEIPT)
		elif all(key(r) in carried.get(receipt, ()) for receipt in receipts):
			out.append(ON_RECEIPT)
		else:
			out.append(NOT_INTO_STOCK)
	return out


def credited(invoices, accounts) -> dict:
	"""{(invoice, account): credit} the invoices' own ledger put on those accounts. A non-stock
	invoice's valuation charge reaches the ledger only where ERPNext reverses it (an invoice row with
	a receipt that booked nothing on those accounts), so this, not the row's amount, is what such an
	invoice took."""
	invoices, accounts = [n for n in set(invoices or ()) if n], [a for a in set(accounts or ()) if a]
	if not invoices or not accounts:
		return {}
	gl = frappe.qb.DocType("GL Entry")
	rows = (
		frappe.qb.from_(gl)
		.select(gl.voucher_no, gl.account, Sum(gl.credit - gl.debit).as_("credit"))
		.where((gl.voucher_type == INVOICE) & gl.voucher_no.isin(invoices) & gl.account.isin(accounts)
			& (gl.is_cancelled == 0))
		.groupby(gl.voucher_no, gl.account)
		.run(as_dict=True)
	)
	return {(r.voucher_no, r.account): max(flt(r.credit, 3), 0.0) for r in rows}


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


def booked_by_draft(doctype, name, account=None) -> dict:
	"""{account: net debit} a DRAFT expense entry is going to post, read from the document: nothing is in the
	ledger yet, so `booked` would say it booked nothing.

	A Journal Entry debits its rows' accounts. A Purchase Invoice debits each non-stock row's expense account by
	the row's net amount, and each tax row of category Total / Valuation and Total on its account (Add debits,
	Deduct credits). A Payment Entry is not read ahead: what it books depends on the party and the references it
	settles, so a draft one is refused (see may_read_ahead) -- submit it first.

	This is what the entry is EXPECTED to book. The voucher is held to the ledger itself when it is submitted
	(`booked`), so an entry that changes before it is submitted is caught then, not trusted now.
	"""
	out = {}

	def add(acct, amount):
		if acct and (not account or acct == account) and abs(flt(amount)) > 0:
			out[acct] = flt(out.get(acct, 0) + flt(amount), 3)

	if doctype == "Journal Entry":
		for r in frappe.get_all("Journal Entry Account", filters={"parent": name, "parenttype": doctype},
				fields=["account", "debit", "credit"]):
			add(r.account, flt(r.debit) - flt(r.credit))
	elif doctype == "Purchase Invoice":
		for r in frappe.get_all("Purchase Invoice Item", filters={"parent": name, "parenttype": doctype},
				fields=["item_code", "expense_account", "base_net_amount"]):
			if r.item_code and cint(frappe.get_cached_value("Item", r.item_code, "is_stock_item")):
				continue  # a stock row debits the stock account, not an expense
			add(r.expense_account, r.base_net_amount)
		for t in frappe.get_all("Purchase Taxes and Charges", filters={"parent": name, "parenttype": doctype},
				fields=["account_head", "base_tax_amount", "add_deduct_tax", "category"]):
			if t.category in ("Total", "Valuation and Total"):
				add(t.account_head, flt(t.base_tax_amount) * (-1 if t.add_deduct_tax == "Deduct" else 1))
	return out


def expense_booking(doctype, name, docstatus, account=None) -> dict:
	"""What the expense entry booked (submitted) or is going to book (draft), {account: net debit}."""
	return booked_by_draft(doctype, name, account) if cint(docstatus) == 0 else booked(doctype, name, account)


def may_read_ahead(doc, expense_doctype) -> bool:
	"""Only a Landed Cost Voucher that is itself still a draft may name a draft expense entry, and only a
	kind whose booking can be read from the document. A voucher being submitted, or a charge row on a
	Purchase Invoice / Receipt, needs the entry submitted."""
	return doc.doctype == "Landed Cost Voucher" and cint(doc.get("docstatus")) == 0 and expense_doctype in DRAFT_EXPENSE_DOCTYPES


def draft_expense_entries(doc) -> list:
	"""[(doctype, name)] the document's charge rows name that are still drafts, each once."""
	out = []
	for row, _account, _amount in charge_rows(doc):
		dt, name = row.get(DOCTYPE_FIELD), row.get(ENTRY_FIELD)
		if dt in EXPENSE_DOCTYPES and name and (dt, name) not in out and cint(frappe.db.get_value(dt, name, "docstatus")) == 0:
			out.append((dt, name))
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
		posted = credited({r.parent for r in purchase if r.state == NOT_INTO_STOCK and cint(r.docstatus) == 1}, [account])
		used = {}
		for r in purchase:
			if r.state == NOT_INTO_STOCK and cint(r.docstatus) == 1:
				left = flt(posted.get((r.parent, account), 0.0) - used.get(r.parent, 0.0), 3)
				r.amount = max(min(flt(r.amount), left), 0.0)
				used[r.parent] = flt(used.get(r.parent, 0.0) + r.amount, 3)
		rows += purchase
	skip = {tuple(p) for p in skip_parents or ()}
	for r in rows:
		if exclude_parent and (r.parenttype, r.parent) == tuple(exclude_parent):
			continue
		if (r.parenttype, r.parent) in skip or r.state == ON_RECEIPT or (r.state == NOT_INTO_STOCK and not r.amount):
			continue
		if cint(r.docstatus) == 1 and r.state != AWAITING_RECEIPT:
			result.submitted = flt(result.submitted + flt(r.amount), 3)
		else:
			result.draft = flt(result.draft + flt(r.amount), 3)
		if (r.parenttype, r.parent) not in result.documents:
			result.documents.append((r.parenttype, r.parent))
	return result


@frappe.whitelist()
def get_expense_entry_lines(expense_doctype: str, expense_entry: str, parenttype: str = None, parent: str = None,
		update_stock: int = 0, items=None, return_against: str = None, goods=None) -> list:
	"""The landed-cost lines an expense entry can still give: for the charge-row picker. The form
	sends its own receipt / invoice ties (`items`), so a charge an invoice repeats from its receipt
	shows what the receipt took as still its own, not as gone."""
	if expense_doctype not in EXPENSE_DOCTYPES:
		frappe.throw(_("An expense entry is a Purchase Invoice, Journal Entry or Payment Entry."))
	frappe.has_permission(expense_doctype, "read", doc=expense_entry, throw=True)
	status = cint(frappe.db.get_value(expense_doctype, expense_entry, "docstatus"))
	if status == 2:
		frappe.throw(_("%s %s is cancelled, so it has booked nothing.") % (_(expense_doctype), expense_entry))
	if status == 0 and not (parenttype == "Landed Cost Voucher" and expense_doctype in DRAFT_EXPENSE_DOCTYPES):
		if parenttype == "Landed Cost Voucher":
			frappe.throw(_("A draft Payment Entry has booked nothing and cannot be read ahead. Submit it first, or name the "
				"invoice or journal that booked the cost."))
		frappe.throw(_("%s %s is not submitted, so it has booked nothing yet.") % (_(expense_doctype), expense_entry))
	# the purchase the cost is spread over is not the expense that booked it (validate_charges refuses it too)
	own = {(parenttype, parent)} if parenttype in CHARGE_PARENTS and parent else set()
	for pair in (frappe.parse_json(goods) if isinstance(goods, str) else goods) or []:
		if isinstance(pair, (list, tuple)) and len(pair) == 2:
			own.add((pair[0], pair[1]))
	if (expense_doctype, expense_entry) in own:
		frappe.throw(_("%s %s is the purchase this cost is spread over, not the expense that booked it. Name the freight or "
			"customs bill, journal or payment that booked the cost.") % (_(expense_doctype), expense_entry))
	exclude = (parenttype, parent) if parenttype and parent else None
	ties = frappe._dict(repeats=set(), carried=set(), receipts=set())
	if parenttype in CHARGE_PARENTS:
		rows = frappe.parse_json(items) if isinstance(items, str) else (items or [])
		stub = frappe._dict(doctype=parenttype, name=parent or None, update_stock=cint(update_stock),
			return_against=return_against, is_return=1 if return_against else 0,
			items=[frappe._dict(r) for r in rows if isinstance(r, dict)])
		ties = _receipt_links(stub)
	lines = []
	for account, amount in expense_booking(expense_doctype, expense_entry, status).items():
		if amount <= TOLERANCE:
			continue
		skip = set(ties.repeats)
		if (expense_doctype, expense_entry, account) in ties.carried:
			skip |= {(RECEIPT, n) for n in ties.receipts}
		taken = absorbed(expense_doctype, expense_entry, account, exclude, skip_parents=skip)
		lines.append({"account": account, "booked": amount, "absorbed": flt(taken.submitted + taken.draft, 3),
			"available": flt(amount - taken.submitted - taken.draft, 3),
			"documents": [d[1] for d in taken.documents], "draft": 1 if status == 0 else 0})
	if status == 0 and not lines:
		frappe.throw(_("%s %s is a draft and is not going to book an expense a landed cost can absorb: only its non-stock rows "
			"(Purchase Invoice) or its debits (Journal Entry) count, and stock rows go to the stock account.")
			% (_(expense_doctype), expense_entry))
	return lines


@frappe.whitelist()
@frappe.validate_and_sanitize_search_inputs
def expense_entry_query(doctype, txt, searchfield, start, page_len, filters):
	"""Link query for the Expense Entry field: submitted entries of the company, newest first. A draft
	Landed Cost Voucher (allow_draft) is also offered the invoices and journals still in draft, marked as
	such: it may be drawn up before the bill that booked its cost has been approved."""
	if doctype not in EXPENSE_DOCTYPES:
		return []
	filters = frappe._dict(filters or {})
	drafts = cint(filters.get("allow_draft")) and doctype in DRAFT_EXPENSE_DOCTYPES
	conditions = {"docstatus": ["in", [0, 1]] if drafts else 1}
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
	rows = frappe.get_list(doctype, filters=conditions, fields=fields + ["docstatus"], order_by="posting_date desc",
		limit_start=start, limit_page_length=page_len)
	out = []
	for r in rows:
		values = [str(r.get(f) or "")[:60] for f in fields]
		out.append(values[:1] + [_("Draft")] + values[1:] if cint(r.get("docstatus")) == 0 else values)
	return out


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


def _charge_key(r):
	return (r.get(DOCTYPE_FIELD), r.get(ENTRY_FIELD), r.get("account_head"))


def _receipt_links(doc) -> frappe._dict:
	"""How the document in hand (saved or not) ties to receipts and invoices, read from its own
	rows as well as the database: {repeats, carried, receipts}.

	A receipt: `repeats` are the non-stock invoices tied to it -- they carry copies of its rows and
	are skipped when counting what is already taken. A non-stock invoice: `receipts` are the ones
	tied to it, and `carried` the costs EVERY one of them already carries -- its own copies of those
	are the receipts' charges and take nothing more (classify applies the same rule)."""
	out = frappe._dict(repeats=set(), carried=set(), receipts=set())
	items = doc.get("items") or []
	saved = not _is_new(doc) and doc.get("name")
	if doc.doctype == RECEIPT:
		invoices = {it.get("purchase_invoice") for it in items if it.get("purchase_invoice")}
		order_rows = {it.get("purchase_order_item") for it in items if it.get("purchase_order_item")}
		if order_rows and not cint(doc.get("is_return")):
			for found in _invoices_by_order_rows(order_rows).values():
				invoices |= found
		if saved:
			invoices |= linked_invoices([doc.name]).get(doc.name, set())
		invoices -= stock_invoices(invoices)
		out.repeats = {(INVOICE, n) for n in invoices}
		return out
	if doc.doctype == INVOICE and not cint(doc.get("update_stock")):
		receipts = {it.get("purchase_receipt") for it in items if it.get("purchase_receipt")}
		order_rows = {it.get("po_detail") for it in items if it.get("po_detail") and not it.get("purchase_receipt")}
		for found in _receipts_by_order_rows(order_rows).values():
			receipts |= found
		if saved:
			receipts |= linked_receipts([doc.name]).get(doc.name, set())
		if cint(doc.get("is_return")) and doc.get("return_against"):
			receipts |= linked_receipts([doc.return_against]).get(doc.return_against, set())
		out.receipts = receipts
		keys = list(receipt_charge_keys(receipts, _charge_key).values())
		out.carried = set.intersection(*keys) if keys else set()
		return out
	return out


def validate_charges(doc, method=None):
	"""validate: every charge row naming an expense entry is held to it (ledger, amount, transaction)."""
	goods = _goods_documents(doc)
	ties = _receipt_links(doc)
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
		status = cint(values.docstatus)
		if status == 2:
			frappe.throw(label + ": " + _("%s %s is cancelled, so it has booked nothing.") % (_(expense_dt), frappe.bold(expense)))
		if status != 1 and not (status == 0 and may_read_ahead(doc, expense_dt)):
			if status == 0 and doc.doctype == "Landed Cost Voucher" and cint(doc.get("docstatus")) == 0:
				frappe.throw(label + ": " + _("A draft Payment Entry has booked nothing and cannot be read ahead. Submit %s "
					"first, or name the invoice or journal that booked the cost.") % frappe.bold(expense))
			frappe.throw(label + ": " + _("%s %s is not submitted, so it has booked nothing yet.") % (_(expense_dt), frappe.bold(expense)))
		if values.company != doc.company:
			frappe.throw(label + ": " + _("{0} belongs to {1}, not {2}.").format(frappe.bold(expense), values.company,
				doc.company))
		if (expense_dt, expense) in goods:
			frappe.throw(label + ": " + _("{0} is the purchase this cost is spread over, not the expense that booked it.")
				.format(frappe.bold(expense)))

		# the ledger: the expense entry must have debited this very account
		# (a draft expense entry has no ledger yet: what it is going to book is read from the document)
		on_account = expense_booking(expense_dt, expense, status, account).get(account, 0.0)
		if on_account <= TOLERANCE:
			elsewhere = [a for a, v in expense_booking(expense_dt, expense, status).items() if v > TOLERANCE]
			if status == 0:
				message = _("%s is still a draft and would book nothing to %s. It would debit: %s. Use the account the expense is "
					"going to be booked on.")
			else:
				message = _("%s booked nothing to %s. It debited: %s. Use the account the expense was booked on.")
			frappe.throw(
				label + ": " + message % (frappe.bold(expense), frappe.bold(account), ", ".join(elsewhere) or _("no expense account")),
				title=_("Expense Ledger Mismatch"),
			)

		# the amount: never more than the entry booked there, across every charge naming it
		key = (expense_dt, expense, account)
		if key in ties.carried:
			# this invoice repeats its receipts' own charge: the receipts already took it
			continue
		taken = absorbed(expense_dt, expense, account, exclude_parent=(doc.doctype, doc.name),
			skip_parents=ties.repeats)
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
