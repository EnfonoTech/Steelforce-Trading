"""Open-item gate on the Period Closing Voucher.

A period should not close while documents dated inside it still wait for
their counterpart — an invoice with no delivery, a delivery with no invoice,
a receipt with no bill, a bill with no receipt. Those rows are exactly what
the four open item reports print (sf_trading.open_items), so the gate reuses
that engine rather than judging afresh.

Population vs clearing dates are deliberately different:

    counted:  rows whose document is dated ON OR BEFORE the period end
    cleared:  by any counterpart submitted up to TODAY, whatever its date

so a delivery raised this morning against a March invoice unblocks a March
closing immediately — the accountant clears items, refreshes the draft
voucher, and watches the list shrink. Cutting the clearing at period end
instead would leave the voucher permanently blocked by items that are, in
fact, resolved.

Wired in hooks.py:
    doc_events["Period Closing Voucher"]["before_submit"] -> validate_open_items
    doctype_js["Period Closing Voucher"] -> public/js/period_closing_voucher.js
"""

import frappe
from frappe import _
from frappe.utils import flt, fmt_money, getdate

from sf_trading.open_items import (
	billed_items_pending_receipt,
	delivered_items_pending_billing,
	invoiced_items_to_be_delivered,
	received_items_pending_billing,
)

FLOWS = [
	("Invoiced Items To Be Delivered", invoiced_items_to_be_delivered),
	("Delivered Items Pending Billing", delivered_items_pending_billing),
	("Received Items Pending Billing", received_items_pending_billing),
	("Billed Items Pending Receipt", billed_items_pending_receipt),
]


@frappe.whitelist()
def pending_open_items(company: str, period_end_date: str):
	"""Per-flow census of items dated in or before the period, still open now.

	Serves the form view and the submit gate alike, so what the accountant
	sees on the draft is byte-for-byte what the gate will judge.
	"""
	frappe.has_permission("Period Closing Voucher", "read", throw=True)

	if not (company and period_end_date):
		frappe.throw(_("Company and Period End Date are required"))

	period_end = getdate(period_end_date)
	filters = frappe._dict({"company": company})  # as_on defaults to today

	summary = []
	for report_name, flow in FLOWS:
		rows = [row for row in flow(filters) if getdate(row.posting_date) <= period_end]
		summary.append(
			{
				"report": report_name,
				"documents": len({row.document for row in rows}),
				"items": len(rows),
				"qty": flt(sum(row.pending_qty for row in rows), 3),
				"value": flt(sum(row.pending_amount for row in rows), 2),
			}
		)
	return summary


def validate_open_items(doc, method=None):
	"""Period Closing Voucher.before_submit: refuse while open items remain."""
	pending = [row for row in pending_open_items(doc.company, doc.period_end_date) if row["items"]]

	if not pending:
		return

	currency = frappe.get_cached_value("Company", doc.company, "default_currency")
	lines = []
	for row in pending:
		detail = _("%(documents)s documents, %(items)s items, value %(value)s") % {
			"documents": row["documents"],
			"items": row["items"],
			"value": fmt_money(row["value"], currency=currency),
		}
		lines.append("<li>" + frappe.bold(_(row["report"])) + ": " + detail + "</li>")

	message = _(
		"The period up to %(period_end)s still carries open items — bill, deliver, receive or return them first:"
	) % {"period_end": frappe.bold(frappe.format(getdate(doc.period_end_date), {"fieldtype": "Date"}))}
	message += "<ul>" + "".join(lines) + "</ul>"
	message += _("Each list above is available as a report of the same name.")

	frappe.throw(msg=message, title=_("Open Items Pending"))
