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
goods are demonstrably in already (a receipt, a received order line) is never re-ticked.

It never ticks when a receipt exists for the goods it bills -- not a submitted one, not a draft, and
not a row already linked on the invoice itself -- because the goods would then be counted into stock
twice: once by the receipt and once by the invoice. Nor when the order is Closed or Cancelled, when
the goods it bills have already been received by any route, when the goods are drop-shipped, or when
an item is batch- or serial-tracked: in each of those a ticked box turns an invoice that submits today
into one that refuses.

"Already received" is judged per order LINE, never per order (2026-10-27). PUR-ORD-2026-00472 was billed
on two invoices: 20020000447 took lines 1 and 2 into stock, which put the order at 50% received, and
20020000448 billed line 3, which nothing had received -- yet the order-wide test unticked it and its
goods were billed into Stock Received But Not Billed with no stock behind them.
"""

from collections import defaultdict

import frappe
from frappe import _
from frappe.utils import cint, flt


def _bills_a_receipt(row) -> bool:
	return bool(row.get("purchase_receipt") or row.get("pr_detail"))


def _received_rows(invoice) -> list:
	"""The rows whose goods another document has already taken into stock, or is about to.

	A row that names a Purchase Receipt, and a stock row whose order line has had anything received
	against it by a receipt -- a draft one too: it is somebody's intention to receive, and an invoice
	that posts the stock first would leave them submitting a duplicate.

	A line received by an earlier ticked invoice instead has been billed as well, so what is left of it
	is still out there: a row is received only when it bills more than the line still awaits
	(`received_qty` is moved by a receipt and by a ticked invoice alike, purchase_invoice.py moves it only
	with update_stock set). Two rows of one line count together.

	Other lines of the same order do not come into it: `update_stock` posts the rows of THIS invoice and
	nothing else. A row that names an order but not the line (typed in, not mapped) can only be judged by
	the order, so it falls back to the order-wide test: anything received against the order at all.
	"""
	items = invoice.get("items") or []
	received = [row for row in items if _bills_a_receipt(row)]
	ordered = [row for row in _stock_rows(invoice) if row.get("purchase_order") and not _bills_a_receipt(row)]
	if not ordered:
		return received

	details = list({row.po_detail for row in ordered if row.get("po_detail")})
	lines, receipted = {}, set()
	if details:
		lines = {
			line.name: line
			for line in frappe.get_all(
				"Purchase Order Item",
				filters={"name": ("in", details)},
				fields=["name", "qty", "received_qty"],
			)
		}
		receipted = set(
			frappe.get_all(
				"Purchase Receipt Item",
				filters={"purchase_order_item": ("in", details), "docstatus": ("<", 2)},
				pluck="purchase_order_item",
			)
		)

	orders = list({row.purchase_order for row in ordered if not row.get("po_detail")})
	received_orders = set()
	if orders:
		received_orders = set(
			frappe.get_all(
				"Purchase Order", filters={"name": ("in", orders), "per_received": (">", 0)}, pluck="name"
			)
		) | set(
			frappe.get_all(
				"Purchase Receipt Item",
				filters={"purchase_order": ("in", orders), "docstatus": ("<", 2)},
				pluck="purchase_order",
			)
		)

	billing = defaultdict(float)
	for row in ordered:
		if row.get("po_detail"):
			billing[row.po_detail] += flt(row.qty)

	for row in ordered:
		detail = row.get("po_detail")
		if not detail:
			if row.purchase_order in received_orders:
				received.append(row)
			continue
		line = lines.get(detail)
		if detail in receipted or (
			line and flt(line.received_qty) > 0 and flt(line.received_qty) + billing[detail] > flt(line.qty) + 0.0001
		):
			received.append(row)
	return received


def _orders_are_live(orders) -> bool:
	"""Closed and Cancelled orders are refused because a ticked invoice reaches
	`update_ordered_and_reserved_qty`, which throws "Purchase Order X is cancelled or closed" at
	submit (erpnext/controllers/buying_controller.py:758). A Purchase Invoice runs no such check of
	its own, so without this gate a Closed order that submits fine today would start refusing.
	"""
	return not frappe.db.exists(
		"Purchase Order", {"name": ("in", list(orders)), "status": ("in", ("Closed", "Cancelled"))}
	)


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

	if not _orders_are_live(orders) or _received_rows(invoice):
		# goods this invoice bills are already in stock. Ticking here would receive them twice -- and
		# one received row is enough, because `update_stock` posts EVERY row of the invoice. What
		# arrived belongs to its receipt; lines nothing has received go on an invoice of their own.
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
	if any(_bills_a_receipt(row) for row in rows):
		message = _(
			"Update Stock has been turned off: this invoice bills a Purchase Receipt, which has "
			"already taken the goods into stock."
		)
	else:
		orders = {row.purchase_order for row in rows if row.get("purchase_order")}
		if not orders:
			return
		if not _orders_are_live(orders):
			message = _(
				"Update Stock has been turned off: the order is Closed or Cancelled, and goods cannot be "
				"taken into stock against it."
			)
		else:
			received = _received_rows(doc)
			if not received:
				return
			message = _(
				"Update Stock has been turned off: {0} on this invoice has already been received against "
				"the order, so ticking it would take it into stock a second time. Bill goods that have not "
				"arrived yet on an invoice of their own."
			).format(", ".join(dict.fromkeys(row.item_code for row in received)))

	doc.update_stock = 0
	frappe.msgprint(message, indicator="orange", alert=True)


def is_the_arrival(invoice) -> bool:
	"""Whether this invoice is what takes its goods into stock: the box has to be ticked for them to arrive.

	True for an invoice of stock items that no receipt stands behind. False for a return or an opening
	entry, a bill with no stock row (service, drop-ship), a row already linked to a Purchase Receipt, a
	closed order, a row whose order line has a receipt (even a draft one) or has been received already --
	the same tests the un-ticking hook uses, so the two can never disagree -- and batch- or serial-tracked
	items, which need a bundle nobody entered. An invoice made from a Goods In is the arrival by definition.
	"""
	if cint(invoice.get("is_return")) or invoice.get("is_opening") == "Yes":
		return False
	if not _stock_rows(invoice):
		return False
	rows = invoice.get("items") or []
	if rows and all(row.get("custom_goods_in_item") for row in rows):
		return True
	if any(_bills_a_receipt(row) for row in rows):
		return False
	orders = {row.purchase_order for row in rows if row.get("purchase_order")}
	if orders and (not _orders_are_live(orders) or _received_rows(invoice)):
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
