# sf_trading/patches/v0_1/seed_sales_person_types.py
"""Seed the two Sales Person Types the sales reports split on: Branch and Outdoor.

Branch is a seller working out of a branch; Outdoor is a field seller whose invoices still carry
a branch, but whose sales the Sales Performance reports must be able to show apart from the
branch's own (see sf_trading/sales_target.py).

Idempotent per row: a type that already exists is left exactly as it was.
"""

import frappe

TYPES = (
	("Branch", "Sells from a branch. Also what a sales person with no type is counted as."),
	("Outdoor", "Sells in the field. Shown apart from the branch's own sales in the sales reports."),
)


def execute():
	if not frappe.db.exists("DocType", "Sales Person Type"):
		return

	for type_name, description in TYPES:
		if frappe.db.exists("Sales Person Type", type_name):
			continue
		frappe.get_doc(
			{
				"doctype": "Sales Person Type",
				"type_name": type_name,
				"description": description,
			}
		).insert(ignore_permissions=True)

	frappe.db.commit()
