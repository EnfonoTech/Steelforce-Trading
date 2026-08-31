# sf_trading/planned_payment.py
"""The refund a return is going to pay, agreed before anyone approves it.

A return over the approval limit cannot be submitted directly, and a Payment Entry cannot exist
against it in the meantime -- ERPNext refuses a Payment Entry whose reference is not submitted
(`payment_entry.validate_reference_documents`), and it refuses it at *validate*, so not even a
draft can be saved. Leaving the refund until after approval meant the cashier agreed an amount
with the customer and then had to remember to go back and record it.

So the cashier still fills the payment popup at save time, and what they enter is kept on the
return itself as a plan: which mode of payment, how much, and the cheque details when there are
any. Nothing is posted. When the approval finally submits the return, the plan becomes real
Payment Entries in the same breath -- the refund posts exactly when the credit note does.

Deliberately not a draft Payment Entry, even a reference-less one: this site routes Payment Entry
through its own PM Workflow, so a draft would have to be approved a second time for one refund,
and a reference-less payment that somebody submits by hand becomes an unallocated advance sitting
against the customer.

The plan is checked at before_submit, where a refusal is still safe, so a return whose figures
changed while it waited cannot approve into a refund that no longer fits.
"""

import json

import frappe
from frappe import _
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields
from frappe.utils import cint, flt

FIELD = "custom_planned_payments"
SECTION = "custom_planned_refund_section"
CHILD = "SF Planned Payment"

# The plan belongs on the Payments tab, in a section of its own. Squeezed into a column of the
# Details tab it rendered about a third of the width it needs -- a grid with a mode of payment, an
# amount and a Payment Entry link, in the space meant for a single field.
SHOWN_WHEN_PLANNED = "eval:doc.custom_planned_payments && doc.custom_planned_payments.length"


def ensure_custom_fields():
	"""after_migrate: the plan table, on the Payments tab, in its own full-width section."""
	create_custom_fields(
		{
			"Sales Invoice": [
				{
					"fieldname": SECTION,
					"label": "Planned Refund",
					"fieldtype": "Section Break",
					"insert_after": "payments_tab",
					"depends_on": SHOWN_WHEN_PLANNED,
				},
				{
					"fieldname": FIELD,
					"label": "Planned Refund",
					"fieldtype": "Table",
					"options": CHILD,
					"insert_after": SECTION,
					"read_only": 1,
					"print_hide": 1,
					"no_copy": 1,
					"depends_on": SHOWN_WHEN_PLANNED,
					"description": (
						"How this refund will be paid once the return is approved. The Payment Entries "
						"are created and submitted with the return itself."
					),
				},
			]
		},
		ignore_validate=True,
	)

	# create_custom_fields creates but never corrects, and this field first shipped on the Details
	# tab beside Payment Mode, where the grid had a third of the width it needs. Put it right on
	# every migrate, so a site that already has it ends up where a fresh one does.
	name = "Sales Invoice-" + FIELD
	if frappe.db.exists("Custom Field", name):
		current = frappe.db.get_value("Custom Field", name, "insert_after")
		if current != SECTION:
			frappe.db.set_value("Custom Field", name, "insert_after", SECTION)
			frappe.clear_cache(doctype="Sales Invoice")


def planned_rows(doc) -> list:
	return doc.get(FIELD) or []


def planned_total(doc) -> float:
	return flt(sum(flt(row.amount) for row in planned_rows(doc)))


def _payable_amount(doc) -> float:
	"""What the return owes the customer, positive."""
	return abs(flt(doc.get("outstanding_amount") or doc.get("rounded_total") or doc.get("grand_total")))


@frappe.whitelist()
def set_planned_payments(
	sales_invoice: str,
	payments: str | list,
	cheque_no: str | None = None,
	cheque_date: str | None = None,
):
	"""Keep the refund the cashier agreed, on the return, until it can be paid.

	Called from the payment popup on a return that has to be approved. Replaces whatever plan was
	there before -- the popup shows the whole refund each time, not an addition to it.
	"""
	frappe.has_permission("Sales Invoice", "write", doc=sales_invoice, throw=True)

	doc = frappe.get_doc("Sales Invoice", sales_invoice)
	if doc.docstatus != 0:
		frappe.throw(_("{0} is not a draft, so its refund cannot be planned any more.").format(doc.name))
	if not cint(doc.is_return):
		frappe.throw(_("{0} is not a return.").format(doc.name))

	if isinstance(payments, str):
		payments = json.loads(payments or "[]")

	doc.set(FIELD, [])
	for row in payments or []:
		amount = flt((row or {}).get("amount"))
		mode = (row or {}).get("mode_of_payment")
		if not mode or amount <= 0:
			continue
		doc.append(
			FIELD,
			{
				"mode_of_payment": mode,
				"amount": amount,
				"cheque_no": (row.get("cheque_no") or cheque_no or None),
				"cheque_date": (row.get("cheque_date") or cheque_date or None),
			},
		)

	_validate_plan(doc)
	doc.save()

	return {
		"planned": planned_total(doc),
		"payable": _payable_amount(doc),
		"rows": len(planned_rows(doc)),
	}


def _validate_plan(doc):
	"""A plan may not promise more than the return owes."""
	planned = planned_total(doc)
	if planned <= 0:
		return

	payable = _payable_amount(doc)
	if planned - payable > 0.0001:
		frappe.throw(
			_("The planned refund of {0} is more than the {1} this return owes.").format(
				planned, payable
			),
			title=_("Refund Does Not Fit"),
		)


def validate_planned_payments(doc, method=None):
	"""before_submit: refuse a plan that no longer fits, while refusing is still possible.

	The return may have been edited while it waited for approval. Checked here rather than at
	on_submit because by then the credit note is posted and a refusal would only strand it.
	"""
	if not cint(doc.get("is_return")) or not planned_rows(doc):
		return
	_validate_plan(doc)


# A state that is not Draft, on a document that is still a draft, means the approval chain has it.
LIVE_APPROVAL_STATES = ("pending", "pending approval", "sent for approval", "awaiting approval")


def require_plan_before_approval(doc, method=None):
	"""validate: a cash return may not enter the approval chain with no refund planned.

	The payment popup is browser code. It gates the SUBMIT button, and that is the only thing it
	gates -- press Submit on the form with nothing entered and it refuses, which is what everyone
	tests. But a return over the approval limit is never submitted from the form: it is saved, sent
	for approval, and then SUBMITTED BY THE APPROVER'S ACTION, server-side, with no browser in the
	loop. Nothing there ever asked about the refund.

	Production, 27 August: 20010004147 was created at 16:40 with the popup dismissed, sent for
	approval at 16:48 and submitted by the approval at 16:53 -- a 12.439 cash refund the customer
	is still owed, and 13 more cash returns since 24 August carry neither a plan nor a payment.

	So the requirement belongs here, on the way IN to the chain, where the cashier is still at the
	counter with the customer. Refusing later, at before_submit, would strand an approved return
	that nobody can now fix without cancelling it.

	Credit-mode returns are exempt: nothing is paid out, the note reduces what the customer owes.
	"""
	if not cint(doc.get("is_return")) or doc.docstatus != 0:
		return
	if (doc.get("custom_payment_mode") or "") == "Credit":
		return
	state = (doc.get("workflow_state") or "").strip().lower()
	if state not in LIVE_APPROVAL_STATES:
		return
	before = doc.get_doc_before_save()
	if before and (before.get("workflow_state") or "").strip().lower() == state:
		# already in the chain; this save is something else and must not be blocked
		return
	if planned_rows(doc) or _payable_amount(doc) < 0.005:
		return

	frappe.throw(
		_("Plan the refund before sending this return for approval. The approval submits the "
		  "return by itself, and a refund that was never planned is never paid -- it simply "
		  "leaves {0} owed to the customer.").format(
			frappe.utils.fmt_money(_payable_amount(doc), currency=doc.get("currency"))),
		title=_("Refund Not Planned"),
	)


def apply_planned_payments(doc, method=None):
	"""on_submit: the plan becomes Payment Entries, in the same breath as the return.

	A failure here is reported, not raised: the return is already submitted by this point and
	throwing would leave a posted credit note behind an error nobody can clear. The rows keep the
	plan, so whoever picks it up can see what was agreed and pay it out with Receive Payment.
	"""
	rows = [row for row in planned_rows(doc) if not row.payment_entry and flt(row.amount) > 0]
	if not cint(doc.get("is_return")) or not rows:
		return

	from sf_trading.api.sales_invoice_payment import create_pos_payments_for_invoice

	# `create_pos_payments_for_invoice` applies a cheque number and date to EVERY row of the call
	# it is given -- which is why the popup has always made two calls for a mixed payment. Sending
	# a cheque row and a cash row together would stamp the cash entry with the cheque's reference
	# and post it on the cheque's date. Grouped the same way here.
	cheque_rows = [row for row in rows if row.cheque_no or row.cheque_date]
	plain_rows = [row for row in rows if row not in cheque_rows]
	batches = []
	for group in (cheque_rows, plain_rows):
		if not group:
			continue
		lead = group[0]
		batches.append(
			(
				group,
				{
					"payments": [
						{"mode_of_payment": row.mode_of_payment, "amount": flt(row.amount)} for row in group
					],
					"cheque_no": lead.cheque_no or None,
					"cheque_date": str(lead.cheque_date) if lead.cheque_date else None,
				},
			)
		)

	created = []
	ordered_rows = []
	try:
		for group, args in batches:
			created.extend(create_pos_payments_for_invoice(sales_invoice=doc.name, **args))
			ordered_rows.extend(group)
	except Exception:
		frappe.log_error(title=f"sf_trading: planned refund for {doc.name} could not be paid")
		frappe.msgprint(
			_("The return was approved, but the refund could not be paid automatically. Use "
			  "<b>Receive Payment</b> on it to pay {0} out.").format(planned_total(doc)),
			title=_("Refund Not Paid"),
			indicator="orange",
		)
		return

	# stamp each row with the entry that paid it. The document is submitted by now, so the rows are
	# written directly rather than through a save the parent would refuse.
	for row, payment_entry in zip(ordered_rows, created, strict=False):
		frappe.db.set_value(CHILD, row.name, "payment_entry", payment_entry, update_modified=False)

	frappe.msgprint(
		_("Refund paid: {0}").format(", ".join(created)),
		title=_("Refund Paid"),
		indicator="green",
		alert=True,
	)


@frappe.whitelist()
def get_planned_payments(sales_invoice: str) -> dict:
	"""What the form shows on a return that is waiting for approval."""
	frappe.has_permission("Sales Invoice", "read", doc=sales_invoice, throw=True)

	doc = frappe.get_doc("Sales Invoice", sales_invoice)
	return {
		"rows": [
			{
				"mode_of_payment": row.mode_of_payment,
				"amount": flt(row.amount),
				"payment_entry": row.payment_entry,
			}
			for row in planned_rows(doc)
		],
		"planned": planned_total(doc),
		"payable": _payable_amount(doc),
		"currency": doc.currency,
	}
