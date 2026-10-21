# sf_trading/api/purchase_order_invoice.py
"""An invoice raised from an order nobody has received posts the stock itself.

Two ways a purchase reaches the ledger here. Goods arrive on a Purchase Receipt and the invoice
bills what was received -- 1,101 invoices on production do that, posting to Stock Received But Not
Billed. Or nothing was received and the invoice IS the arrival, which needs `update_stock` and
posts straight to Trading Inventory -- 99 invoices do that.

The second shape had to be remembered by hand on every invoice, and forgetting it leaves the goods
priced and payable but absent from stock until somebody notices. So an invoice mapped from an order
against which NOTHING has been received arrives with the box already ticked.

Ticking happens at mapping time and never on save: `before_validate` would re-tick it every time
the document was saved, so a buyer who unticked it could never make that stick. Here the invoice
simply opens ticked and anybody may untick it.

UNticking is the opposite case and does run on save, in
`untick_stock_when_the_goods_already_arrived`. Turning the box off when the goods are demonstrably
already in the warehouse takes nothing away from the buyer -- the alternative is stock counted
twice, or core's own refusal at submit -- and a draft raised before the receipt arrived has to be
corrected at the moment it is saved again, not at the moment it was created.

Only Administrator may leave the box off an invoice that IS the goods' arrival
(`require_stock_update`, 2026-10-20): PI 20020000419 was approved with the box turned off and the stock
only reached the warehouse the next day, when Administrator raised a receipt for the order. For anyone else
the box is ticked and locked on such an invoice; Administrator can still untick it, and an invoice whose
goods are demonstrably in already (a receipt, a part-received order) is never re-ticked.

It never ticks when a receipt exists -- not a submitted one, not a draft, and not a row already
linked on the invoice itself -- because the goods would then be counted into stock twice: once by
the receipt and once by the invoice. Nor when the order is Closed or Cancelled, when anything has
already been received by any route, when the goods are drop-shipped, or when an item is batch- or
serial-tracked: in each of those a ticked box turns an invoice that submits today into one that
refuses.
"""

import frappe
from frappe import _
from frappe.utils import cint, flt


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


def _orders_are_eligible(orders) -> bool:
	"""Every named order must be live and have had nothing arrive against it, by any route.

	`per_received` is the one field BOTH routes move -- a Purchase Receipt and an earlier ticked
	invoice alike (erpnext moves it from purchase_invoice.py only when update_stock is set), so it
	is the honest test for "nothing has arrived yet". A receipt check alone misses an order already
	part-received by an earlier ticked invoice: one such order exists on production today.

	Closed and Cancelled orders are refused because a ticked invoice reaches
	`update_ordered_and_reserved_qty`, which throws "Purchase Order X is cancelled or closed" at
	submit (erpnext/controllers/buying_controller.py:758). A Purchase Invoice runs no such check of
	its own, so without this gate a Closed order that submits fine today would start refusing.
	"""
	for order in frappe.get_all(
		"Purchase Order",
		filters={"name": ("in", list(orders))},
		fields=["name", "status", "per_received"],
	):
		if order.status in ("Closed", "Cancelled"):
			return False
		if flt(order.per_received) > 0:
			return False
	return True


def _is_drop_ship(row) -> bool:
	"""Goods that go straight from supplier to customer never enter a warehouse here.

	ERPNext keeps their expense head off the inventory account (purchase_invoice.py:470-479) while
	the PO-to-PI mapper still copies the row, so a ticked invoice would post stock that will never
	exist and no receipt will ever arrive to stop it.
	"""
	if row.get("delivered_by_supplier"):
		return True
	if row.get("po_detail"):
		return bool(frappe.db.get_value("Purchase Order Item", row.po_detail, "delivered_by_supplier"))
	return False


def _stock_rows(invoice) -> list:
	"""Rows this invoice would actually put into a warehouse."""
	rows = []
	for row in invoice.get("items") or []:
		if not row.get("item_code"):
			continue
		if not cint(frappe.get_cached_value("Item", row.item_code, "is_stock_item")):
			continue
		if _is_drop_ship(row):
			continue
		rows.append(row)
	return rows


def _is_tracked(item_code) -> bool:
	"""Batch- or serial-tracked items are left to the buyer.

	A ticked invoice must carry a Serial and Batch Bundle for them, which nobody entered, so an
	invoice that saves today would stop saving. No stock item on this site is tracked at present;
	the gate is here so that changing one Item does not quietly break the buying flow.
	"""
	batch, serial = frappe.get_cached_value("Item", item_code, ["has_batch_no", "has_serial_no"])
	return bool(cint(batch) or cint(serial))


def set_update_stock(invoice) -> bool:
	"""Decide `update_stock`: on when this invoice IS the goods' arrival, off when it is not.

	It answers both ways on purpose. The site carries a hand-made Property Setter defaulting
	`update_stock` to 1 on every Purchase Invoice (created 2026-09-10 through Customize Form), so
	a function that could only ever tick the box left the dangerous case ticked: an invoice mapped
	from an order whose goods had already arrived on a separate receipt. Core does not catch that
	one -- `validate_purchase_receipt_if_update_stock`
	(erpnext/accounts/doctype/purchase_invoice/purchase_invoice.py:750) only throws when the ROW
	itself names a `purchase_receipt`, which an order-mapped row does not -- so the invoice submits
	and receives the same goods a second time. PUR-ORD-2026-00310 was exactly that, fully received
	on MAT-PRE-2026-01392 and still offering a ticked invoice.
	"""
	if cint(invoice.get("is_return")):
		return False

	orders = {row.purchase_order for row in invoice.get("items") or [] if row.get("purchase_order")}
	if not orders:
		# nothing to judge by: a direct invoice keeps whatever default the site set
		return False

	if not _orders_are_eligible(orders) or _has_a_receipt(orders, invoice):
		# the goods are already in stock. Ticking here would receive them twice -- and a partly
		# received order is no different, because `update_stock` posts EVERY row of the invoice,
		# not just the quantity still outstanding. What arrived belongs to its receipt; what has
		# not arrived yet belongs to the next one.
		invoice.update_stock = 0
		return False

	if cint(invoice.get("update_stock")):
		# already on, and nothing above asked for it to come off
		return False

	rows = _stock_rows(invoice)
	if not rows:
		# a service-only or drop-ship bill has no stock to post
		return False

	if any(_is_tracked(row.item_code) for row in rows):
		return False

	# Every stock row needs somewhere to put the goods. ERPNext refuses the invoice at validate
	# otherwise ("Warehouse required"), and refusing at the moment of creation with a ticked box
	# nobody asked for would be a worse first impression than leaving it off.
	if any(not row.get("warehouse") for row in rows):
		return False

	invoice.update_stock = 1
	return True


def untick_stock_when_the_goods_already_arrived(doc, method=None):
	"""before_validate: an invoice cannot post stock that a receipt has already posted.

	The mapper decides the box at creation, but a draft outlives that moment: an invoice raised
	while nothing had arrived, then saved again after the receipt was booked, is still ticked and
	would receive everything twice. And the site's Property Setter defaults the box ON for every
	invoice, including one built with Get Items From -> Purchase Receipt.

	Core refuses the second shape at validate (`validate_purchase_receipt_if_update_stock`,
	erpnext/accounts/doctype/purchase_invoice/purchase_invoice.py:750) with a message asking the
	buyer to untick a box they never ticked, and does not refuse the first shape at all. Unticking
	is the only answer either document can have, so it is given here instead of an error.
	"""
	if cint(doc.get("is_return")) or not cint(doc.get("update_stock")):
		return

	rows = doc.get("items") or []
	# an invoice made from a Goods In IS the goods' arrival: the Goods In recorded them without
	# stock, so this invoice has to take them in, however much of the order arrived before it
	# (sf_trading/goods_in.py)
	if rows and all(row.get("custom_goods_in_item") for row in rows):
		return
	if any(row.get("purchase_receipt") or row.get("pr_detail") for row in rows):
		message = _(
			"Update Stock has been turned off: this invoice bills a Purchase Receipt, which has "
			"already taken the goods into stock."
		)
	else:
		orders = {row.purchase_order for row in rows if row.get("purchase_order")}
		if not orders or (_orders_are_eligible(orders) and not _has_a_receipt(orders, doc)):
			return
		message = _(
			"Update Stock has been turned off: the goods on this order have already been received, "
			"so ticking it would take them into stock a second time."
		)

	doc.update_stock = 0
	frappe.msgprint(message, indicator="orange", alert=True)


def is_the_arrival(invoice) -> bool:
	"""Whether this invoice is what takes its goods into stock: the box has to be ticked for them to arrive.

	True for an invoice of stock items that no receipt stands behind. False for a return or an opening
	entry, a bill with no stock row (service, drop-ship), a row already linked to a Purchase Receipt, an
	order that is closed, part-received or has a receipt (even a draft one) -- the same tests the
	un-ticking hook uses, so the two can never disagree -- and batch- or serial-tracked items, which need
	a bundle nobody entered. An invoice made from a Goods In is the arrival by definition.
	"""
	if cint(invoice.get("is_return")) or invoice.get("is_opening") == "Yes":
		return False
	if not _stock_rows(invoice):
		return False
	rows = invoice.get("items") or []
	if rows and all(row.get("custom_goods_in_item") for row in rows):
		return True
	if any(row.get("purchase_receipt") or row.get("pr_detail") for row in rows):
		return False
	orders = {row.purchase_order for row in rows if row.get("purchase_order")}
	if orders and (not _orders_are_eligible(orders) or _has_a_receipt(orders, invoice)):
		return False
	return not any(_is_tracked(row.item_code) for row in _stock_rows(invoice))


def _still_to_be_submitted(doc) -> bool:
	"""A draft, or a draft being approved: the approver's save arrives already marked submitted, with the draft
	as the document before the save -- and that save is where PI 20020000419 lost its box."""
	if cint(doc.get("docstatus")) == 0:
		return True
	before = doc.get_doc_before_save() if hasattr(doc, "get_doc_before_save") else None
	return cint(doc.get("docstatus")) == 1 and before is not None and cint(before.get("docstatus")) == 0


def require_stock_update(doc, method=None):
	"""before_validate, after the un-ticking hook: only Administrator leaves Update Stock off an invoice that IS
	the goods' arrival. Everyone else's draft is ticked again as it is saved, approved or submitted."""
	if frappe.session.user == "Administrator" or cint(doc.get("update_stock")):
		return
	if not _still_to_be_submitted(doc) or not is_the_arrival(doc):
		return
	doc.update_stock = 1
	frappe.msgprint(
		_("Update Stock has been turned on: this invoice brings the goods into stock, and only Administrator can leave it off."),
		indicator="orange",
		alert=True,
	)


@frappe.whitelist()
def update_stock_rule(doc) -> dict:
	"""For the invoice form: whether Update Stock is locked on for this (possibly unsaved) invoice."""
	frappe.has_permission("Purchase Invoice", "read", throw=True)
	invoice = frappe.get_doc(frappe.parse_json(doc))
	return {"locked": bool(invoice.doctype == "Purchase Invoice" and is_the_arrival(invoice))}


@frappe.whitelist()
def make_purchase_invoice(source_name, target_doc=None, args=None):
	"""ERPNext's own mapper, with the stock box ticked when the goods have not arrived yet.

	Registered in hooks.py under `override_whitelisted_methods`, so every caller of the core
	method -- the Create menu, the API, another app -- gets the same answer.
	"""
	from erpnext.buying.doctype.purchase_order.purchase_order import (
		make_purchase_invoice as core_make_purchase_invoice,
	)

	from sf_trading.goods_in import link_invoice_to_goods_in

	invoice = core_make_purchase_invoice(source_name, target_doc=target_doc, args=args)
	# goods recorded on a Goods In come into stock with this invoice: it carries those Goods In
	# rows with Update Stock on, and nothing else (sf_trading/goods_in.py)
	if link_invoice_to_goods_in(invoice):
		return invoice
	try:
		set_update_stock(invoice)
	except Exception:
		# a mapper that dies leaves the buyer with no invoice at all; the box is not worth that
		frappe.log_error(frappe.get_traceback(), "sf_trading: update_stock default on PI from PO")
	return invoice
