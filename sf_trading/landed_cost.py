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


def absorbed(doctype, name, account, exclude_parent=None) -> frappe._dict:
	"""What the landed-cost charges naming this expense entry take from it on one account.

	Every live charge row counts -- submitted and draft apart -- so two drafts cannot both take the
	same money.
	"""
	result = frappe._dict(submitted=0.0, draft=0.0, documents=[])
	if not frappe.db.has_column(LCV_CHARGES, ENTRY_FIELD):
		return result
	rows = [
		frappe._dict(r, amount=r.base_amount)
		for r in frappe.get_all(
			LCV_CHARGES,
			filters={DOCTYPE_FIELD: doctype, ENTRY_FIELD: name, "expense_account": account, "docstatus": ["<", 2],
				"parenttype": "Landed Cost Voucher"},
			fields=["parent", "parenttype", "docstatus", "base_amount"],
		)
	]
	if frappe.db.has_column(PURCHASE_CHARGES, ENTRY_FIELD):
		rows += [
			frappe._dict(r, amount=r.base_tax_amount)
			for r in frappe.get_all(
				PURCHASE_CHARGES,
				filters={DOCTYPE_FIELD: doctype, ENTRY_FIELD: name, "account_head": account, "docstatus": ["<", 2],
					"parenttype": ["in", CHARGE_PARENTS], "category": ["in", VALUATION_CATEGORIES],
					"add_deduct_tax": "Add"},
				fields=["parent", "parenttype", "docstatus", "base_tax_amount"],
			)
		]
	for r in rows:
		if exclude_parent and (r.parenttype, r.parent) == tuple(exclude_parent):
			continue
		if cint(r.docstatus) == 1:
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


def validate_charges(doc, method=None):
	"""validate: every charge row naming an expense entry is held to it (ledger, amount, transaction)."""
	goods = _goods_documents(doc)
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
		taken = absorbed(expense_dt, expense, account, exclude_parent=(doc.doctype, doc.name))
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
