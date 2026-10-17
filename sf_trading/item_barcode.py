# sf_trading/item_barcode.py
"""Every Item carries a barcode: its own Item Code, CODE-128, in its stock UOM.

The July import gave 16,509 items exactly that row (barcode = Item Code, type CODE-128, UOM = the item's stock UOM). It was a data load, not
a hook, so the 239 items made since (213 of them by one user) have no barcode at all, and an item typed in by hand needs the row typed in too.
The Altabiah site has Server Scripts that do this for its own Items; Steel Force never had one.

`before_validate` on Item, registered after item_case.capitalise_item so the barcode is the capitalised code: an item with no barcode rows gets
one. An item that already has any barcode is left alone, whatever it is (a supplier's EAN typed in by hand stays the only one).

It never refuses a save. ERPNext does not allow one barcode on two items ("Barcode X already used in Item Y"), so when another item already
holds this Item Code as a barcode the row is simply not added. CODE-128 is a type ERPNext does not check the digits of, unlike EAN-8 / EAN-13 /
UPC, so an alphanumeric code such as SVC-00007 is as good as a number.

CODE-128 is NOT in ERPNext's list of Barcode Types (EAN, UPC-A, CODE-39, EAN-12, EAN-8, GS1, GTIN, ISBN, ISSN, JAN, PZN, UPC), and `Item.validate_barcode`
blanks any type that is not in the list on every save. The July import wrote its CODE-128 straight into the table, so those types survive only until
the item is next saved. The Property Setter `Item Barcode-barcode_type-options` (fixtures) adds CODE-128 to the list, so the type of a row this hook
adds, and of every imported row, is kept. CODE-39 would have been a listed alternative, but ERPNext validates its characters and a code such as
"GIFT,CAKE AND HAMPERS" would then refuse the save; CODE-128 is not validated.

patches/v0_1/add_item_barcodes.py does the same, once, for the items that already have none.
"""

import frappe

BARCODE_TYPE = "CODE-128"


def barcode_taken(code, item) -> bool:
	"""Is `code` already a barcode of some OTHER item? (The comparison is case-insensitive, as the column is.)"""
	return bool(frappe.db.exists("Item Barcode", {"barcode": code, "parent": ["!=", item or ""]}))


def add_item_barcode(doc, method=None):
	"""before_validate on Item."""
	if doc.get("barcodes"):
		return

	code = doc.get("item_code") or doc.get("name")
	if not code or barcode_taken(code, doc.get("name")):
		return

	doc.append("barcodes", {"barcode": code, "barcode_type": BARCODE_TYPE, "uom": doc.get("stock_uom")})
