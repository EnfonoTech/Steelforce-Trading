# sf_trading/pdc_transfer.py
"""Move a cleared post-dated cheque out of the cheque account and close the PDC.

A cheque received from a customer is booked to a holding account (the branch's "For PDC" mode
of payment) on the day the invoice is raised, with the cheque's own date on `reference_date`.
When the bank finally credits it, the money has to be moved from that holding account into the
real bank account. Accountants were doing that as a hand-built Internal Transfer Payment Entry
with nothing tying it back to the cheque, so "has this cheque been banked?" could only be
answered by reading dates and amounts and guessing.

This module ties the two together:

* `custom_pdc_source_payment_entry` on the transfer names the cheque's Payment Entry. One
  field, on the transfer rather than on the cheque, because that is the side being created --
  the report reads it backwards to show the transfer against the cheque.
* Submitting the transfer stamps `clearance_date` on the cheque entry, which is what makes the
  PDC Report call it Cleared. Cancelling the transfer takes it back off. `clearance_date` is a
  read-only field with no `allow_on_submit`, so it is written with `frappe.db.set_value` --
  exactly what core's own Bank Clearance tool does.
* The transfer deliberately carries **no** mode of payment. The PDC Report selects Payment
  Entries by cheque-coded mode; giving the transfer the cheque's mode would list the transfer
  as a second cheque and double the outstanding PDC figure.
"""

import frappe
from frappe import _
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields
from frappe.utils import cint, flt, getdate, nowdate

# ZATCA payment-means code for a cheque, carried on Mode of Payment by
# `custom_zatca_payment_means_code`. The PDC Report reads the same code.
CHEQUE_CODE = "20"
SOURCE_FIELD = "custom_pdc_source_payment_entry"


def ensure_custom_fields():
	"""after_migrate: the link from an Internal Transfer back to the cheque it banks."""
	create_custom_fields(
		{
			"Payment Entry": [
				{
					"fieldname": SOURCE_FIELD,
					"label": "PDC Payment Entry",
					"fieldtype": "Link",
					"options": "Payment Entry",
					"insert_after": "reference_date",
					"read_only": 1,
					"depends_on": 'eval:doc.payment_type=="Internal Transfer"',
					"description": (
						"The post-dated cheque this transfer banks. Set by the Create Internal "
						"Transfer action; submitting the transfer marks that cheque cleared."
					),
				}
			]
		},
		ignore_validate=True,
	)


def cheque_modes() -> list:
	"""Modes of Payment whose ZATCA payment means code is the cheque code."""
	modes = frappe.get_all("Mode of Payment", fields=["name", "custom_zatca_payment_means_code"])
	return [m.name for m in modes if (m.custom_zatca_payment_means_code or "").strip() == CHEQUE_CODE]


def transfers_for(payment_entries: list) -> dict:
	"""{cheque payment entry: transfer row} for every non-cancelled transfer naming one.

	A draft transfer counts as "one exists" so a second is not raised beside it, but it is
	reported as a draft -- nothing has moved until it is submitted.
	"""
	if not payment_entries:
		return {}

	# The field is created by after_migrate. The PDC Report imports this module, so on a bench
	# that has pulled the code but not migrated yet, asking for the column would break the
	# report outright rather than simply showing nothing.
	if not frappe.db.has_column("Payment Entry", SOURCE_FIELD):
		return {}

	rows = frappe.get_all(
		"Payment Entry",
		filters={SOURCE_FIELD: ["in", payment_entries], "docstatus": ["<", 2]},
		fields=["name", "docstatus", "posting_date", "paid_to", SOURCE_FIELD + " as source"],
	)

	found = {}
	for row in rows:
		# a submitted transfer always wins over a draft one
		existing = found.get(row.source)
		if existing and cint(existing.docstatus) == 1:
			continue
		found[row.source] = row
	return found


def _load_cheque_entry(payment_entry: str):
	"""The cheque Payment Entry, checked for everything a transfer needs of it."""
	pe = frappe.get_doc("Payment Entry", payment_entry)

	if pe.docstatus != 1:
		frappe.throw(_("Payment Entry {0} is not submitted.").format(pe.name))
	if pe.payment_type != "Receive":
		frappe.throw(
			_("{0} is a {1} entry. Only a received cheque can be banked by an internal transfer.").format(
				pe.name, _(pe.payment_type)
			)
		)
	if pe.mode_of_payment not in cheque_modes():
		frappe.throw(
			_("{0} is not a cheque payment — its mode of payment does not carry payment means code {1}.").format(
				pe.name, CHEQUE_CODE
			)
		)
	if not pe.paid_to:
		frappe.throw(_("{0} has no account the cheque was received into.").format(pe.name))

	return pe


@frappe.whitelist()
def get_transfer_context(payment_entry: str) -> dict:
	"""What the Create Internal Transfer dialog needs to open on a single cheque."""
	frappe.has_permission("Payment Entry", "read", doc=payment_entry, throw=True)

	pe = frappe.get_doc("Payment Entry", payment_entry)
	existing = transfers_for([pe.name]).get(pe.name)

	return {
		"payment_entry": pe.name,
		"is_cheque": pe.mode_of_payment in cheque_modes(),
		"company": pe.company,
		"from_account": pe.paid_to,
		"amount": flt(pe.received_amount) or flt(pe.paid_amount),
		"currency": pe.paid_to_account_currency or pe.paid_from_account_currency,
		"cheque_no": pe.reference_no,
		"cheque_date": str(pe.reference_date) if pe.reference_date else None,
		"clearance_date": str(pe.clearance_date) if pe.clearance_date else None,
		"transfer": existing.name if existing else None,
		"transfer_docstatus": cint(existing.docstatus) if existing else None,
	}


@frappe.whitelist()
def create_internal_transfer(
	payment_entry: str,
	to_account: str,
	posting_date: str = None,
	submit: int | str = 1,
) -> str:
	"""Bank one cheque: an Internal Transfer from the cheque account into `to_account`.

	Args:
		payment_entry: the submitted cheque Payment Entry
		to_account: the bank account the cheque was credited to
		posting_date: the date the bank credited it; defaults to today
		submit: submit the transfer (the default). Left as a draft when 0, for a site that
			routes Payment Entry through an approval chain and wants the transfer approved.

	Returns:
		The Internal Transfer Payment Entry name.
	"""
	frappe.has_permission("Payment Entry", "read", doc=payment_entry, throw=True)
	frappe.has_permission("Payment Entry", "create", throw=True)

	if not to_account:
		frappe.throw(_("Select the bank account the cheque was credited to."))

	source = _load_cheque_entry(payment_entry)

	existing = transfers_for([source.name]).get(source.name)
	if existing:
		frappe.throw(
			_("Internal Transfer {0} already exists for cheque {1}.").format(existing.name, source.name),
			title=_("Already Transferred"),
		)

	if source.paid_to == to_account:
		frappe.throw(_("The cheque is already in {0}. Choose a different bank account.").format(to_account))

	account = frappe.get_cached_value("Account", to_account, ["company", "is_group"], as_dict=True)
	if not account or account.company != source.company:
		frappe.throw(_("Account {0} does not belong to company {1}.").format(to_account, source.company))
	if cint(account.is_group):
		frappe.throw(_("Account {0} is a group. Choose the ledger account itself.").format(to_account))

	amount = flt(source.received_amount) or flt(source.paid_amount)
	if amount <= 0:
		frappe.throw(_("Cheque {0} carries no amount to transfer.").format(source.name))

	transfer = frappe.new_doc("Payment Entry")
	transfer.payment_type = "Internal Transfer"
	transfer.company = source.company
	transfer.posting_date = getdate(posting_date or nowdate())
	transfer.paid_from = source.paid_to
	transfer.paid_to = to_account
	transfer.paid_amount = amount
	transfer.received_amount = amount
	transfer.cost_center = source.cost_center
	# the cheque's own number and date, so the bank statement can be matched against it
	transfer.reference_no = source.reference_no or source.name
	transfer.reference_date = source.reference_date or source.posting_date
	transfer.set(SOURCE_FIELD, source.name)
	transfer.remarks = _("Banking of post-dated cheque {0} ({1})").format(
		source.name, source.reference_no or _("no cheque number")
	)
	transfer.insert()

	if cint(submit):
		# An entry raised from the cheque it banks has already been decided by whoever banked
		# it; the same reasoning as the invoice payment popup's auto-submitted entries.
		transfer.flags.ignore_workflow = True
		transfer.submit()

	return transfer.name


@frappe.whitelist()
def create_internal_transfers(
	payment_entries: str | list,
	to_account: str,
	posting_date: str = None,
	submit: int | str = 1,
) -> dict:
	"""Bank several cheques into the same account in one go, from the PDC Report.

	Every cheque is attempted; one that cannot be banked is reported rather than taking the
	whole batch down with it, so a single bad row does not undo the good ones.
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


def on_submit(doc, method=None):
	"""A submitted transfer closes the cheque behind it."""
	source = doc.get(SOURCE_FIELD)
	if not source or doc.payment_type != "Internal Transfer":
		return
	if not frappe.db.exists("Payment Entry", source):
		return

	frappe.db.set_value("Payment Entry", source, "clearance_date", doc.posting_date, update_modified=False)


def on_cancel(doc, method=None):
	"""Cancelling the transfer re-opens the cheque.

	Only when the cheque's clearance date is still the one this transfer stamped: a cheque
	cleared afterwards through the Bank Clearance tool has been settled some other way, and
	that answer is not this transfer's to undo.
	"""
	source = doc.get(SOURCE_FIELD)
	if not source or doc.payment_type != "Internal Transfer":
		return

	clearance_date = frappe.db.get_value("Payment Entry", source, "clearance_date")
	if not clearance_date or getdate(clearance_date) != getdate(doc.posting_date):
		return

	frappe.db.set_value("Payment Entry", source, "clearance_date", None, update_modified=False)
