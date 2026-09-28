# sf_trading/patches/v0_1/seed_sales_return_reasons.py
"""Seed the standard Sales Return Reason list, so a return's reason is a pick, not a retype.

Nine reasons cover what Steel Force actually raises returns for -- eight causes plus "Other" as
the escape hatch that Remarks has to explain instead (see sf_trading/sales_return_reason.py for
that rule).

Idempotent per row, not per run: checks each reason with frappe.db.exists before inserting it, so
a site that already has one (hand-created before this patch ran, or a re-run after a partial
migrate) is left exactly as it was rather than getting a duplicate or a DuplicateEntryError.
"""

import frappe

REASONS = (
	("Damaged", "The goods arrived, or were found, damaged."),
	("Defective", "The goods do not work, or do not meet quality expectations."),
	("Wrong Item", "The wrong item was delivered or invoiced."),
	("Wrong Quantity", "More or fewer than the ordered quantity was delivered or invoiced."),
	("Quality Issue", "The goods do not match the agreed specification or standard."),
	("Order Cancellation", "The customer cancelled the order after billing."),
	("Over Supply", "More was supplied than the customer ordered or agreed to take."),
	("Duplicate Invoice", "The same sale was billed more than once."),
	("Other", "Any reason not covered above -- Remarks must explain it."),
)


def execute():
	if not frappe.db.exists("DocType", "Sales Return Reason"):
		return

	for reason, description in REASONS:
		if frappe.db.exists("Sales Return Reason", reason):
			continue
		frappe.get_doc(
			{
				"doctype": "Sales Return Reason",
				"reason": reason,
				"description": description,
			}
		).insert(ignore_permissions=True)

	frappe.db.commit()
