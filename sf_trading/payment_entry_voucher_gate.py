# sf_trading/payment_entry_voucher_gate.py
"""A Pay or Receive Payment Entry must be against a voucher.

ERPNext lets a Payment Entry be saved with an empty References table: the money then lands on the party's account
unallocated, tied to no invoice, order or journal entry. On sft prod that happened 18 times since July (11 Pay to a
Customer, 6 Receive from a Customer, 1 Pay to a Supplier of 17,566.359 BHD) against about 9,300 entries that did name a
voucher, and nothing in the entry says why. The owner asked for it to be refused for every party type.

Refused at save (validate), so it cannot be parked as a draft either. An Internal Transfer has no party and no voucher, so it
is not covered. A row counts only when it names a document (a blank row added and never filled in is not a voucher).

`SF Trading Settings > Refuse a Payment Entry with no voucher` turns it off at once, no restart; a switch nobody stored is ON.
"""

import frappe
from frappe import _

from sf_trading import switches

SWITCH = "require_voucher_on_payment_entry"
PAYMENT_TYPES = ("Pay", "Receive")


def validate_has_voucher(doc, method=None):
	"""validate on Payment Entry."""
	if doc.get("payment_type") not in PAYMENT_TYPES:
		return
	if any(row.get("reference_name") for row in doc.get("references") or []):
		return
	if not switches.is_on(SWITCH):
		return

	frappe.throw(
		_(
			"A %s Payment Entry must be against a voucher. Add at least one row to the References table "
			"(an invoice, an order or a journal entry) before saving."
		)
		% _(doc.payment_type),
		title=_("No Voucher"),
	)
