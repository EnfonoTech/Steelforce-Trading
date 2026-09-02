# sf_trading/api/purchase_order_invoice.py
"""An invoice raised from an order nobody has received posts the stock itself.

Two ways a purchase reaches the ledger here. Goods arrive on a Purchase Receipt and the invoice
bills what was received -- 1,101 invoices on production do that, posting to Stock Received But Not
Billed. Or nothing was received and the invoice IS the arrival, which needs `update_stock` and
posts straight to Trading Inventory -- 99 invoices do that.

The second shape had to be remembered by hand on every invoice, and forgetting it leaves the goods
priced and payable but absent from stock until somebody notices. So an invoice mapped from an order
against which NOTHING has been received arrives with the box already ticked.

Deliberately at mapping time and not on save. `before_validate` would re-tick it every time the
document was saved, so a buyer who unticked it could never make that stick; here the invoice simply
opens ticked and anybody may untick it.

It never ticks when a receipt exists -- not a submitted one, not a draft, and not a row already
linked on the invoice itself -- because the goods would then be counted into stock twice: once by
the receipt and once by the invoice.
"""

import frappe
from frappe.utils import cint


def _has_a_receipt(orders, invoice) -> bool:
	"""Whether anything has been received against these orders, by any route.

	A draft receipt counts. It is somebody's intention to receive, and an invoice that posts the
	stock first would leave them submitting a duplicate.
	"""
	if any(row.get("purchase_receipt") or row.get("pr_detail") for row in invoice.get("items") or []):
		return True

	return bool(
		frappe.db.exists(
			"Purchase Receipt Item",
			{"purchase_order": ("in", list(orders)), "docstatus": ("<", 2)},
		)
	)


def _stock_rows(invoice) -> list:
	return [
		row
		for row in invoice.get("items") or []
		if row.get("item_code")
		and cint(frappe.get_cached_value("Item", row.item_code, "is_stock_item"))
	]


def set_update_stock(invoice) -> bool:
	"""Tick `update_stock` when this invoice is the goods' first entry into stock."""
	if cint(invoice.get("update_stock")) or cint(invoice.get("is_return")):
		return False

	orders = {row.purchase_order for row in invoice.get("items") or [] if row.get("purchase_order")}
	if not orders:
		return False

	if _has_a_receipt(orders, invoice):
		return False

	rows = _stock_rows(invoice)
	if not rows:
		# a service-only bill has no stock to post
		return False

	# Every stock row needs somewhere to put the goods. ERPNext refuses the invoice at validate
	# otherwise ("Warehouse required"), and refusing at the moment of creation with a ticked box
	# nobody asked for would be a worse first impression than leaving it off.
	if any(not row.get("warehouse") for row in rows):
		return False

	invoice.update_stock = 1
	return True


@frappe.whitelist()
def make_purchase_invoice(source_name, target_doc=None, args=None):
	"""ERPNext's own mapper, with the stock box ticked when the goods have not arrived yet.

	Registered in hooks.py under `override_whitelisted_methods`, so every caller of the core
	method -- the Create menu, the API, another app -- gets the same answer.
	"""
	from erpnext.buying.doctype.purchase_order.purchase_order import (
		make_purchase_invoice as core_make_purchase_invoice,
	)

	invoice = core_make_purchase_invoice(source_name, target_doc=target_doc, args=args)
	try:
		set_update_stock(invoice)
	except Exception:
		# a mapper that dies leaves the buyer with no invoice at all; the box is not worth that
		frappe.log_error(frappe.get_traceback(), "sf_trading: update_stock default on PI from PO")
	return invoice
