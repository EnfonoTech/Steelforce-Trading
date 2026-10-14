# Copyright (c) 2026, Enfono Technologies and contributors
# For license information, please see license.txt

"""Sales Order Stock Drill Down: item by item, warehouse by warehouse, the orders waiting for the stock.

The Sales Order Tracker says an open order has not been invoiced, and why. When the why is about stock --
or when stock is there and the order is still not invoiced -- this is the report to open: it lays the open
orders out the way the stock sees them.

    Item          what is still to be delivered on the open orders, what is on hand in the company's warehouses,
                  what the open orders have reserved of it (the Bin's own figure) and what is free after them
      Warehouse   the same at one warehouse -- the warehouses the orders name, and the others that hold the item
        Order     each open order asking that warehouse for the item, OLDEST FIRST: what it still needs, the
                  stock left after serving it and the orders before it, whether the stock covers it, and why
                  that order has not been invoiced (the tracker's own reason)

Stock Says (item and warehouse rows)
    Enough      what is on hand covers everything the open orders have reserved there
    Short       some is on hand, not enough for every order
    No Stock    nothing on hand

On an order row it is Covered (the stock reaches it after the orders before it), Part (the stock runs out
inside it) or Not Covered (nothing is left when its turn comes). The orders a filter hides still take their
share of the stock when the sequence is worked out, so "left after this order" is the true figure.

Filters: company, item, item group, warehouse, order, customer, branch, Stock Says (on the item), order date
window, and "Only Orders Not Invoiced" (on by default: an order that is billed in full asks for no more
stock). Only the orders that hold stock for a customer (To Deliver and Bill, To Deliver) are read, and only
the lines of items whose stock is kept.
"""

from collections import defaultdict

import frappe
from frappe import _
from frappe.utils import cint, date_diff, flt, getdate, nowdate

from sf_trading import order_stock
from sf_trading.sf_trading.report.sales_order_tracker import sales_order_tracker as tracker

ENOUGH = "Enough"
SHORT = "Short"
NO_STOCK = "No Stock"
#: items with stock for everything come first: those are the orders that could be invoiced today
RANK = {ENOUGH: 0, SHORT: 1, NO_STOCK: 2}
TOLERANCE = order_stock.TOLERANCE

ITEM, WAREHOUSE, ORDER = "Item", "Warehouse", "Order"


def execute(filters=None):
	filters = frappe._dict(filters or {})
	if not filters.get("company"):
		frappe.throw(_("Company is required"))

	today = getdate(nowdate())
	orders = [o for o in tracker.get_orders(filters) if o.status in order_stock.OPEN_FOR_DELIVERY]
	if not filters.get("sales_order") and cint(filters.get("only_not_invoiced", 1)):
		orders = [o for o in orders if flt(o.per_billed, 2) < 100]

	stock = order_stock.load(orders, filters.company)
	reasons = {o.name: order_stock.reason_for(o, stock, today) for o in orders}
	lines = wanted_lines(stock, filters)

	pairs = {(line.item_code, line.warehouse) for line in lines}
	sequence = {}
	for pair, demand in order_stock.open_demand(pairs).items():
		bin_ = stock.bins.get(pair)
		sequence[pair] = order_stock.demand_in_sequence(bin_.actual if bin_ else 0.0, demand)

	rows, counts = build_tree(lines, stock, sequence, {o.name: o for o in orders}, reasons, today, filters)
	return get_columns(), rows, None, None, get_summary(counts)


def wanted_lines(stock, filters) -> list:
	"""The lines to lay out: stock-tracked, still to deliver, and what the item / group / warehouse filters ask for."""
	groups = None
	if filters.get("item_group"):
		lft, rgt = frappe.db.get_value("Item Group", filters.item_group, ["lft", "rgt"]) or (0, 0)
		groups = set(frappe.get_all("Item Group", filters={"lft": [">=", lft], "rgt": ["<=", rgt]}, pluck="name")) or {filters.item_group}
	out = []
	for lines in stock.lines.values():
		for line in lines:
			if not line.tracked:
				continue
			if filters.get("item_code") and line.item_code != filters.item_code:
				continue
			if groups is not None and line.item_group not in groups:
				continue
			if filters.get("warehouse") and line.warehouse != filters.warehouse:
				continue
			out.append(line)
	return out


def stock_says(actual, reserved) -> str:
	"""A warehouse against everything the open orders have reserved of the item there."""
	if flt(actual) <= TOLERANCE:
		return NO_STOCK
	if flt(actual) + TOLERANCE >= flt(reserved):
		return ENOUGH
	return SHORT


def item_says(statuses) -> str:
	"""An item from the warehouses its orders name."""
	statuses = [status for status in statuses if status]
	if statuses and all(status == ENOUGH for status in statuses):
		return ENOUGH
	if statuses and all(status == NO_STOCK for status in statuses):
		return NO_STOCK
	return SHORT


def build_tree(lines, stock, sequence, orders, reasons, today, filters) -> tuple:
	"""The report's rows, parent before children, and the counts the summary shows.

	`lines` are the lines to lay out, `stock` what `order_stock.load` read, `sequence` (item, warehouse) -> every
	open line asking for it company-wide, oldest first, with its stock before / after and whether it is covered,
	`orders` the orders shown by name and `reasons` their (reason, detail, stock check).
	"""
	by_item = defaultdict(lambda: defaultdict(list))
	for line in lines:
		by_item[line.item_code][line.warehouse].append(line)

	trees = []
	for item, by_warehouse in by_item.items():
		first = next(iter(by_warehouse.values()))[0]
		shown = set(by_warehouse)
		if not filters.get("warehouse"):
			shown |= {warehouse for warehouse, count in stock.bins_by_item.get(item, [])
				if warehouse in stock.warehouses and count > TOLERANCE}
		warehouse_rows, statuses = [], []
		totals = defaultdict(float)
		for warehouse in sorted(shown):
			bin_ = stock.bins.get((item, warehouse))
			actual, reserved = (bin_.actual, bin_.reserved) if bin_ else (0.0, 0.0)
			asked = by_warehouse.get(warehouse, [])
			pending = sum(flt(line.pending_stock) for line in asked)
			status = stock_says(actual, reserved) if asked else ""
			statuses.append(status)
			details = {line.name for line in asked}
			order_rows = []
			for entry in sequence.get((item, warehouse), []):
				if entry["detail"] not in details:
					continue
				reason, why, _check = reasons.get(entry["sales_order"], ("", "", ""))
				order = orders.get(entry["sales_order"])
				order_rows.append({
					"node": "%s|%s|%s" % (item, warehouse, entry["detail"]),
					"parent_node": "%s|%s" % (item, warehouse),
					"indent": 2,
					"kind": ORDER,
					"label": entry["sales_order"],
					"sales_order": entry["sales_order"],
					"customer_name": entry.get("customer_name") or (order.customer_name if order else ""),
					"order_date": entry.get("transaction_date"),
					"waiting_days": date_diff(today, entry["transaction_date"]) if entry.get("transaction_date") else 0,
					"pending_qty": flt(entry["pending"], 3),
					"stock_after": flt(entry["stock_after"], 3),
					"stock_status": entry["covered"],
					"why_not_invoiced": reason,
					"why_detail": why,
					"uom": first.stock_uom,
				})
			warehouse_rows.append({
				"node": "%s|%s" % (item, warehouse),
				"parent_node": item,
				"indent": 1,
				"kind": WAREHOUSE,
				"label": warehouse,
				"pending_qty": flt(pending, 3),
				"stock_qty": flt(actual, 3),
				"reserved_qty": flt(reserved, 3),
				"free_qty": flt(actual - reserved, 3),
				"orders": len({line.parent for line in asked}),
				"stock_status": status,
				"uom": first.stock_uom,
			})
			warehouse_rows.extend(order_rows)
			totals["pending"] += pending
			totals["stock"] += actual
			totals["reserved"] += reserved
		status = item_says(statuses)
		if filters.get("stock_status") and status != filters.stock_status:
			continue
		trees.append((RANK[status], item, [{
			"node": item,
			"parent_node": "",
			"indent": 0,
			"kind": ITEM,
			"label": "%s - %s" % (item, first.item_name) if first.item_name and first.item_name != item else item,
			"pending_qty": flt(totals["pending"], 3),
			"stock_qty": flt(totals["stock"], 3),
			"reserved_qty": flt(totals["reserved"], 3),
			"free_qty": flt(totals["stock"] - totals["reserved"], 3),
			"orders": len({line.parent for ls in by_warehouse.values() for line in ls}),
			"stock_status": status,
			"item_group": first.item_group,
			"uom": first.stock_uom,
		}] + warehouse_rows))

	trees.sort(key=lambda tree: (tree[0], tree[1]))
	rows = [row for _rank, _item, tree in trees for row in tree]
	order_rows = [row for row in rows if row["kind"] == ORDER]
	counts = {
		"items": len(trees),
		"orders": len({row["sales_order"] for row in order_rows}),
		"covered": len({row["sales_order"] for row in order_rows if row["stock_status"] == "Covered"}),
	}
	return rows, counts


def get_summary(counts) -> list:
	return [
		{"label": _("Items"), "value": counts["items"], "datatype": "Int", "indicator": "blue"},
		{"label": _("Orders Waiting"), "value": counts["orders"], "datatype": "Int", "indicator": "orange"},
		{"label": _("Orders The Stock Covers"), "value": counts["covered"], "datatype": "Int", "indicator": "green"},
	]


def get_columns() -> list:
	def col(fieldname, label, fieldtype, width=110, options=None):
		return {"fieldname": fieldname, "label": _(label), "fieldtype": fieldtype, "width": width, "options": options}

	return [
		col("label", "Item / Warehouse / Order", "Data", 340),
		col("kind", "Level", "Data", 85),
		col("customer_name", "Customer", "Data", 190),
		col("order_date", "Order Date", "Date", 95),
		col("waiting_days", "Days Waiting", "Int", 90),
		col("pending_qty", "Still To Deliver", "Float", 110),
		col("stock_qty", "Stock On Hand", "Float", 110),
		col("reserved_qty", "Reserved (All Orders)", "Float", 130),
		col("free_qty", "Free After All Orders", "Float", 135),
		col("stock_after", "Left After This Order", "Float", 135),
		col("stock_status", "Stock Says", "Data", 110),
		col("orders", "Orders", "Int", 70),
		col("why_not_invoiced", "Why Not Invoiced", "Data", 175),
		col("why_detail", "Why Not Invoiced - Details", "Data", 380),
		col("item_group", "Item Group", "Link", 130, "Item Group"),
		col("uom", "UOM", "Link", 70, "UOM"),
		{"fieldname": "sales_order", "label": _("Sales Order"), "fieldtype": "Link", "options": "Sales Order", "width": 140, "hidden": 1},
		{"fieldname": "node", "label": _("Node"), "fieldtype": "Data", "width": 10, "hidden": 1},
		{"fieldname": "parent_node", "label": _("Parent Node"), "fieldtype": "Data", "width": 10, "hidden": 1},
	]
