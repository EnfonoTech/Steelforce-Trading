# sf_trading/pdc_transfer.py
"""Post-dated cheques, received and issued: banked in part or in full, or returned unpaid.

A cheque is a Payment Entry whose Mode of Payment carries ZATCA payment means code 20. It moves
the money into (or out of) a holding account -- the cheque mode's own account, "PDC Account" on
this site -- and the cheque then lives until that holding balance is gone:

* **Received from a customer** (`Receive`): the holding account is `paid_to`. The bank crediting
  it is an Internal Transfer *out* of the holding account into the bank.
* **Issued to a supplier** (`Pay`): the holding account is `paid_from`. The bank paying it is an
  Internal Transfer *from* the bank *into* the holding account.

**Partial clearing.** A cheque can be banked in several transfers -- each transfer names the cheque
in `custom_pdc_source_payment_entry`, and together they may not move more than the cheque is
worth. Cleared Amount is their total; the cheque reads Partly Cleared until it is all banked, and
its `clearance_date` is stamped only then.

**Return (bounced cheque).** Whatever is still in the holding account when a cheque comes back
unpaid is reversed by a Journal Entry dated the return date, which names the cheque:

    received:  Dr the customer   Cr the holding account     -> the customer owes it again
    issued:    Dr the holding    Cr the supplier            -> we owe the supplier again

The party side is split over the documents the cheque paid, so their outstanding comes back
(an advance first, then the invoices it settled, most recent first): ERPNext checks only the side
of a reference that REDUCES an outstanding, so a debit against a customer's invoice (or a credit
against a supplier's bill) passes and reopens it. Cancelling that Journal Entry undoes the return.

The cheque's own position -- Cleared Amount, Returned Amount, PDC Status -- is recomputed from its
transfers and returns every time one of them is submitted or cancelled, so it can never drift
from the documents that make it up.
"""

import frappe
from frappe import _
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields
from frappe.utils import cint, cstr, flt, getdate, nowdate

# ZATCA payment-means code for a cheque, carried on Mode of Payment by
# `custom_zatca_payment_means_code`. The PDC Report reads the same code.
CHEQUE_CODE = "20"
SOURCE_FIELD = "custom_pdc_source_payment_entry"
REJECTION_FIELD = "custom_pdc_rejection_date"
CLEARED_FIELD = "custom_pdc_cleared_amount"
RETURNED_FIELD = "custom_pdc_returned_amount"
STATUS_FIELD = "custom_pdc_status"
RETURN_AMOUNT_FIELD = "custom_pdc_return_amount"

PENDING = "Pending"
PARTLY_CLEARED = "Partly Cleared"
CLEARED = "Cleared"
RETURNED = "Returned"
PARTLY_RETURNED = "Partly Returned"
STATUSES = (PENDING, PARTLY_CLEARED, CLEARED, RETURNED, PARTLY_RETURNED)

TOLERANCE = 0.0005
CHEQUE_DEPENDS = "eval:doc.payment_type!='Internal Transfer' && doc.mode_of_payment"


# ─── setup ───────────────────────────────────────────────────────────────────────────────────────


def ensure_custom_fields():
	"""after_migrate: the cheque's link from its transfers and returns, and its position fields."""
	position = {"allow_on_submit": 1, "read_only": 1, "no_copy": 1, "depends_on": CHEQUE_DEPENDS}
	create_custom_fields(
		{
			"Payment Entry": [
				{
					"fieldname": SOURCE_FIELD,
					"label": "PDC Payment Entry",
					"fieldtype": "Link",
					"options": "Payment Entry",
					"insert_after": "reference_date",
					"depends_on": 'eval:doc.payment_type=="Internal Transfer"',
					"description": (
						"The post-dated cheque this transfer banks, in full or in part. Filled in by the "
						"PDC actions on the cheque, or picked here on a transfer you build yourself."
					),
				},
				dict(position, fieldname=STATUS_FIELD, label="PDC Status", fieldtype="Select",
					options="\n" + "\n".join(STATUSES), insert_after=SOURCE_FIELD, in_standard_filter=1),
				dict(position, fieldname=CLEARED_FIELD, label="PDC Cleared Amount", fieldtype="Currency",
					options="paid_to_account_currency", insert_after=STATUS_FIELD),
				dict(position, fieldname=RETURNED_FIELD, label="PDC Returned Amount", fieldtype="Currency",
					options="paid_to_account_currency", insert_after=CLEARED_FIELD),
			],
			"Journal Entry": [
				{
					"fieldname": SOURCE_FIELD,
					"label": "Returned Cheque",
					"fieldtype": "Link",
					"options": "Payment Entry",
					"insert_after": "cheque_date",
					"read_only": 1,
					"no_copy": 1,
					"description": "Set when this journal reverses a cheque returned unpaid.",
				},
				{
					"fieldname": RETURN_AMOUNT_FIELD,
					"label": "Returned Cheque Amount",
					"fieldtype": "Currency",
					"insert_after": SOURCE_FIELD,
					"read_only": 1,
					"no_copy": 1,
					"depends_on": "eval:doc." + SOURCE_FIELD,
				},
			],
		},
		ignore_validate=True,
		update=True,
	)

	# create_custom_fields creates but never corrects, and this field shipped read-only before a
	# transfer could be built by hand. Put it right on every migrate.
	name = "Payment Entry-" + SOURCE_FIELD
	if frappe.db.exists("Custom Field", name) and frappe.db.get_value("Custom Field", name, "read_only"):
		frappe.db.set_value("Custom Field", name, "read_only", 0)
		frappe.clear_cache(doctype="Payment Entry")

	backfill_positions()


def ensure_rejection_field():
	"""after_migrate: the date a cheque was returned unpaid, alongside clearance_date."""
	create_custom_fields(
		{
			"Payment Entry": [
				{
					"fieldname": REJECTION_FIELD,
					"label": "PDC Returned On",
					"fieldtype": "Date",
					"insert_after": "clearance_date",
					"allow_on_submit": 1,
					"read_only": 1,
					"no_copy": 1,
					"depends_on": "eval:doc.mode_of_payment",
					"description": "Set when the cheque is returned unpaid (bounced). Empty means it was not.",
				}
			]
		},
		ignore_validate=True,
		update=True,
	)


def backfill_positions():
	"""Give every submitted cheque without a PDC Status its position. Runs after the fields exist
	(after_migrate), so it can never meet a missing column; cheques already positioned are left
	alone, so a re-run costs one query."""
	if not frappe.db.has_column("Payment Entry", STATUS_FIELD):
		return
	modes = cheque_modes()
	if not modes:
		return
	for name in frappe.get_all(
		"Payment Entry",
		filters={"docstatus": 1, "mode_of_payment": ["in", modes], "payment_type": ["in", ["Receive", "Pay"]],
			STATUS_FIELD: ["is", "not set"]},
		pluck="name",
	):
		refresh(name)


# ─── the cheque and its position ─────────────────────────────────────────────────────────────────


def cheque_modes() -> list:
	"""Modes of Payment whose ZATCA payment means code is the cheque code."""
	modes = frappe.get_all("Mode of Payment", fields=["name", "custom_zatca_payment_means_code"])
	return [m.name for m in modes if (m.custom_zatca_payment_means_code or "").strip() == CHEQUE_CODE]


def is_receipt(pe) -> bool:
	return pe.payment_type == "Receive"


def holding_account(pe) -> str:
	"""The account the cheque's money sits in until it is banked or returned."""
	return pe.paid_to if is_receipt(pe) else pe.paid_from


def cheque_amount(pe) -> float:
	return flt(pe.received_amount) if is_receipt(pe) else flt(pe.paid_amount)


def transfer_rows(payment_entries: list) -> dict:
	"""{cheque: [transfer rows]} -- every non-cancelled Internal Transfer naming each cheque."""
	if not payment_entries or not frappe.db.has_column("Payment Entry", SOURCE_FIELD):
		return {}
	found = {}
	for row in frappe.get_all(
		"Payment Entry",
		filters={SOURCE_FIELD: ["in", list(payment_entries)], "docstatus": ["<", 2], "payment_type": "Internal Transfer"},
		fields=["name", "docstatus", "posting_date", "paid_from", "paid_to", "paid_amount", SOURCE_FIELD + " as source"],
		order_by="posting_date asc, creation asc",
	):
		found.setdefault(row.source, []).append(row)
	return found


def return_rows(payment_entries: list) -> dict:
	"""{cheque: [return journal rows]} -- every non-cancelled return Journal Entry naming each cheque."""
	if not payment_entries or not frappe.db.has_column("Journal Entry", SOURCE_FIELD):
		return {}
	found = {}
	for row in frappe.get_all(
		"Journal Entry",
		filters={SOURCE_FIELD: ["in", list(payment_entries)], "docstatus": ["<", 2]},
		fields=["name", "docstatus", "posting_date", RETURN_AMOUNT_FIELD + " as amount", SOURCE_FIELD + " as source"],
		order_by="posting_date asc, creation asc",
	):
		found.setdefault(row.source, []).append(row)
	return found


def transfers_for(payment_entries: list) -> dict:
	"""{cheque: one transfer row} -- the latest submitted transfer, else a draft. Kept for callers
	that show a single transfer per cheque (the PDC Report's transfer columns)."""
	out = {}
	for cheque, rows in transfer_rows(payment_entries).items():
		submitted = [r for r in rows if cint(r.docstatus) == 1]
		out[cheque] = (submitted or rows)[-1]
	return out


def position_of(pe, transfers=None, returns=None) -> frappe._dict:
	"""How much of a cheque has been banked, returned, and is still waiting -- from its documents."""
	transfers = transfers if transfers is not None else transfer_rows([pe.name]).get(pe.name, [])
	returns = returns if returns is not None else return_rows([pe.name]).get(pe.name, [])
	submitted_transfers = [t for t in transfers if cint(t.docstatus) == 1]
	submitted_returns = [r for r in returns if cint(r.docstatus) == 1]
	amount = cheque_amount(pe)
	cleared = flt(sum(flt(t.paid_amount) for t in submitted_transfers), 3)
	returned = flt(sum(flt(r.amount) for r in submitted_returns), 3)
	# Cheques from before transfers and returns were counted: one whose clearance_date was set
	# with no transfer (the bank reconciled the cheque account itself) reads cleared in full, and
	# one marked rejected by date alone, with no journal behind it, keeps reading returned -- its
	# money is still in the holding account, so a return journal can still be posted for it.
	legacy_cleared = not transfers and not returns and bool(pe.get("clearance_date"))
	legacy_returned = not returns and bool(pe.get(REJECTION_FIELD))
	if legacy_cleared:
		cleared = amount
	remaining = flt(amount - cleared - returned, 3)

	if returned > TOLERANCE:
		status = RETURNED if cleared <= TOLERANCE and remaining <= TOLERANCE else PARTLY_RETURNED
	elif cleared <= TOLERANCE:
		status = RETURNED if legacy_returned else PENDING
	elif remaining > TOLERANCE:
		status = PARTLY_CLEARED
	else:
		status = CLEARED

	return frappe._dict(
		amount=amount, cleared=cleared, returned=returned, remaining=max(remaining, 0.0), status=status,
		last_cleared_on=max((getdate(t.posting_date) for t in submitted_transfers), default=None),
		last_returned_on=max((getdate(r.posting_date) for r in submitted_returns), default=None),
		legacy_cleared=legacy_cleared, legacy_returned=legacy_returned,
		transfers=transfers, returns=returns,
		drafts=[t.name for t in transfers if cint(t.docstatus) == 0] + [r.name for r in returns if cint(r.docstatus) == 0],
	)


def refresh(payment_entry: str):
	"""Write the cheque's position onto it. Called on every submit/cancel of a transfer or return."""
	pe = frappe.get_doc("Payment Entry", payment_entry)
	if pe.docstatus != 1 or pe.mode_of_payment not in cheque_modes() or pe.payment_type not in ("Receive", "Pay"):
		return
	pos = position_of(pe)
	values = {
		CLEARED_FIELD: pos.cleared,
		RETURNED_FIELD: pos.returned,
		STATUS_FIELD: pos.status,
		# cleared means banked in full; a cheque that came back is closed by its return date instead
		"clearance_date": pos.last_cleared_on if pos.status == CLEARED else None,
		REJECTION_FIELD: pos.last_returned_on if pos.returned > TOLERANCE else None,
	}
	# what the old screens wrote is kept until a transfer or a return journal replaces it
	if pos.legacy_cleared:
		values.pop("clearance_date")
	if pos.legacy_returned:
		values.pop(REJECTION_FIELD)
	values = {k: v for k, v in values.items() if frappe.db.has_column("Payment Entry", k)}
	frappe.db.set_value("Payment Entry", pe.name, values, update_modified=False)
	return pos


def _load_cheque_entry(payment_entry: str):
	"""The cheque Payment Entry, checked for everything banking or returning it needs."""
	pe = frappe.get_doc("Payment Entry", payment_entry)
	if pe.docstatus != 1:
		frappe.throw(_("Payment Entry {0} is not submitted.").format(pe.name))
	if pe.payment_type not in ("Receive", "Pay"):
		frappe.throw(_("{0} is an {1} entry, not a cheque received or issued.").format(pe.name, _(pe.payment_type)))
	if pe.mode_of_payment not in cheque_modes():
		frappe.throw(
			_("{0} is not a cheque payment: its mode of payment does not carry payment means code {1}.").format(
				pe.name, CHEQUE_CODE
			)
		)
	if not holding_account(pe):
		frappe.throw(_("{0} has no account the cheque is held in.").format(pe.name))
	return pe


# ─── banking (clearing) ──────────────────────────────────────────────────────────────────────────


@frappe.whitelist()
@frappe.validate_and_sanitize_search_inputs
def cheques_awaiting_transfer(doctype, txt, searchfield, start, page_len, filters):
	"""Link query: submitted cheques, received or issued, that still have something to bank."""
	modes = cheque_modes()
	if not modes:
		return []
	conditions = {
		"docstatus": 1,
		"payment_type": ["in", ["Receive", "Pay"]],
		"mode_of_payment": ["in", modes],
		"clearance_date": ["is", "not set"],
	}
	if frappe.db.has_column("Payment Entry", STATUS_FIELD):
		conditions[STATUS_FIELD] = ["in", ["", PENDING, PARTLY_CLEARED]]
	if (filters or {}).get("company"):
		conditions["company"] = filters["company"]
	if txt:
		conditions["name"] = ["like", "%" + txt + "%"]
	rows = frappe.get_all(
		"Payment Entry",
		filters=conditions,
		fields=["name", "reference_no", "party_name", "payment_type", "paid_amount", "received_amount"],
		order_by="reference_date asc",
		limit_start=start,
		limit_page_length=page_len,
	)
	return [[r.name, r.reference_no, r.party_name, _(r.payment_type),
		str(flt(r.received_amount) if r.payment_type == "Receive" else flt(r.paid_amount))] for r in rows]


@frappe.whitelist()
def get_transfer_context(payment_entry: str) -> dict:
	"""What the cheque's PDC actions need: its direction, accounts and position."""
	frappe.has_permission("Payment Entry", "read", doc=payment_entry, throw=True)
	pe = frappe.get_doc("Payment Entry", payment_entry)
	is_cheque = pe.mode_of_payment in cheque_modes() and pe.payment_type in ("Receive", "Pay")
	pos = position_of(pe) if is_cheque and pe.docstatus == 1 else frappe._dict(transfers=[], returns=[], drafts=[])
	receipt = is_receipt(pe)
	submitted = [t for t in pos.transfers if cint(t.docstatus) == 1]
	return {
		"payment_entry": pe.name,
		"is_cheque": is_cheque,
		"direction": "received" if receipt else "issued",
		"company": pe.company,
		"holding_account": holding_account(pe) if is_cheque else None,
		# kept for the hand-built transfer form: the account the transfer moves money OUT of
		"from_account": holding_account(pe) if receipt else None,
		"amount": cheque_amount(pe),
		"remaining": pos.get("remaining"),
		"cleared": pos.get("cleared"),
		"returned": pos.get("returned"),
		"status": pos.get("status"),
		"currency": pe.paid_to_account_currency if receipt else pe.paid_from_account_currency,
		"cheque_no": pe.reference_no,
		"cheque_date": str(pe.reference_date) if pe.reference_date else None,
		"clearance_date": str(pe.clearance_date) if pe.clearance_date else None,
		"rejection_date": str(pe.get(REJECTION_FIELD)) if pe.get(REJECTION_FIELD) else None,
		"transfers": [{"name": t.name, "docstatus": cint(t.docstatus), "amount": flt(t.paid_amount),
			"posting_date": str(t.posting_date)} for t in pos.transfers],
		"returns": [{"name": r.name, "docstatus": cint(r.docstatus), "amount": flt(r.amount),
			"posting_date": str(r.posting_date)} for r in pos.returns],
		"drafts": pos.get("drafts"),
		# the latest transfer, for screens that show one
		"transfer": submitted[-1].name if submitted else (pos.transfers[-1].name if pos.transfers else None),
		"transfer_docstatus": 1 if submitted else (cint(pos.transfers[-1].docstatus) if pos.transfers else None),
	}


@frappe.whitelist()
def create_internal_transfer(
	payment_entry: str,
	to_account: str,
	posting_date: str = None,
	submit: int | str = 1,
	amount: float | str = None,
) -> str:
	"""Bank a cheque, in full or in part, by an Internal Transfer.

	Args:
		payment_entry: the submitted cheque Payment Entry, received or issued
		to_account: the bank account -- the one a received cheque was credited to, or the one an
			issued cheque was paid from
		posting_date: the date the bank moved the money; defaults to today
		submit: submit the transfer (the default); 0 leaves a draft for approval
		amount: how much the bank moved; defaults to everything still waiting on the cheque

	Returns:
		The Internal Transfer Payment Entry name.
	"""
	frappe.has_permission("Payment Entry", "read", doc=payment_entry, throw=True)
	frappe.has_permission("Payment Entry", "create", throw=True)
	if not to_account:
		frappe.throw(_("Select the bank account."))

	source = _load_cheque_entry(payment_entry)
	pos = position_of(source)
	if pos.drafts:
		frappe.throw(
			_("Cheque {0} has a draft waiting ({1}). Submit or delete it first.").format(source.name, ", ".join(pos.drafts)),
			title=_("Draft Pending"),
		)
	if pos.remaining <= TOLERANCE:
		frappe.throw(_("Nothing is left to bank on cheque {0} ({1}).").format(source.name, _(pos.status)),
			title=_("Already Settled"))

	holding = holding_account(source)
	if holding == to_account:
		frappe.throw(_("The cheque is held in {0}. Choose the bank account instead.").format(to_account))
	account = frappe.get_cached_value("Account", to_account, ["company", "is_group"], as_dict=True)
	if not account or account.company != source.company:
		frappe.throw(_("Account {0} does not belong to company {1}.").format(to_account, source.company))
	if cint(account.is_group):
		frappe.throw(_("Account {0} is a group. Choose the ledger account itself.").format(to_account))

	value = flt(amount) if amount not in (None, "") else pos.remaining
	if value <= 0:
		frappe.throw(_("Enter the amount the bank moved."))
	if value - pos.remaining > TOLERANCE:
		frappe.throw(_("Only {0} is still waiting on cheque {1}.").format(pos.remaining, source.name))

	transfer = frappe.new_doc("Payment Entry")
	transfer.payment_type = "Internal Transfer"
	transfer.company = source.company
	transfer.posting_date = getdate(posting_date or nowdate())
	if is_receipt(source):
		transfer.paid_from, transfer.paid_to = holding, to_account
	else:
		transfer.paid_from, transfer.paid_to = to_account, holding
	transfer.paid_amount = value
	transfer.received_amount = value
	transfer.cost_center = source.cost_center
	if source.get("branch") and transfer.meta.has_field("branch"):
		transfer.branch = source.branch
	# the cheque's own mode, number and date, so a bank statement can be matched against it; the
	# PDC Report and the cheque reminder exclude Internal Transfers, so this never counts twice
	transfer.mode_of_payment = source.mode_of_payment
	transfer.reference_no = source.reference_no or source.name
	transfer.reference_date = source.reference_date or source.posting_date
	transfer.set(SOURCE_FIELD, source.name)
	banking = _("Banking of post-dated cheque {0} ({1})").format(source.name, source.reference_no or _("no cheque number"))
	if abs(value - pos.amount) > TOLERANCE:
		banking += " " + _("(part: {0} of {1})").format(value, pos.amount)
	transfer.remarks = banking
	transfer.insert()

	if cint(submit):
		# raised from the cheque it banks: already decided by whoever banked it
		transfer.flags.ignore_workflow = True
		transfer.submit()
	return transfer.name


@frappe.whitelist()
def create_internal_transfers(payment_entries, to_account: str, posting_date: str = None, submit=1) -> dict:
	"""Bank several cheques in full into the same account in one go, from the PDC Report.

	Every cheque is attempted; one that cannot be banked is reported rather than taking the whole
	batch down with it.
	"""
	if isinstance(payment_entries, str):
		payment_entries = frappe.parse_json(payment_entries) or []
	if not payment_entries:
		frappe.throw(_("Select at least one cheque."))
	created, failed = [], []
	for name in payment_entries:
		savepoint = "sf_pdc_transfer"
		frappe.db.savepoint(savepoint)
		try:
			created.append(create_internal_transfer(name, to_account, posting_date, submit))
		except Exception as exc:
			frappe.db.rollback(save_point=savepoint)
			failed.append({"payment_entry": name, "error": str(exc)})
			frappe.clear_last_message()
	return {"created": created, "failed": failed}


def validate(doc, method=None):
	"""Payment Entry validate: a transfer naming a cheque, whoever built it.

	The money must leave (received cheque) or reach (issued cheque) the account the cheque is
	held in, and all the cheque's transfers together may not bank more than is left on it once
	what was returned is taken off.
	"""
	source_name = doc.get(SOURCE_FIELD)
	if not source_name:
		return
	if doc.payment_type != "Internal Transfer":
		frappe.throw(
			_("Only an Internal Transfer may name a PDC Payment Entry. This one is a {0} entry.").format(_(doc.payment_type))
		)
	source = _load_cheque_entry(source_name)
	holding = holding_account(source)
	if is_receipt(source) and doc.paid_from != holding:
		frappe.throw(_("The cheque was received into {0}, so the transfer has to be paid from that account.").format(
			frappe.bold(holding)))
	if not is_receipt(source) and doc.paid_to != holding:
		frappe.throw(_("The cheque was issued from {0}, so the transfer has to be paid into that account.").format(
			frappe.bold(holding)))

	others = [t for t in transfer_rows([source.name]).get(source.name, []) if t.name != doc.name]
	pos = position_of(source, transfers=others)
	if flt(doc.paid_amount) - pos.remaining > TOLERANCE:
		frappe.throw(
			_("Cheque {0} has {1} left to bank (worth {2}, banked {3}, returned {4}).").format(
				source.name, pos.remaining, pos.amount, pos.cleared, pos.returned
			),
			title=_("More Than the Cheque"),
		)


def on_submit(doc, method=None):
	"""A submitted cheque starts Pending; a submitted transfer moves its cheque on."""
	source = doc.get(SOURCE_FIELD)
	if source and doc.payment_type == "Internal Transfer" and frappe.db.exists("Payment Entry", source):
		refresh(source)
	elif doc.payment_type in ("Receive", "Pay") and doc.mode_of_payment and doc.docstatus == 1:
		pos = refresh(doc.name)
		if pos:
			doc.set(STATUS_FIELD, pos.status)


def on_cancel(doc, method=None):
	"""Cancelling a transfer gives its amount back to the cheque. A cheque itself cannot be
	cancelled while a submitted transfer or return names it: Frappe refuses on the link."""
	source = doc.get(SOURCE_FIELD)
	if source and doc.payment_type == "Internal Transfer" and frappe.db.exists("Payment Entry", source):
		refresh(source)


# ─── return (bounced cheque) ─────────────────────────────────────────────────────────────────────


def _settled_documents(pe):
	"""What the cheque paid, in the order a return gives it back: an advance (unallocated) first,
	then each document it settled, the most recent first. Each item is (amount, doctype, name);
	doctype is None for money not tied to an invoice."""
	invoice_doctype = "Sales Invoice" if is_receipt(pe) else "Purchase Invoice"
	party_account = pe.paid_from if is_receipt(pe) else pe.paid_to
	account_field = "debit_to" if is_receipt(pe) else "credit_to"
	items = []
	if flt(pe.unallocated_amount) > 0:
		items.append((flt(pe.unallocated_amount), None, None))
	for ref in sorted(pe.get("references") or [], key=lambda r: -cint(r.idx)):
		allocated = flt(ref.allocated_amount)
		if allocated <= 0:
			continue
		if ref.reference_doctype == invoice_doctype and frappe.db.get_value(
			invoice_doctype, ref.reference_name, account_field
		) == party_account:
			items.append((allocated, invoice_doctype, ref.reference_name))
		else:
			# an order advance or a journal: the party owes it again, but not on an invoice
			items.append((allocated, None, None))
	return items


def _return_journal(pe, pos, return_date, reason):
	receipt = is_receipt(pe)
	party_account = pe.paid_from if receipt else pe.paid_to
	holding = holding_account(pe)
	company_currency = frappe.get_cached_value("Company", pe.company, "default_currency")
	for account in (party_account, holding):
		if frappe.get_cached_value("Account", account, "account_currency") != company_currency:
			frappe.throw(_("Cheque {0} is in a foreign currency; return it by a journal entered by hand.").format(pe.name))

	amount = pos.remaining
	chunks, left = [], amount
	for value, doctype, name in _settled_documents(pe):
		if left <= TOLERANCE:
			break
		take = min(value, left)
		chunks.append((take, doctype, name))
		left = flt(left - take, 3)
	if left > TOLERANCE:
		chunks.append((left, None, None))
	# one row for everything not tied to an invoice
	merged, loose = [], 0.0
	for take, doctype, name in chunks:
		if doctype:
			merged.append((flt(take, 3), doctype, name))
		else:
			loose += take
	if loose > TOLERANCE:
		merged.insert(0, (flt(loose, 3), None, None))

	party_side = "debit_in_account_currency" if receipt else "credit_in_account_currency"
	holding_side = "credit_in_account_currency" if receipt else "debit_in_account_currency"
	dimensions = {"cost_center": pe.cost_center}
	if pe.get("branch"):
		dimensions["branch"] = pe.branch

	je = frappe.new_doc("Journal Entry")
	je.voucher_type = "Journal Entry"
	je.company = pe.company
	je.posting_date = return_date
	je.cheque_no = pe.reference_no or pe.name
	je.cheque_date = pe.reference_date or pe.posting_date
	direction = _("received from") if receipt else _("issued to")
	remark = _("Cheque {0} {1} {2} returned unpaid (PDC {3}).").format(
		pe.reference_no or "", direction, pe.party_name or pe.party, pe.name
	)
	je.user_remark = remark + ((" " + reason) if reason else "")
	# raised by the cheque's own Return action, never typed: the approval chain and backdate
	# control exempt it on that basis, as they do every journal ERPNext raises for a document
	je.is_system_generated = 1
	je.set(SOURCE_FIELD, pe.name)
	je.set(RETURN_AMOUNT_FIELD, amount)
	for take, doctype, name in merged:
		row = dict(dimensions, account=party_account, party_type=pe.party_type, party=pe.party)
		row[party_side] = take
		if doctype:
			row.update(reference_type=doctype, reference_name=name)
		je.append("accounts", row)
	holding_row = dict(dimensions, account=holding)
	holding_row[holding_side] = amount
	je.append("accounts", holding_row)
	return je


@frappe.whitelist()
def return_pdc(payment_entry: str, return_date: str = None, reason: str = None) -> dict:
	"""Return a cheque unpaid: reverse whatever of it is still held, by a Journal Entry.

	A cheque partly banked returns only the rest. The journal is submitted at once and names the
	cheque; cancelling it undoes the return.
	"""
	frappe.has_permission("Payment Entry", "write", doc=payment_entry, throw=True)
	frappe.has_permission("Journal Entry", "create", throw=True)
	pe = _load_cheque_entry(payment_entry)
	pos = position_of(pe)
	if pos.drafts:
		frappe.throw(_("Cheque {0} has a draft waiting ({1}). Submit or delete it first.").format(pe.name, ", ".join(pos.drafts)))
	if pos.remaining <= TOLERANCE:
		frappe.throw(_("Nothing is left on cheque {0} to return ({1}).").format(pe.name, _(pos.status)))

	date = getdate(return_date or nowdate())
	if date < getdate(pe.posting_date):
		frappe.throw(_("A cheque cannot be returned before it was received or issued ({0}).").format(pe.posting_date))
	if date > getdate(nowdate()):
		frappe.throw(_("The return date cannot be in the future."))

	je = _return_journal(pe, pos, date, cstr(reason).strip())
	je.insert()
	je.submit()
	return {"journal_entry": je.name, "amount": pos.remaining, "status": refresh(pe.name).status}


@frappe.whitelist()
def reject_pdc(payment_entry: str, rejection_date: str = None, reason: str = None) -> dict:
	"""The older name of `return_pdc`, kept for screens and links that still call it."""
	result = return_pdc(payment_entry, rejection_date, reason)
	result[REJECTION_FIELD] = str(getdate(rejection_date or nowdate()))
	return result


def journal_on_change(doc, method=None):
	"""Journal Entry on_submit / on_cancel: a return journal moves its cheque."""
	source = doc.get(SOURCE_FIELD) if doc.meta.has_field(SOURCE_FIELD) else None
	if source and frappe.db.exists("Payment Entry", source):
		refresh(source)


def journal_validate(doc, method=None):
	"""Journal Entry validate: a return can never reverse more than is left on its cheque."""
	source = doc.get(SOURCE_FIELD) if doc.meta.has_field(SOURCE_FIELD) else None
	if not source:
		return
	pe = _load_cheque_entry(source)
	others = [r for r in return_rows([pe.name]).get(pe.name, []) if r.name != doc.name]
	pos = position_of(pe, returns=others)
	if flt(doc.get(RETURN_AMOUNT_FIELD)) - pos.remaining > TOLERANCE:
		frappe.throw(_("Cheque {0} has only {1} left to return.").format(pe.name, pos.remaining))
