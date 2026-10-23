# Copyright (c) 2026, Enfono Technologies and contributors
# For license information, please see license.txt

"""PO Stock Count Variance: the physical stock counted on each Purchase Order row against the system stock.

One line per order row, from the fields sf_trading/po_stock_count.py keeps on it: the system stock in the
row's warehouse when the order was saved / submitted, the count typed by the buyer, the variance (physical -
system) and that variance as a share of the system stock. "Rows" picks the counted rows (default), the stock
rows not counted -- to see who skips the count -- or both. Submitted orders by default; drafts on request.
"Raised By" is whoever made the order, the buyer who counted.

View: Item Rows (one line per order row) or Document Rows -- the same rows folded to one line per order: how
many stock rows it has, how many were counted, how many show a variance, and the shortage / excess they add up to.
"""

import frappe
from frappe import _
from frappe.utils import cint, flt

from sf_trading.po_stock_count import COUNT, SYSTEM, VARIANCE

ITEM_ROWS = "Item Rows"
DOCUMENT_ROWS = "Document Rows"
COUNTED_ROWS = "Counted"
NOT_COUNTED_ROWS = "Not Counted"
ALL_ROWS = "All"


def execute(filters=None):
	filters = frappe._dict(filters or {})
	if not filters.company:
		frappe.throw(_("Pick a Company."))
	if filters.from_date and filters.to_date and filters.from_date > filters.to_date:
		frappe.throw(_("From Date cannot be after To Date."))
	rows = data(filters)
	if filters.view == DOCUMENT_ROWS:
		# the item rows folded, never a second query: both views count the same rows
		return document_columns(), fold_to_documents(rows)
	return columns(), rows


def document_columns():
	return [
		{"fieldname": "purchase_order", "label": _("Purchase Order"), "fieldtype": "Link", "options": "Purchase Order", "width": 150},
		{"fieldname": "transaction_date", "label": _("Date"), "fieldtype": "Date", "width": 95},
		{"fieldname": "status", "label": _("Status"), "fieldtype": "Data", "width": 90},
		{"fieldname": "supplier", "label": _("Supplier"), "fieldtype": "Link", "options": "Supplier", "width": 180},
		{"fieldname": "stock_rows", "label": _("Stock Rows"), "fieldtype": "Int", "width": 95},
		{"fieldname": "counted_rows", "label": _("Counted"), "fieldtype": "Int", "width": 85},
		{"fieldname": "not_counted_rows", "label": _("Not Counted"), "fieldtype": "Int", "width": 100},
		{"fieldname": "variance_rows", "label": _("Rows With Variance"), "fieldtype": "Int", "width": 130},
		{"fieldname": "shortage", "label": _("Total Shortage"), "fieldtype": "Float", "width": 115},
		{"fieldname": "excess", "label": _("Total Excess"), "fieldtype": "Float", "width": 110},
		{"fieldname": "raised_by", "label": _("Raised By"), "fieldtype": "Link", "options": "User", "width": 160},
	]


def fold_to_documents(rows) -> list:
	"""One line per order, in the order the item rows came. Shortage and excess are in each item's Stock UOM."""
	orders = {}
	for row in rows:
		doc = orders.get(row.purchase_order)
		if not doc:
			doc = orders[row.purchase_order] = frappe._dict(
				purchase_order=row.purchase_order, transaction_date=row.transaction_date, status=row.status,
				supplier=row.supplier, raised_by=row.raised_by, stock_rows=0, counted_rows=0, not_counted_rows=0,
				variance_rows=0, shortage=0.0, excess=0.0,
			)
		doc.stock_rows += 1
		if row.physical_count is None:
			doc.not_counted_rows += 1
			continue
		doc.counted_rows += 1
		variance = flt(row.variance)
		if variance:
			doc.variance_rows += 1
		if variance < 0:
			doc.shortage += -variance
		elif variance > 0:
			doc.excess += variance
	return list(orders.values())


def columns():
	return [
		{"fieldname": "purchase_order", "label": _("Purchase Order"), "fieldtype": "Link", "options": "Purchase Order", "width": 150},
		{"fieldname": "transaction_date", "label": _("Date"), "fieldtype": "Date", "width": 95},
		{"fieldname": "status", "label": _("Status"), "fieldtype": "Data", "width": 90},
		{"fieldname": "supplier", "label": _("Supplier"), "fieldtype": "Link", "options": "Supplier", "width": 160},
		{"fieldname": "item_code", "label": _("Item Code"), "fieldtype": "Link", "options": "Item", "width": 130},
		{"fieldname": "item_name", "label": _("Item Name"), "fieldtype": "Data", "width": 200},
		{"fieldname": "item_group", "label": _("Item Group"), "fieldtype": "Link", "options": "Item Group", "width": 120},
		{"fieldname": "warehouse", "label": _("Warehouse"), "fieldtype": "Link", "options": "Warehouse", "width": 140},
		{"fieldname": "stock_uom", "label": _("Stock UOM"), "fieldtype": "Link", "options": "UOM", "width": 80},
		{"fieldname": "system_stock", "label": _("System Stock"), "fieldtype": "Float", "width": 110},
		{"fieldname": "physical_count", "label": _("Physical Count"), "fieldtype": "Float", "width": 110},
		{"fieldname": "variance", "label": _("Variance"), "fieldtype": "Float", "width": 100},
		{"fieldname": "variance_percent", "label": _("Variance %"), "fieldtype": "Percent", "width": 95},
		{"fieldname": "order_qty", "label": _("Ordered Qty (Stock UOM)"), "fieldtype": "Float", "width": 120},
		{"fieldname": "raised_by", "label": _("Raised By"), "fieldtype": "Link", "options": "User", "width": 160},
	]


CHUNK = 500


def _non_stock(item_codes) -> set:
	codes, found = list(item_codes), set()
	for i in range(0, len(codes), CHUNK):
		found.update(frappe.get_all("Item", filters={"name": ["in", codes[i : i + CHUNK]], "is_stock_item": 0}, pluck="name"))
	return found


def data(filters) -> list:
	"""frappe.get_list over the order with its row fields: Purchase Order permissions and the user's user
	permissions apply, as on the Purchase Order list."""
	row = "`tabPurchase Order Item`"
	conditions = [
		["Purchase Order", "company", "=", filters.company],
		["Purchase Order", "docstatus", "in", [0, 1] if cint(filters.include_drafts) else [1]],
		["Purchase Order Item", "delivered_by_supplier", "=", 0],
		["Purchase Order Item", "warehouse", "is", "set"],
	]
	if filters.from_date and filters.to_date:
		conditions.append(["Purchase Order", "transaction_date", "between", [filters.from_date, filters.to_date]])
	if filters.supplier:
		conditions.append(["Purchase Order", "supplier", "=", filters.supplier])
	if filters.purchase_order:
		conditions.append(["Purchase Order", "name", "=", filters.purchase_order])
	for field in ("item_code", "item_group", "warehouse"):
		if filters.get(field):
			conditions.append(["Purchase Order Item", field, "=", filters.get(field)])
	rows_wanted = filters.rows or COUNTED_ROWS
	# blank is "not counted"; 0 is a count
	if rows_wanted == COUNTED_ROWS or cint(filters.only_variance):
		conditions.append(["Purchase Order Item", COUNT, "is", "set"])
	elif rows_wanted == NOT_COUNTED_ROWS:
		conditions.append(["Purchase Order Item", COUNT, "is", "not set"])
	if cint(filters.only_variance):
		conditions.append(["Purchase Order Item", VARIANCE, "!=", 0])

	lines = frappe.get_list(
		"Purchase Order",
		filters=conditions,
		fields=[
			"name as purchase_order", "transaction_date", "docstatus", "supplier", "owner as raised_by",
			f"{row}.item_code", f"{row}.item_name", f"{row}.item_group", f"{row}.warehouse", f"{row}.stock_uom",
			f"{row}.stock_qty as order_qty", f"{row}.{SYSTEM} as system_stock", f"{row}.{COUNT} as physical_count",
			f"{row}.{VARIANCE} as variance",
		],
		order_by=f"`tabPurchase Order`.transaction_date desc, `tabPurchase Order`.name asc, {row}.idx asc",
		limit_page_length=0,
	)
	# a service / non-stock item has nothing on a shelf to count
	skip = _non_stock({line.item_code for line in lines if line.item_code})

	result = []
	for line in lines:
		if line.item_code in skip:
			continue
		line.status = _("Draft") if line.docstatus == 0 else _("Submitted")
		if (line.physical_count or "").strip():
			line.physical_count = flt(line.physical_count)
			line.variance_percent = flt(line.variance) / flt(line.system_stock) * 100 if flt(line.system_stock) else None
		else:
			# not counted: blanks, not 0s that read as an empty shelf (and an order raised before this field
			# existed never recorded its system stock either)
			line.system_stock = line.physical_count = line.variance = line.variance_percent = None
		result.append(line)
	return result
