# sf_trading/goods_in.py
"""Goods In: goods physically received against a Purchase Order, recorded for quantity only.

The client (BRD "Custom In Qty / Goods Received"): goods often arrive before the supplier's
invoice. A Purchase Receipt would put them into stock and value them before the bill exists, so
they record the arrival on a Goods In instead -- no Stock Ledger Entry, no GL Entry -- and the
goods enter stock later through the Purchase Invoice made from that Goods In, with Update Stock.

    Purchase Order -> Goods In -> Purchase Invoice (Update Stock)

Goods arrive by one route only (the client's rule): a Purchase Order row's quantity can be brought
in by a Purchase Receipt, a stock-updating Purchase Invoice, or a Goods In, and never twice. So for
each PO row

    open Goods In   = Goods In qty not yet turned into a Purchase Invoice
    free to receive = ordered - received (PR / stock-updating PI) - open Goods In

a Goods In may take at most "free to receive", and a Purchase Receipt may not take what an open
Goods In already holds: that quantity comes into stock only through a Purchase Invoice with Update
Stock. That invoice can be made from the Goods In, or straight from the Purchase Order -- then it
picks up the order's open Goods In rows by itself (link_invoice_to_goods_in). Either way each
invoice row carries the Goods In row it came from (Purchase Invoice Item custom_goods_in /
custom_goods_in_item), which moves that row's Invoiced Qty and Purchase Invoice when the invoice is
submitted or cancelled. A row added by hand, linked to no Goods In, still may not take what a Goods
In holds.

Purchase Receipt itself is untouched for any quantity no Goods In holds.
"""

from __future__ import annotations

import frappe
from frappe import _
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields
from frappe.query_builder.functions import Max, Sum
from frappe.utils import flt

GOODS_IN_FIELD = "custom_goods_in"
GOODS_IN_ROW_FIELD = "custom_goods_in_item"


def ensure_custom_fields():
	create_custom_fields(
		{
			"Purchase Invoice Item": [
				{
					"fieldname": GOODS_IN_FIELD,
					"label": "Goods In",
					"fieldtype": "Link",
					"options": "Goods In",
					"insert_after": "po_detail",
					"read_only": 1,
					"search_index": 1,
				},
				{
					"fieldname": GOODS_IN_ROW_FIELD,
					"label": "Goods In Item",
					"fieldtype": "Data",
					"insert_after": GOODS_IN_FIELD,
					"read_only": 1,
					"hidden": 1,
					"search_index": 1,
				},
			]
		},
		update=True,
	)


# ── quantities ────────────────────────────────────────────────────────────────


def open_goods_in_qty(po_items, exclude_goods_in=None) -> dict:
	"""PO row -> Goods In qty submitted against it and not yet invoiced."""
	if not po_items:
		return {}
	gi = frappe.qb.DocType("Goods In")
	row = frappe.qb.DocType("Goods In Item")
	query = (
		frappe.qb.from_(row)
		.inner_join(gi)
		.on(gi.name == row.parent)
		.select(row.purchase_order_item, Sum(row.qty - row.invoiced_qty))
		.where((gi.docstatus == 1) & row.purchase_order_item.isin(list(po_items)))
		.groupby(row.purchase_order_item)
	)
	if exclude_goods_in:
		query = query.where(gi.name != exclude_goods_in)
	return {po_item: max(flt(qty), 0) for po_item, qty in query.run()}


def po_rows(po_items) -> dict:
	if not po_items:
		return {}
	return {
		r.name: r
		for r in frappe.get_all(
			"Purchase Order Item",
			filters={"name": ["in", list(po_items)]},
			fields=["name", "parent", "item_code", "qty", "received_qty", "uom", "warehouse"],
		)
	}


def free_to_receive(po_items, exclude_goods_in=None) -> dict:
	"""PO row -> qty not yet brought in by any route."""
	rows = po_rows(po_items)
	held = open_goods_in_qty(po_items, exclude_goods_in)
	return {
		name: max(flt(r.qty) - flt(r.received_qty) - flt(held.get(name)), 0) for name, r in rows.items()
	}


# ── Purchase Receipt / Purchase Invoice straight from the Purchase Order ─────


def validate_receipt_against_goods_in(doc, _method=None):
	"""Purchase Receipt and Purchase Invoice validate.

	A row made straight from a Purchase Order may not take what an open Goods In already holds for
	that row -- neither receive it (Purchase Receipt, stock-updating Purchase Invoice) nor bill it
	(any Purchase Invoice): that quantity is received AND billed by the invoice made from the Goods
	In, or the Goods In would stay open forever and the goods be billed twice. A row made from a
	Goods In may not invoice more than that Goods In row still has open."""
	if doc.get("is_return"):
		return

	if doc.doctype == "Purchase Invoice":
		validate_invoice_from_goods_in(doc)
		# a row billing a Purchase Receipt bills goods that came in by that receipt, not by Goods In
		direct = [i for i in doc.items if i.get("po_detail") and not i.get(GOODS_IN_ROW_FIELD) and not i.get("pr_detail")]
		wanted = _sum_by(direct, "po_detail")
		if not wanted:
			return
		held = open_goods_in_qty(wanted)
		if not any(held.values()):
			return
		_refuse_over(wanted, held, billed_qty(wanted), _("billed"))
		if doc.get("update_stock"):
			_refuse_over(wanted, held, {k: flt(r.received_qty) for k, r in po_rows(wanted).items()}, _("received"))
		return

	direct = [i for i in doc.items if i.get("purchase_order_item")]
	wanted = _sum_by(direct, "purchase_order_item")
	if not wanted:
		return
	held = open_goods_in_qty(wanted)
	if any(held.values()):
		_refuse_over(wanted, held, {k: flt(r.received_qty) for k, r in po_rows(wanted).items()}, _("received"))


def _sum_by(items, field) -> dict:
	out = {}
	for item in items:
		out[item.get(field)] = out.get(item.get(field), 0) + flt(item.qty)
	return out


def billed_qty(po_items) -> dict:
	"""PO row -> qty on submitted, non-return Purchase Invoices."""
	pi = frappe.qb.DocType("Purchase Invoice")
	pii = frappe.qb.DocType("Purchase Invoice Item")
	return dict(
		frappe.qb.from_(pii)
		.inner_join(pi)
		.on(pi.name == pii.parent)
		.select(pii.po_detail, Sum(pii.qty))
		.where((pi.docstatus == 1) & (pi.is_return == 0) & pii.po_detail.isin(list(po_items)))
		.groupby(pii.po_detail)
		.run()
	)


def _refuse_over(wanted, held, done, what):
	"""Refuse a PO row asking for more than ordered - already `what` - held by open Goods In."""
	rows = po_rows(wanted)
	for po_item, qty in wanted.items():
		if not held.get(po_item):
			continue
		r = rows[po_item]
		free = max(flt(r.qty) - flt(done.get(po_item)) - flt(held[po_item]), 0)
		if qty > free:
			frappe.throw(
				_("{0} of {1} on Purchase Order {2} is recorded on Goods In and not yet invoiced, so only {3} "
				  "more can be {4} here. Make the Purchase Invoice from that Goods In instead.").format(
					frappe.bold(flt(held[po_item])), frappe.bold(r.item_code), frappe.bold(r.parent), frappe.bold(free), what
				),
				title=_("Already Received on Goods In"),
			)


def validate_invoice_from_goods_in(doc):
	rows = [item for item in doc.items if item.get(GOODS_IN_ROW_FIELD)]
	if not rows:
		return
	if not doc.get("update_stock"):
		frappe.throw(
			_("A Purchase Invoice made from a Goods In brings those goods into stock: tick Update Stock."),
			title=_("Update Stock Required"),
		)

	wanted = {}
	for item in rows:
		wanted[item.get(GOODS_IN_ROW_FIELD)] = wanted.get(item.get(GOODS_IN_ROW_FIELD), 0) + flt(item.qty)
	open_rows = {
		r.name: r
		for r in frappe.get_all(
			"Goods In Item",
			filters={"name": ["in", list(wanted)], "docstatus": 1},
			fields=["name", "parent", "item_code", "qty", "invoiced_qty"],
		)
	}
	for row_name, qty in wanted.items():
		r = open_rows.get(row_name)
		if not r:
			frappe.throw(_("Goods In row {0} is not submitted.").format(row_name))
		# invoiced_qty moves on submit only, so this invoice is not in it yet
		left = flt(r.qty) - flt(r.invoiced_qty)
		if qty > left:
			frappe.throw(
				_("Only {0} of {1} on Goods In {2} is still to be invoiced.").format(
					frappe.bold(left), frappe.bold(r.item_code), frappe.bold(r.parent)
				),
				title=_("More Than Received"),
			)


# ── Purchase Invoice made straight from the Purchase Order ───────────────────


def open_goods_in_rows(po_items) -> dict:
	"""PO row -> its open Goods In rows, oldest first, as (row, parent, qty still to invoice)."""
	if not po_items:
		return {}
	gi = frappe.qb.DocType("Goods In")
	row = frappe.qb.DocType("Goods In Item")
	out = {}
	for name, parent, po_item, left in (
		frappe.qb.from_(row)
		.inner_join(gi)
		.on(gi.name == row.parent)
		.select(row.name, row.parent, row.purchase_order_item, row.qty - row.invoiced_qty)
		.where((gi.docstatus == 1) & row.purchase_order_item.isin(list(po_items)) & (row.qty > row.invoiced_qty))
		.orderby(gi.posting_date)
		.orderby(gi.creation)
		.orderby(row.idx)
		.run()
	):
		out.setdefault(po_item, []).append((name, parent, flt(left)))
	return out


def link_invoice_to_goods_in(invoice) -> bool:
	"""Purchase Order -> Purchase Invoice: when goods of the order sit on an open Goods In, the
	invoice is the one that brings them into stock -- so it carries exactly those Goods In rows
	(oldest first), each linked to its row, with Update Stock on.

	Anything of the order that no Goods In holds is left off this invoice: it either came in by a
	Purchase Receipt already (and is billed from that receipt) or has not arrived, and a ticked
	invoice would post every row it carries. Returns whether the invoice was changed."""
	if invoice.get("is_return"):
		return False
	held = open_goods_in_rows({i.po_detail for i in invoice.items if i.get("po_detail") and not i.get(GOODS_IN_ROW_FIELD)})
	if not held:
		return False

	rows = []
	for item in invoice.items:
		if item.get(GOODS_IN_ROW_FIELD):
			rows.append(item.as_dict(no_default_fields=True))
			continue
		wanted = flt(item.qty)
		for row_name, parent, left in held.get(item.get("po_detail"), []):
			if wanted <= 0:
				break
			take = min(wanted, left)
			new = item.as_dict(no_default_fields=True)
			new.update({"qty": take, "received_qty": take, GOODS_IN_FIELD: parent, GOODS_IN_ROW_FIELD: row_name})
			rows.append(new)
			wanted -= take
	if not rows:
		return False

	invoice.set("items", [])
	for row in rows:
		invoice.append("items", row)
	invoice.update_stock = 1
	invoice.run_method("set_missing_values")
	invoice.run_method("calculate_taxes_and_totals")
	return True


# ── Invoiced Qty on the Goods In ──────────────────────────────────────────────


def update_invoiced_qty(doc, _method=None):
	"""Purchase Invoice on_submit / on_cancel: recompute Invoiced Qty of every Goods In row this
	invoice touches, and those Goods Ins' status."""
	row_names = {item.get(GOODS_IN_ROW_FIELD) for item in doc.items if item.get(GOODS_IN_ROW_FIELD)}
	if not row_names:
		return

	pi = frappe.qb.DocType("Purchase Invoice")
	pii = frappe.qb.DocType("Purchase Invoice Item")
	invoiced = {
		row: (qty, invoice)
		for row, qty, invoice in frappe.qb.from_(pii)
		.inner_join(pi)
		.on(pi.name == pii.parent)
		.select(pii[GOODS_IN_ROW_FIELD], Sum(pii.qty), Max(pi.name))
		.where((pi.docstatus == 1) & (pi.is_return == 0) & pii[GOODS_IN_ROW_FIELD].isin(list(row_names)))
		.groupby(pii[GOODS_IN_ROW_FIELD])
		.run()
	}
	parents = set()
	for row_name in row_names:
		qty, invoice = invoiced.get(row_name, (0, None))
		frappe.db.set_value(
			"Goods In Item",
			row_name,
			{"invoiced_qty": flt(qty), "purchase_invoice": invoice},
			update_modified=False,
		)
		parents.add(frappe.db.get_value("Goods In Item", row_name, "parent"))
	for parent in parents:
		frappe.get_doc("Goods In", parent).set_status(update=True)


# ── Purchase Order Connections ────────────────────────────────────────────────


def purchase_order_dashboard(data):
	"""override_doctype_dashboards: Goods In beside Purchase Receipt in the order's Connections.
	Goods In links the order by `purchase_order`, the dashboard's own fieldname."""
	data = frappe._dict(data or {})
	transactions = data.setdefault("transactions", [])
	for group in transactions:
		items = group.setdefault("items", [])
		if "Goods In" in items:
			return data
	for group in transactions:
		if "Purchase Receipt" in group.get("items", []):
			group["items"].insert(group["items"].index("Purchase Receipt") + 1, "Goods In")
			return data
	transactions.append({"label": _("Goods In"), "items": ["Goods In"]})
	return data
