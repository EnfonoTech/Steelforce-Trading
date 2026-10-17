"""0.1.36 put an Applicable Branches table on Price List; 0.1.37 moved it to Item Price, where a price
can differ per branch (sf_trading/item_price_branch.py).

Only sft-uat ever carried the Price List version. This removes it there: the Custom Field, then the
child doctype `Price List Branch` it pointed at (and its table). A site that never had it has nothing to
remove, so the patch is a no-op there.
"""

import frappe


def execute():
	field = "Price List-custom_applicable_branches"
	if frappe.db.exists("Custom Field", field):
		frappe.delete_doc("Custom Field", field, force=True, ignore_permissions=True)

	if frappe.db.exists("DocType", "Price List Branch"):
		frappe.delete_doc("DocType", "Price List Branch", force=True, ignore_permissions=True)

	frappe.clear_cache(doctype="Price List")
