# sf_trading/patches/v0_1/seed_exempt_supplier_groups.py
"""Make the two Supplier Groups whose suppliers are not asked for Payment Terms, a supporting document or a Tax ID.

"Govt" (a ministry or other government body) and "W/O VAT" (a supplier without VAT): sf_trading/party_documents.py
COMPLETENESS_EXEMPT_SUPPLIER_GROUPS. Neither exists on prod or UAT, so a supplier cannot be put in one until they do.
Only the groups are made here: no supplier is moved, that is done by choosing the group on the supplier's form.

Idempotent: a group that exists, whatever its parent, is left as it is.
"""

import frappe

PARENT = "All Supplier Groups"
GROUPS = ("Govt", "W/O VAT")


def execute():
	if not frappe.db.exists("DocType", "Supplier Group"):
		return
	if not frappe.db.exists("Supplier Group", PARENT):
		frappe.logger("sf_trading").warning("seed_exempt_supplier_groups: no %s, nothing made", PARENT)
		return

	made = []
	for name in GROUPS:
		if frappe.db.exists("Supplier Group", name):
			continue
		frappe.get_doc(
			{"doctype": "Supplier Group", "supplier_group_name": name, "parent_supplier_group": PARENT, "is_group": 0}
		).insert(ignore_permissions=True)
		made.append(name)

	frappe.db.commit()
	frappe.logger("sf_trading").info("seed_exempt_supplier_groups: made %s", ", ".join(made) or "nothing (both exist)")
