# sf_trading/item_label.py
"""The Item Label: a sticker with the item's barcode, printed from the Item list or the Item form.

Print Format "Item Label" (sf_trading/sf_trading/print_format/item_label) lays out one 50 x 25 mm label:
the stock UOM and the selling price on top (the price in English and in Arabic), the barcode in the middle
and the item name under it. The barcode is the Item Code -- every item already carries it as its CODE-128
barcode (sf_trading/item_barcode.py) -- so the label scans as the item at the till.

Only an item that is held in stock or is a fixed asset gets a label (`labelled`): a service / non-stock
item is never on a shelf to stick one on. The Print Label button is shown for those items alone, and the
print format refuses the others.

The button asks how many labels to print, showing the stock available (`available_qty`: in the user's
default warehouse when one is set, otherwise in every warehouse), and the print view repeats the label
that many times (`copies` in the print URL; capped at MAX_COPIES).

The barcode is drawn here as an SVG (CODE-128, set C for an even run of digits, set B otherwise), so the
label needs no barcode library and no call to a CDN: it prints the same in the browser and in a PDF.
"""

from __future__ import annotations

import frappe
from frappe import _
from frappe.utils import cint, flt, fmt_money, getdate

# CODE-128 bar/space widths for the symbol values 0..105, and the stop pattern
_PATTERNS = (
	"212222 222122 222221 121223 121322 131222 122213 122312 132212 221213 "
	"221312 231212 112232 122132 122231 113222 123122 123221 223211 221132 "
	"221231 213212 223112 312131 311222 321122 321221 312212 322112 322211 "
	"212123 212321 232121 111323 131123 131321 112313 132113 132311 211313 "
	"231113 231311 112133 112331 132131 113123 113321 133121 313121 211331 "
	"231131 213113 213311 213131 311123 311321 331121 312113 312311 332111 "
	"314111 221411 431111 111224 111422 121124 121421 141122 141221 112214 "
	"112412 122114 122411 142112 142211 241211 221114 413111 241112 134111 "
	"111242 121142 121241 114212 124112 124211 411212 421112 421211 212141 "
	"214121 412121 111143 111341 131141 114113 114311 411113 411311 113141 "
	"114131 311141 411131 211412 211214 211232"
).split()
_STOP = "2331112"
_START_B, _START_C = 104, 105

_ARABIC_DIGITS = str.maketrans("0123456789.,", "٠١٢٣٤٥٦٧٨٩٫٬")
MAX_COPIES = 500


def code128_values(text: str) -> list[int]:
	"""The symbol values for `text`, start and check symbol included (stop excluded)."""
	if text.isascii() and text.isdigit() and len(text) % 2 == 0:
		values = [_START_C] + [int(text[i : i + 2]) for i in range(0, len(text), 2)]
	else:
		if any(not 32 <= ord(ch) <= 127 for ch in text):
			frappe.throw(_("{0} cannot be printed as a barcode.").format(frappe.bold(text)))
		values = [_START_B] + [ord(ch) - 32 for ch in text]
	check = (values[0] + sum(value * position for position, value in enumerate(values[1:], start=1))) % 103
	return values + [check]


def code128_modules(text: str) -> str:
	"""The barcode as a run of 1 (bar) and 0 (space) modules, quiet zones excluded."""
	out = []
	for pattern in [_PATTERNS[value] for value in code128_values(text)] + [_STOP]:
		for position, width in enumerate(pattern):
			out.append(("1" if position % 2 == 0 else "0") * int(width))
	return "".join(out)


def barcode_svg(text: str, height: int = 40) -> str:
	"""An inline SVG of `text` as CODE-128, scaled to the width it is given (preserveAspectRatio none)."""
	modules = code128_modules(text)
	quiet = 10
	width = len(modules) + 2 * quiet
	bars = []
	# neighbouring bar modules make one rect
	position = 0
	while position < len(modules):
		if modules[position] == "1":
			end = position
			while end < len(modules) and modules[end] == "1":
				end += 1
			bars.append(f'<rect x="{quiet + position}" y="0" width="{end - position}" height="{height}"/>')
			position = end
		else:
			position += 1
	return (
		f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" preserveAspectRatio="none" '
		f'width="100%" height="100%" shape-rendering="crispEdges">{"".join(bars)}</svg>'
	)


def labelled(item) -> bool:
	"""A stock item or a fixed asset; never a service / non-stock item."""
	return bool(cint(item.get("is_stock_item")) or cint(item.get("is_fixed_asset")))


def selling_price(item_code: str, uom: str | None = None) -> frappe._dict | None:
	"""The item's price on the default selling price list (Selling Settings), for its stock UOM when
	the list prices it per UOM; None when it has no price there."""
	price_list = frappe.db.get_single_value("Selling Settings", "selling_price_list")
	if not price_list:
		return None
	prices = frappe.get_all(
		"Item Price",
		filters={"item_code": item_code, "price_list": price_list, "selling": 1},
		fields=["price_list_rate", "currency", "uom", "valid_from", "valid_upto", "customer", "supplier", "batch_no"],
		order_by="valid_from desc, modified desc",
	)
	# the shelf price: one in force today and meant for everybody -- not tomorrow's, not an expired one, and not
	# the special price of one customer, supplier or batch (ERPNext's own get_item_price leaves those out too)
	today = getdate()
	prices = [
		p for p in prices
		if not (p.customer or p.supplier or p.batch_no)
		and (not p.valid_from or getdate(p.valid_from) <= today)
		and (not p.valid_upto or getdate(p.valid_upto) >= today)
	]
	# a price for the label's UOM first, then one with no UOM
	price = next((p for p in prices if uom and p.uom == uom), None) or next((p for p in prices if not p.uom), None)
	if not price:
		return None
	symbol = frappe.db.get_value("Currency", price.currency, "symbol") or price.currency
	amount = flt(price.price_list_rate)
	plain = fmt_money(amount, currency=price.currency)
	return frappe._dict(
		amount=amount,
		currency=price.currency,
		english=f"{price.currency} {plain.replace(symbol, '').strip()}" if symbol else plain,
		arabic=f"{plain.replace(symbol, '').strip().translate(_ARABIC_DIGITS)} {symbol}",
	)


@frappe.whitelist()
def available_qty(item_code: str) -> dict:
	"""The stock the Number of Labels dialog shows: in the user's default warehouse, or in all of them."""
	frappe.has_permission("Item", "read", item_code, throw=True)
	frappe.has_permission("Bin", "read", throw=True)
	warehouse = frappe.defaults.get_user_default("Warehouse")
	filters = {"item_code": item_code}
	if warehouse:
		filters["warehouse"] = warehouse
	qty = sum(flt(b.actual_qty) for b in frappe.get_all("Bin", filters=filters, fields=["actual_qty"]))
	return {"qty": qty, "warehouse": warehouse}


def _copies() -> int:
	"""How many labels the print URL asks for: 1 unless `copies` says more, never above MAX_COPIES."""
	return min(max(cint(frappe.form_dict.get("copies")) or 1, 1), MAX_COPIES)


def label_context(doc) -> frappe._dict:
	"""What the Item Label print format shows. Refuses an item that gets no label."""
	if not labelled(doc):
		frappe.throw(_("Labels are printed only for stock items and fixed assets, not for {0}.").format(frappe.bold(doc.name)))
	# the item's Item Code when it is one of its barcodes (it is, for an item made or imported here), else the
	# first barcode it carries: an item that already had a supplier's EAN gets no Item Code row (item_barcode.py),
	# and a sticker encoding a code no barcode row holds would not scan as this item at the till
	barcodes = [b.barcode for b in (doc.get("barcodes") or []) if b.barcode]
	# (compared as the column is, case-insensitively)
	code = doc.name if not barcodes or doc.name.lower() in {b.lower() for b in barcodes} else barcodes[0]
	return frappe._dict(
		copies=_copies(),
		uom=doc.stock_uom,
		price=selling_price(doc.name, doc.stock_uom),
		barcode=barcode_svg(code),
		code=code,
		name=doc.item_name or doc.name,
	)
