# sf_trading/po_stock_count.py
"""Physical stock counted item by item while a Purchase Order is raised, held against the system stock.

Before buying, the buyer counts what is actually on the shelf and types it on the order row
(`custom_physical_stock_count`, in the item's Stock UOM). Each save of the draft, and the submit,
puts beside it what the system holds in that row's warehouse (`custom_system_stock`, from the Bin)
and the difference (`custom_stock_variance` = physical - system). Once submitted the three stay as
they were, so the order records the stock as it stood when the purchase was decided. The PO Stock
Count Variance report lists them.

The count is a text field holding a number, not a number field: a count of 0 is a real count (the
shelf is empty, which is often why the order is raised), but a number field cannot be blank -- an
empty one is 0, and ERPNext's item fetch puts 0 in every number field of the row as the item is
picked -- so "not counted" and "counted, none" would look the same. Blank means not counted; anything
typed must be a number, not below 0, and is kept tidied ("3", "2.5").

Whether every row MUST be counted before the order is submitted is SF Trading Settings > "Require
Physical Stock Count on Purchase Orders" (on; unticking it makes the count optional). Only rows that are
stock items going into one of our warehouses need a count: a service / non-stock item has nothing to
count, and a drop-shipped row (delivered by the supplier straight to the customer) never enters ours.
"""

import frappe
from frappe import _
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields
from frappe.custom.doctype.property_setter.property_setter import make_property_setter
from frappe.utils import cint, cstr, flt

from sf_trading.switches import is_on

SETTINGS = "SF Trading Settings"
SWITCH = "require_po_stock_count"
ROW = "Purchase Order Item"
COUNT = "custom_physical_stock_count"
SYSTEM = "custom_system_stock"
VARIANCE = "custom_stock_variance"


def ensure_custom_fields():
	"""after_migrate: the count, the system stock and the variance on the order row, and room for the
	count in the items grid."""
	create_custom_fields(
		{
			ROW: [
				{
					"fieldname": COUNT,
					"label": "Physical Stock Count",
					"fieldtype": "Data",
					# beside the Stock UOM quantity, which is the unit it is counted in
					"insert_after": "stock_qty",
					"in_list_view": 1,
					"columns": 1,
					"no_copy": 1,
					"description": "Counted in the warehouse, in the item's Stock UOM. Enter 0 when the shelf is empty; "
						"leave blank only when not counted.",
				},
				{
					"fieldname": SYSTEM,
					"label": "System Stock at Count",
					"fieldtype": "Float",
					"insert_after": COUNT,
					"read_only": 1,
					"no_copy": 1,
					"depends_on": f"eval:doc.{COUNT}",
					"description": "Stock in this row's warehouse, in Stock UOM, when the order was last saved as a draft or submitted.",
				},
				{
					"fieldname": VARIANCE,
					"label": "Stock Variance",
					"fieldtype": "Float",
					"insert_after": SYSTEM,
					"read_only": 1,
					"no_copy": 1,
					# beside the count in the items grid: the difference, or 0 when there is none
					"in_list_view": 1,
					"columns": 1,
					"depends_on": "",
					"description": "Physical Stock Count minus System Stock at Count; 0 when they agree.",
				},
			]
		},
		ignore_validate=True,
		update=True,
	)
	# The grid shows in_list_view fields until 11 column units are used, and the standard ones already
	# fill them; "Required By" (a date) and Rate are narrowed to one unit each to make room for the
	# count and its variance.
	for fieldname in ("schedule_date", "rate"):
		make_property_setter(ROW, fieldname, "columns", 1, "Int", validate_fields_for_doctype=False)


def required() -> bool:
	# ON unless somebody unticked it (a switch never stored reads as its default -- see switches.py)
	return is_on(SWITCH, default=True)


def counted(row) -> bool:
	return bool(cstr(row.get(COUNT)).strip())


def _stock_items(doc) -> set:
	codes = list({row.item_code for row in doc.items if row.item_code})
	if not codes:
		return set()
	return set(frappe.get_all("Item", filters={"name": ["in", codes], "is_stock_item": 1}, pluck="name"))


def _countable(row, stock_items) -> bool:
	return row.item_code in stock_items and bool(row.warehouse) and not cint(row.delivered_by_supplier)


def _system_stock(rows) -> dict:
	pairs = {(row.item_code, row.warehouse) for row in rows}
	if not pairs:
		return {}
	bins = frappe.get_all(
		"Bin",
		filters={"item_code": ["in", list({p[0] for p in pairs})], "warehouse": ["in", list({p[1] for p in pairs})]},
		fields=["item_code", "warehouse", "actual_qty"],
	)
	return {(b.item_code, b.warehouse): flt(b.actual_qty) for b in bins}


def _tidy_count(row):
	"""The typed count as a number, written back tidied; blank stays blank. A non-number or a negative refuses."""
	raw = cstr(row.get(COUNT)).strip().replace(",", "")
	if not raw:
		row.set(COUNT, None)
		return None
	try:
		value = float(raw)
	except ValueError:
		value = None
	if value is None or value != value or value in (float("inf"), float("-inf")):
		frappe.throw(_("Row {0}: Physical Stock Count must be a number, not {1}.").format(row.idx, frappe.bold(row.get(COUNT))))
	if value < 0:
		frappe.throw(_("Row {0}: Physical Stock Count cannot be below 0.").format(row.idx))
	row.set(COUNT, ("%.6f" % value).rstrip("0").rstrip("."))
	return value


def refresh(doc, _method=None):
	"""Purchase Order validate: the count checked, and the system stock and variance on every row."""
	stock_items = _stock_items(doc)
	countable = [row for row in doc.items if _countable(row, stock_items)]
	countable_ids = {id(row) for row in countable}
	stock = _system_stock(countable)
	for row in doc.items:
		value = _tidy_count(row)
		if id(row) in countable_ids:
			row.set(SYSTEM, stock.get((row.item_code, row.warehouse), 0.0))
			row.set(VARIANCE, value - flt(row.get(SYSTEM)) if value is not None else 0.0)
		else:
			row.set(SYSTEM, 0.0)
			row.set(VARIANCE, 0.0)


def require_count(doc, _method=None):
	"""Purchase Order before_submit: every stock row counted, while SF Trading Settings asks for it."""
	if not required():
		return
	stock_items = _stock_items(doc)
	missing = [row for row in doc.items if _countable(row, stock_items) and not counted(row)]
	if not missing:
		return
	lines = "".join("<li>{0}: {1}</li>".format(_("Row {0}").format(row.idx), frappe.bold(row.item_code)) for row in missing)
	frappe.throw(
		_("Enter the Physical Stock Count (in Stock UOM) on these rows before submitting. Enter 0 if there is none on the shelf.")
		+ f"<ul>{lines}</ul>",
		title=_("Physical Stock Count Required"),
	)
