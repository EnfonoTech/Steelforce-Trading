# sf_trading/patches/v0_1/add_item_barcodes.py
"""Give the items that have no barcode the one the July import gave the rest, and fill a blank UOM on those rows (sf_trading/item_barcode.py).

On sft prod 239 of 16,748 items have no barcode row (made after the import). Each gets barcode = Item Code, CODE-128, UOM = its stock UOM, exactly
as the 16,508 imported rows look. The rows are written straight into the child table, so the 239 parent items are not re-saved (no new version, no
modified bump, no chance of an old item's unrelated validation refusing the save).

An item whose code is already some OTHER item's barcode is skipped, as ERPNext allows a barcode on one item only. The barcode column compares
case-insensitively, so the check does too.

The barcode rows that stand for the item (barcode = Item Code) but carry no UOM -- one on prod, 110106020049, added by hand with type EAN-8 -- get the
item's stock UOM. Their type is left as somebody set it.

Idempotent: a second run finds no item without a barcode and no blank UOM.
"""

import frappe

from sf_trading.item_barcode import BARCODE_TYPE


def execute():
	taken = {(code or "").lower() for code in frappe.db.sql_list("select barcode from `tabItem Barcode`")}

	items = frappe.db.sql(
		"""select it.name, it.stock_uom from `tabItem` it
		where not exists (select 1 from `tabItem Barcode` b where b.parent = it.name and b.parenttype = 'Item')""",
		as_dict=True,
	)

	added, skipped = 0, []
	for item in items:
		if item.name.lower() in taken:
			skipped.append(item.name)
			continue

		frappe.get_doc(
			{
				"doctype": "Item Barcode",
				"parent": item.name,
				"parenttype": "Item",
				"parentfield": "barcodes",
				"idx": 1,
				"barcode": item.name,
				"barcode_type": BARCODE_TYPE,
				"uom": item.stock_uom,
			}
		).db_insert()
		taken.add(item.name.lower())
		added += 1

	blank = frappe.db.sql(
		"""select b.name, it.stock_uom from `tabItem Barcode` b join `tabItem` it on it.name = b.parent
		where b.parenttype = 'Item' and ifnull(b.uom, '') = '' and b.barcode = b.parent and ifnull(it.stock_uom, '') != ''""",
		as_dict=True,
	)
	for row in blank:
		frappe.db.set_value("Item Barcode", row.name, "uom", row.stock_uom, update_modified=False)

	frappe.db.commit()
	frappe.logger("sf_trading").info(
		"add_item_barcodes: %s barcodes added, %s skipped (code already another item's barcode: %s), %s blank UOMs filled",
		added,
		len(skipped),
		", ".join(skipped[:10]) or "-",
		len(blank),
	)
