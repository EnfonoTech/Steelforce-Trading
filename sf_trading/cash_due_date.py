# sf_trading/cash_due_date.py
"""A Cash sale is due the day it is made, whatever payment terms its customer carries.

ERPNext fills a Sales Invoice / Sales Order from the CUSTOMER: the customer's default Payment Terms Template builds
the Payment Schedule and sets the Payment Due Date. A credit customer on "40 Days Credit" who pays Cash at the counter
was left with an invoice due in 40 days -- on sft prod 964 of the 8,323 Cash invoices since July carry a later due date,
and the overdue reports, the Payment Schedule and the customer's ageing all read it.

`Payment Mode` (custom_payment_mode) says how the sale is settled. When it is Cash the customer's terms are not looked at:
the template is dropped and the Payment Schedule is ONE row, due on the document's own date, for the whole amount.

Runs as a `validate` doc event, i.e. AFTER the controller's own validate. The controller is what builds the schedule (from
the template, from the linked order's terms, or one default row), so doing it afterwards wins however the schedule was built,
including the invoice raised from a Sales Order that carries terms. The amount is the sum of the rows core worked out (net
of any advance), so nothing moves except the dates and the term.

Left alone: returns, POS invoices, opening entries, a schedule that already holds a payment (paid_amount), any mode but Cash.
"""

import frappe
from frappe.utils import flt, getdate

CASH_MODE = "Cash"


def apply_cash_due_date(doc, method=None):
	"""validate on Sales Invoice and Sales Order."""
	if doc.get("custom_payment_mode") != CASH_MODE:
		return
	if doc.get("is_return") or doc.get("is_pos") or doc.get("is_opening") == "Yes":
		return

	day = doc.get("posting_date") or doc.get("transaction_date")
	if not day:
		return
	day = getdate(day)

	rows = doc.get("payment_schedule") or []
	if any(flt(r.get("paid_amount")) for r in rows):
		return

	has_due_date = doc.meta.has_field("due_date")
	if (
		len(rows) == 1
		and not rows[0].get("payment_term")
		and getdate(rows[0].get("due_date")) == day
		and not doc.get("payment_terms_template")
		and (not has_due_date or getdate(doc.get("due_date")) == day)
	):
		return

	amount = _total(doc, rows, base=False)
	base_amount = _total(doc, rows, base=True)

	doc.payment_terms_template = None
	if has_due_date:
		doc.due_date = day
	if not rows:
		return

	doc.set("payment_schedule", [])
	doc.append(
		"payment_schedule",
		{
			"due_date": day,
			"invoice_portion": 100,
			"payment_amount": amount,
			"base_payment_amount": base_amount,
			"outstanding": amount,
			"base_outstanding": base_amount,
		},
	)


def _total(doc, rows, base):
	"""What one 100% row must carry: the document's own total as core measures it, else what the rows added up to.

	Several terms each round their own share (two halves of 3.675 are 1.838 + 1.838 = 3.676), so the sum of the rows can sit a
	thousandth off the invoice. One row for the whole amount should be exactly the total core would have written for it:
	rounded total (or grand total) less write-off, with no advance in play. With an advance, or when the rows and the total
	disagree by more than core's own 0.1 tolerance, the rows' sum is kept and nothing is papered over.
	"""
	summed = flt(sum(flt(r.get("base_payment_amount" if base else "payment_amount")) for r in rows))
	if doc.get("total_advance"):
		return summed
	total = doc.get("base_rounded_total" if base else "rounded_total") or doc.get("base_grand_total" if base else "grand_total")
	if total is None:
		return summed
	if doc.get("doctype") == "Sales Invoice":
		total = flt(total) - flt(doc.get("base_write_off_amount" if base else "write_off_amount"))
	total = flt(total, doc.precision("base_grand_total" if base else "grand_total"))
	return total if abs(total - summed) <= 0.1 else summed
