# Copyright (c) 2026, Enfono Technologies and contributors
# For license information, please see license.txt

"""Goods In: quantity-only receipt against a Purchase Order. See sf_trading/goods_in.py.

A plain Document, deliberately not a StockController: nothing here posts a Stock Ledger Entry or
a GL Entry, so stock in hand, valuation and COGS never move because of it.
"""

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.model.mapper import get_mapped_doc
from frappe.utils import flt

from sf_trading.goods_in import GOODS_IN_FIELD, GOODS_IN_ROW_FIELD, free_to_receive, po_rows


class GoodsIn(Document):
	def validate(self):
		self.validate_purchase_order()
		self.validate_rows()
		self.set_status()

	def on_submit(self):
		self.set_status(update=True)

	def on_cancel(self):
		self.set_status(update=True)

	def validate_purchase_order(self):
		po = frappe.db.get_value(
			"Purchase Order", self.purchase_order, ["docstatus", "status", "supplier", "company"], as_dict=True
		)
		if not po or po.docstatus != 1:
			frappe.throw(_("Purchase Order {0} is not submitted.").format(frappe.bold(self.purchase_order)))
		if po.status in ("Closed", "On Hold") and self.docstatus == 0:
			frappe.throw(_("Purchase Order {0} is {1}.").format(frappe.bold(self.purchase_order), po.status))
		self.supplier = po.supplier
		self.company = po.company

	def validate_rows(self):
		self.items = [row for row in self.items if flt(row.qty) > 0]
		if not self.items:
			frappe.throw(_("Enter a Received / In Qty on at least one row."))

		po_items = {row.purchase_order_item for row in self.items}
		rows = po_rows(po_items)
		free = free_to_receive(po_items, exclude_goods_in=self.name)

		taken = {}
		for row in self.items:
			po_row = rows.get(row.purchase_order_item)
			if not po_row or po_row.parent != self.purchase_order:
				frappe.throw(
					_("Row {0}: {1} is not a row of Purchase Order {2}.").format(
						row.idx, row.item_code, self.purchase_order
					)
				)
			row.item_code = po_row.item_code
			row.uom = po_row.uom
			row.warehouse = row.warehouse or po_row.warehouse
			row.ordered_qty = po_row.qty
			row.pending_qty = free.get(row.purchase_order_item, 0)

			taken[row.purchase_order_item] = taken.get(row.purchase_order_item, 0) + flt(row.qty)
			if taken[row.purchase_order_item] > flt(row.pending_qty):
				frappe.throw(
					_("Row {0}: only {1} of {2} is still to be received on Purchase Order {3} "
					  "(ordered {4}; the rest already came in by Purchase Receipt, Purchase Invoice or another Goods In).").format(
						row.idx,
						frappe.bold(row.pending_qty),
						frappe.bold(row.item_code),
						self.purchase_order,
						row.ordered_qty,
					),
					title=_("More Than Ordered"),
				)

	def set_status(self, update=False):
		if self.docstatus == 0:
			status = "Draft"
		elif self.docstatus == 2:
			status = "Cancelled"
		elif all(flt(row.invoiced_qty) >= flt(row.qty) for row in self.items):
			status = "Completed"
		else:
			status = "To Bill"
		if update:
			self.db_set("status", status, update_modified=False)
		else:
			self.status = status


@frappe.whitelist()
def make_goods_in(source_name, target_doc=None):
	"""Purchase Order -> Goods In, with every row's still-to-receive quantity."""
	frappe.has_permission("Purchase Order", "read", source_name, throw=True)
	free = free_to_receive({d.name for d in frappe.get_doc("Purchase Order", source_name).items})

	def update_item(source, target, source_parent):
		target.ordered_qty = source.qty
		target.pending_qty = free.get(source.name, 0)
		target.qty = target.pending_qty

	doc = get_mapped_doc(
		"Purchase Order",
		source_name,
		{
			"Purchase Order": {
				"doctype": "Goods In",
				"field_map": {"name": "purchase_order"},
				"field_no_map": ["naming_series", "status", "remarks"],
				"validation": {"docstatus": ["=", 1]},
			},
			"Purchase Order Item": {
				"doctype": "Goods In Item",
				"field_map": {"name": "purchase_order_item"},
				"field_no_map": ["qty"],
				"postprocess": update_item,
				"condition": lambda d: free.get(d.name, 0) > 0,
			},
		},
		target_doc,
	)
	if not doc.items:
		frappe.throw(_("Everything on Purchase Order {0} has already been received.").format(source_name))
	return doc


@frappe.whitelist()
def make_purchase_invoice(source_name):
	"""Goods In -> Purchase Invoice with Update Stock: core's own Purchase Order mapping (rates,
	taxes, accounts), narrowed to this Goods In's rows and their still-to-invoice quantity."""
	from erpnext.buying.doctype.purchase_order.purchase_order import get_mapped_purchase_invoice

	frappe.has_permission("Goods In", "read", source_name, throw=True)
	gi = frappe.get_doc("Goods In", source_name)
	if gi.docstatus != 1:
		frappe.throw(_("Goods In {0} is not submitted.").format(source_name))

	open_rows = {}
	for row in gi.items:
		left = flt(row.qty) - flt(row.invoiced_qty)
		if left > 0:
			open_rows.setdefault(row.purchase_order_item, []).append((row, left))
	if not open_rows:
		frappe.throw(_("Everything on Goods In {0} has already been invoiced.").format(source_name))

	invoice = get_mapped_purchase_invoice(gi.purchase_order)
	items = []
	for item in invoice.items:
		for row, left in open_rows.get(item.po_detail, []):
			new = item.as_dict(no_default_fields=True)
			new.update(
				{
					"qty": left,
					"received_qty": left,
					"warehouse": row.warehouse or item.warehouse,
					GOODS_IN_FIELD: gi.name,
					GOODS_IN_ROW_FIELD: row.name,
				}
			)
			items.append(new)
	invoice.set("items", [])
	for item in items:
		invoice.append("items", item)
	invoice.update_stock = 1
	invoice.set_posting_time = 0
	invoice.run_method("set_missing_values")
	invoice.run_method("calculate_taxes_and_totals")
	return invoice
