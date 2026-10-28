# sf_trading/patches/v0_1/seed_purchase_return_reasons.py
"""Seed the standard Purchase Return Reason list -- the buying side of seed_sales_return_reasons.

The reasons a debit note goes back to a supplier for, plus "Other" as the escape hatch that
Return/Debit Reason has to explain (sf_trading/sales_return_reason.py holds that rule for both).

Idempotent per row, as the sales seed is: a reason that already exists is left as it was.
"""

import frappe

REASONS = (
	("Damaged", "The goods arrived damaged."),
	("Defective", "The goods do not work, or do not meet quality expectations."),
	("Wrong Item", "The supplier sent or billed the wrong item."),
	("Wrong Quantity", "More or fewer than the ordered quantity was received or billed."),
	("Quality Issue", "The goods do not match the agreed specification or standard."),
	("Excess Supply", "The supplier sent more than was ordered."),
	("Expired", "The goods arrived expired or too close to expiry."),
	("Rate Difference", "The supplier billed a higher rate than agreed."),
	("Duplicate Invoice", "The same purchase was billed more than once."),
	("Other", "Any reason not covered above -- Return/Debit Reason must explain it."),
)


def execute():
	if not frappe.db.exists("DocType", "Purchase Return Reason"):
		return

	for reason, description in REASONS:
		if frappe.db.exists("Purchase Return Reason", reason):
			continue
		frappe.get_doc(
			{
				"doctype": "Purchase Return Reason",
				"reason": reason,
				"description": description,
			}
		).insert(ignore_permissions=True)

	frappe.db.commit()
