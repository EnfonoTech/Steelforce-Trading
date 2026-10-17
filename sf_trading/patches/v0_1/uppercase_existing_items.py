# sf_trading/patches/v0_1/uppercase_existing_items.py
"""Put the existing Item master into capitals (sf_trading/item_case.py is the rule for new entries).

On sft prod (2026-10-17): 31 item names and 9 item codes were not in capitals, out of 16,748 items.

1. Item Name: for every item whose name is not already in capitals, the name is stripped and upper-cased. Only those rows are written, so the
   16,717 names that are already right are not touched, and a second run changes nothing. The same text is carried to the places that copy it:
   the item's Item Price rows, and its Description when the description holds the old name verbatim. Historical documents keep the name they
   were made with (ERPNext never rewrites an invoice's item name when the master changes).
2. Item Code: the nine mixed-case codes are renamed to their capitals with frappe.rename_doc (the supported way: it updates every link, Item Price,
   Bin and the purchase rows that name them, about 110 rows in total, and the Item's child tables). Frappe treats a case-only rename as a rename
   ("for fixing case, accents"). A code whose capitals already belong to ANOTHER item is skipped and logged, never merged.

Row writes keep `modified`; nothing is committed until the end.
"""

import frappe
from frappe.model.rename_doc import rename_doc

log = None


def execute():
	global log
	log = frappe.logger("sf_trading")
	names = rename_names()
	codes = rename_codes()
	log.info("uppercase_existing_items: %s names, %s codes", names, codes)
	frappe.db.commit()


def rename_names() -> int:
	changed = 0
	rows = frappe.db.sql(
		"SELECT name, item_name, description FROM `tabItem` WHERE BINARY item_name != BINARY UPPER(item_name) OR item_name != TRIM(item_name)",
		as_dict=True,
	)
	for row in rows:
		old = row.item_name or ""
		new = old.strip().upper()
		if new == old:
			continue
		frappe.db.set_value("Item", row.name, "item_name", new, update_modified=False)
		frappe.db.sql("UPDATE `tabItem Price` SET item_name = %s WHERE item_code = %s AND item_name = %s", (new, row.name, old))
		description = row.description or ""
		if old and old in description:
			frappe.db.set_value("Item", row.name, "description", description.replace(old, new), update_modified=False)
		changed += 1
	return changed


def rename_codes() -> int:
	renamed = 0
	for (code,) in frappe.db.sql("SELECT name FROM `tabItem` WHERE BINARY name != BINARY UPPER(name)"):
		target = code.strip().upper()
		if target == code:
			continue
		# another item that really is called the capitalised code: a merge, which this patch will not do
		clash = frappe.db.sql("SELECT name FROM `tabItem` WHERE BINARY name = %s", target)
		if clash:
			log.warning("uppercase_existing_items: %s not renamed, %s already exists", code, target)
			continue
		rename_doc("Item", code, target, force=True, ignore_permissions=True, show_alert=False)
		renamed += 1
	return renamed
