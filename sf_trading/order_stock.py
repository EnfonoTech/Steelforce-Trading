# sf_trading/order_stock.py
"""What stands between an open Sales Order and its invoice, and what the stock behind it says.

Steel Force invoices an order with a Sales Invoice that moves the stock (Update Stock), and negative
stock is not allowed -- so an order is invoiced the moment someone raises the invoice AND the warehouse
holds the goods. The Sales Order Tracker could say that an order was not invoiced; this says why:

* **the stock** at the warehouse each line names: In Stock (the quantity still to deliver is there),
  Part Stock, No Stock, and where the rest sits (another warehouse the user may see);
* **a draft** already made: an invoice or a Delivery Note saved and never submitted;
* **the customer**: whatever the Sales Invoice gates would refuse the customer for (an unapproved credit
  customer, a missing supporting document or phone number), asked of the same gates the party popup asks, so
  the tracker and the refusal can never disagree;
* **the date**: a delivery date that has not come, an order on hold, goods delivered on a Delivery Note and
  never billed;
* and when none of those is the matter: the order is simply waiting for somebody -- "Stock Ready, Not
  Invoiced" is the one to chase.

`why_not_invoiced` returns the first reason that applies (on hold, a draft invoice, a draft Delivery Note, a
delivery date not come, the stock short, the customer blocked, delivered and never billed, then "ready, not
invoiced") and every fact it found as the detail. Nothing here writes anything.

The stock check reads one warehouse per line, the line's own, against that line's quantity alone. Orders
that compete for the same goods are laid out one after the other, oldest first, by `demand_in_sequence`
(the Sales Order Stock Drill Down report).
"""

from __future__ import annotations

from collections import defaultdict

import frappe
from frappe import _
from frappe.utils import cint, cstr, flt, getdate, strip_html

TOLERANCE = 0.0005
CHUNK = 1000

# what the stock at the order's warehouse says about a line, and about an order
IN_STOCK = "In Stock"
PART_STOCK = "Part Stock"
NO_STOCK = "No Stock"

# why an open order has not been invoiced (PART_STOCK / NO_STOCK are the stock check's own words)
ON_HOLD = "On Hold"
DRAFT_INVOICE = "Invoice In Draft"
DRAFT_DELIVERY = "Delivery Note In Draft"
NOT_DUE = "Not Due Yet"
CUSTOMER_BLOCKED = "Customer Blocked"
DELIVERED_NOT_BILLED = "Delivered, Not Billed"
STOCK_READY = "Stock Ready, Not Invoiced"
NOT_INVOICED = "Not Invoiced Yet"

REASONS = (STOCK_READY, DRAFT_INVOICE, DRAFT_DELIVERY, NO_STOCK, PART_STOCK, CUSTOMER_BLOCKED,
	DELIVERED_NOT_BILLED, NOT_DUE, ON_HOLD, NOT_INVOICED)

#: the reasons somebody has to act on today. An order on hold, or not due yet, is waiting on purpose; No Stock /
#: Part Stock wait for the warehouse and purchasing (they have a next step too, but nobody can invoice yet)
ACTIONABLE = (STOCK_READY, DRAFT_INVOICE, DRAFT_DELIVERY, CUSTOMER_BLOCKED, DELIVERED_NOT_BILLED, NOT_INVOICED)

#: statuses of an order that has nothing left to invoice or cannot be
FINISHED = ("Closed", "Completed", "Cancelled", "Draft")
#: statuses that still hold stock for the customer
OPEN_FOR_DELIVERY = ("To Deliver and Bill", "To Deliver")

#: how many lines of a stock note are spelled out
NOTE_LINES = 3


def qty(value) -> str:
	"""A quantity as people write it: 22, 0.5, 38.175."""
	text = "%.3f" % flt(value)
	return text.rstrip("0").rstrip(".") if "." in text else text


def chunks(names):
	names = list(names)
	for i in range(0, len(names), CHUNK):
		yield names[i:i + CHUNK]


# ─── the rules, on plain data ────────────────────────────────────────────────────────────────────


def needs_reason(order) -> bool:
	"""An order that still has something to invoice."""
	return order.status not in FINISHED and flt(order.per_billed, 2) < 100


def line_state(pending, actual) -> str:
	"""IN_STOCK, PART_STOCK or NO_STOCK: `pending` still to deliver against `actual` on hand ("" when
	nothing is to be delivered)."""
	if flt(pending) <= TOLERANCE:
		return ""
	if flt(actual) + TOLERANCE >= flt(pending):
		return IN_STOCK
	if flt(actual) > TOLERANCE:
		return PART_STOCK
	return NO_STOCK


def stock_check(states) -> str:
	"""What the stock says about a whole order, from its lines' states."""
	states = [state for state in states if state]
	if not states:
		return ""
	if all(state == IN_STOCK for state in states):
		return IN_STOCK
	if all(state == NO_STOCK for state in states):
		return NO_STOCK
	return PART_STOCK


def stock_note(lines) -> str:
	"""The lines the stock does not cover, in words: "FLAT BAR: 22 of 40 at SFSB (also 31 in SFSS)"."""
	short = [line for line in lines if line.get("tracked") and line.get("state") in (PART_STOCK, NO_STOCK)]
	parts = []
	for line in short[:NOTE_LINES]:
		text = _("%s: %s of %s at %s") % (line.item_code, qty(line.actual), qty(line.pending_stock), line.warehouse)
		elsewhere = line.get("elsewhere") or {}
		if elsewhere:
			text += " (" + _("also %s") % ", ".join("%s in %s" % (qty(count), warehouse)
				for warehouse, count in sorted(elsewhere.items(), key=lambda kv: -kv[1])[:2]) + ")"
		parts.append(text)
	if len(short) > NOTE_LINES:
		parts.append(_("and %s more line(s)") % (len(short) - NOTE_LINES))
	return "; ".join(parts)


def warehouses_of(lines) -> str:
	"""The warehouses an order still has goods to deliver from, as one text: "SFSB - SFB, SFSS - SFB"."""
	return ", ".join(sorted({line.warehouse for line in lines if line.get("warehouse")}))


def next_step(reason, lines=()) -> str:
	"""What the team does about an order that has this reason, in a sentence ("" when there is no reason)."""
	if reason == STOCK_READY:
		return _("Raise the Sales Invoice now: the stock is there")
	if reason == DRAFT_INVOICE:
		return _("Open the draft invoice and submit it")
	if reason == DRAFT_DELIVERY:
		return _("Submit the draft Delivery Note, then raise the invoice")
	if reason == CUSTOMER_BLOCKED:
		return _("Complete the customer's details or approval (see the details), then invoice")
	if reason == DELIVERED_NOT_BILLED:
		return _("Raise the invoice for the goods already delivered")
	if reason == NOT_INVOICED:
		return _("Raise the Sales Invoice")
	if reason in (NO_STOCK, PART_STOCK):
		# where the rest of the first short line sits, when it sits anywhere the user may see
		short = [line for line in lines if line.get("tracked") and line.get("state") in (PART_STOCK, NO_STOCK)
			and line.get("elsewhere")]
		if short:
			warehouse, count = max(short[0].elsewhere.items(), key=lambda item: item[1])
			return _("Transfer the stock from %s (%s there) or buy it") % (warehouse, qty(count))
		if reason == PART_STOCK:
			return _("Invoice the part that is in stock, or complete the stock first")
		return _("Get the stock: buy it or transfer it in")
	if reason == NOT_DUE:
		return _("Nothing yet: the delivery date has not come")
	if reason == ON_HOLD:
		return _("Release the hold, or close the order")
	return ""


def why_not_invoiced(order, today, *, lines=(), draft_invoices=(), draft_notes=(), delivered_notes=(), blocks=()) -> tuple:
	"""(reason, detail) for an order that has not been invoiced in full; ("", "") for one that has, or is finished.

	The reason is the first of: on hold, an invoice in draft, a Delivery Note in draft, a delivery date not
	yet come, the stock short, the customer blocked, goods delivered and never billed -- and when nothing of
	that is the matter, STOCK_READY (every line is in stock) or NOT_INVOICED. The detail names every one that
	applies.
	"""
	if not needs_reason(order):
		return "", ""
	check = stock_check([line.get("state") for line in lines if line.get("tracked")])
	facts = []
	if order.status == ON_HOLD:
		facts.append((ON_HOLD, _("The order is on hold")))
	if draft_invoices:
		facts.append((DRAFT_INVOICE, _("Invoice saved but not submitted: %s") % ", ".join(sorted(draft_invoices))))
	if draft_notes:
		facts.append((DRAFT_DELIVERY, _("Delivery Note saved but not submitted: %s") % ", ".join(sorted(draft_notes))))
	if order.get("delivery_date") and getdate(order.delivery_date) > getdate(today):
		facts.append((NOT_DUE, _("Delivery date %s has not come") % frappe.format_value(order.delivery_date, "Date")))
	if lines and check in (PART_STOCK, NO_STOCK):
		facts.append((check, stock_note(lines) or (_("Not enough stock") if check == PART_STOCK else _("No stock"))))
	if blocks:
		facts.append((CUSTOMER_BLOCKED, "; ".join(blocks)))
	if not lines and delivered_notes:
		facts.append((DELIVERED_NOT_BILLED, _("Delivered on %s; no invoice raised") % ", ".join(sorted(delivered_notes))))
	if not facts:
		if check == IN_STOCK:
			facts.append((STOCK_READY, _("Stock is there for every line and nothing has been raised")))
		else:
			facts.append((NOT_INVOICED, _("Nothing has been raised against it")))
	return facts[0][0], " | ".join(text for _reason, text in facts)


def demand_in_sequence(actual, lines) -> list:
	"""Serve `lines` (oldest order first) from `actual` units on hand: each line with the stock before it,
	after it, and whether it is covered. `lines` is a list of dicts with `pending`; the dicts get
	`stock_before`, `stock_after` and `covered` ("Covered", "Part", "Not Covered")."""
	running = flt(actual)
	for line in lines:
		line["stock_before"] = running
		running -= flt(line["pending"])
		line["stock_after"] = running
		if running >= -TOLERANCE:
			line["covered"] = "Covered"
		elif line["stock_before"] > TOLERANCE:
			line["covered"] = "Part"
		else:
			line["covered"] = "Not Covered"
	return lines


# ─── what the database says ──────────────────────────────────────────────────────────────────────


def pending_lines(names) -> dict:
	"""order -> its lines that still have goods to deliver (those the supplier ships direct are not ours to stock)."""
	out = defaultdict(list)
	for chunk in chunks(names):
		for line in frappe.get_all("Sales Order Item", filters={"parent": ["in", chunk]},
				fields=["name", "parent", "idx", "item_code", "item_name", "item_group", "uom", "stock_uom", "conversion_factor",
					"qty", "delivered_qty", "warehouse", "delivered_by_supplier"],
				order_by="parent asc, idx asc", limit_page_length=0):
			pending = flt(line.qty) - flt(line.delivered_qty)
			if cint(line.delivered_by_supplier) or pending <= TOLERANCE:
				continue
			line.pending = pending
			line.pending_stock = pending * (flt(line.conversion_factor) or 1.0)
			out[line.parent].append(line)
	return out


def bins_of(item_codes) -> tuple:
	"""((item, warehouse) -> {actual, reserved, projected}, item -> [(warehouse, actual)])."""
	by_pair, by_item = {}, defaultdict(list)
	for chunk in chunks(sorted(set(item_codes))):
		for row in frappe.get_all("Bin", filters={"item_code": ["in", chunk]},
				fields=["item_code", "warehouse", "actual_qty", "reserved_qty", "projected_qty"], limit_page_length=0):
			by_pair[(row.item_code, row.warehouse)] = frappe._dict(
				actual=flt(row.actual_qty), reserved=flt(row.reserved_qty), projected=flt(row.projected_qty))
			by_item[row.item_code].append((row.warehouse, flt(row.actual_qty)))
	return by_pair, by_item


def stock_tracked(item_codes) -> set:
	"""The items whose stock is kept (a service on an order has no stock to check)."""
	out = set()
	for chunk in chunks(sorted(set(item_codes))):
		out.update(frappe.get_all("Item", filters={"name": ["in", chunk], "is_stock_item": 1}, pluck="name"))
	return out


def readable_warehouses(company) -> set:
	"""The warehouses of the company the user may see (user permissions apply)."""
	return set(frappe.get_list("Warehouse", filters={"company": company, "is_group": 0, "disabled": 0}, pluck="name",
		limit_page_length=0))


def annotate(lines_by_order, by_pair, by_item, tracked, warehouses):
	"""Put the stock on each line: what is on hand at its warehouse, what is reserved there, what sits elsewhere."""
	for lines in lines_by_order.values():
		for line in lines:
			line.tracked = line.item_code in tracked
			bin_ = by_pair.get((line.item_code, line.warehouse))
			line.actual = bin_.actual if bin_ else 0.0
			line.reserved = bin_.reserved if bin_ else 0.0
			line.elsewhere = {warehouse: count for warehouse, count in by_item.get(line.item_code, [])
				if warehouse != line.warehouse and warehouse in warehouses and count > TOLERANCE}
			line.state = line_state(line.pending_stock, line.actual) if line.tracked else ""


def drafts_of(names) -> tuple:
	"""(order -> {draft invoice}, order -> {draft Delivery Note}): documents made from the order and never submitted."""
	invoices, notes = defaultdict(set), defaultdict(set)
	for chunk in chunks(names):
		for row in frappe.db.sql(
			"""SELECT DISTINCT i.sales_order AS so, s.name FROM `tabSales Invoice Item` i
			INNER JOIN `tabSales Invoice` s ON s.name = i.parent
			WHERE s.docstatus = 0 AND s.is_return = 0 AND i.sales_order IN %(names)s""", {"names": tuple(chunk)}, as_dict=True):
			invoices[row.so].add(row.name)
		for row in frappe.db.sql(
			"""SELECT DISTINCT i.against_sales_order AS so, d.name FROM `tabDelivery Note Item` i
			INNER JOIN `tabDelivery Note` d ON d.name = i.parent
			WHERE d.docstatus = 0 AND d.is_return = 0 AND i.against_sales_order IN %(names)s""", {"names": tuple(chunk)}, as_dict=True):
			notes[row.so].add(row.name)
	return invoices, notes


def submitted_delivery_notes(names) -> dict:
	out = defaultdict(set)
	for chunk in chunks(names):
		for row in frappe.db.sql(
			"""SELECT DISTINCT i.against_sales_order AS so, d.name FROM `tabDelivery Note Item` i
			INNER JOIN `tabDelivery Note` d ON d.name = i.parent
			WHERE d.docstatus = 1 AND d.is_return = 0 AND i.against_sales_order IN %(names)s""", {"names": tuple(chunk)}, as_dict=True):
			out[row.so].add(row.name)
	return out


def customer_blocks(orders, company) -> dict:
	"""{(customer, payment mode): [what a Sales Invoice for that customer would be refused for]}.

	The gates registered on Sales Invoice are asked, exactly as the party popup asks them (party_alert.py),
	plus a customer that is disabled or frozen. A blank Payment Mode is asked as Cash, the lenient reading:
	only what refuses in every mode is reported until the mode is known.
	"""
	from sf_trading import party_alert

	out = {}
	for order in orders:
		key = (order.customer, cstr(order.get("custom_payment_mode")).strip())
		if key in out:
			continue
		problems = []
		disabled, frozen = frappe.db.get_value("Customer", order.customer, ["disabled", "is_frozen"]) or (0, 0)
		if cint(disabled):
			problems.append(_("Customer is disabled"))
		if cint(frozen):
			problems.append(_("Customer is frozen"))
		header = {"customer": order.customer, "company": company, "custom_payment_mode": key[1] or "Cash", "__islocal": 1}
		try:
			problems += [strip_html(text).strip() for text in party_alert.party_problems("Sales Invoice", header)]
		except Exception:
			# the tracker must open even when a gate cannot be asked
			frappe.logger("sf_trading").exception("order stock: customer gates for %s", order.customer)
		out[key] = problems
	return out


def load(orders, company) -> frappe._dict:
	"""Everything the stock and reason columns read, for the orders given (rows with name, customer and, when
	the site has it, custom_payment_mode)."""
	names = [order.name for order in orders]
	lines = pending_lines(names)
	items = {line.item_code for ls in lines.values() for line in ls}
	by_pair, by_item = bins_of(items)
	warehouses = readable_warehouses(company)
	annotate(lines, by_pair, by_item, stock_tracked(items), warehouses)
	invoices, notes = drafts_of(names)
	return frappe._dict(
		lines=lines,
		warehouses=warehouses,
		draft_invoices=invoices,
		draft_notes=notes,
		delivered_notes=submitted_delivery_notes(names),
		blocks=customer_blocks(orders, company),
		bins=by_pair,
		bins_by_item=by_item,
	)


def reason_for(order, stock, today) -> tuple:
	"""(reason, detail, stock check) of one order from `load`'s facts."""
	lines = stock.lines.get(order.name, [])
	key = (order.customer, cstr(order.get("custom_payment_mode")).strip())
	reason, detail = why_not_invoiced(order, today, lines=lines, draft_invoices=stock.draft_invoices.get(order.name, ()),
		draft_notes=stock.draft_notes.get(order.name, ()), delivered_notes=stock.delivered_notes.get(order.name, ()),
		blocks=stock.blocks.get(key, ()))
	return reason, detail, stock_check([line.state for line in lines if line.tracked])


def open_demand(pairs) -> dict:
	"""(item, warehouse) -> every open line asking for it company-wide, oldest order first, as dicts ready for
	`demand_in_sequence`. A pair is read whole: the orders a filter hides still take their share of the stock."""
	wanted = defaultdict(set)
	for item, warehouse in pairs:
		wanted[item].add(warehouse)
	out = defaultdict(list)
	for chunk in chunks(sorted(wanted)):
		rows = frappe.db.sql(
			"""SELECT i.item_code, i.warehouse, i.parent AS sales_order, i.name AS detail, so.customer_name, so.transaction_date,
				(i.qty - i.delivered_qty) * IF(i.conversion_factor, i.conversion_factor, 1) AS pending
			FROM `tabSales Order Item` i INNER JOIN `tabSales Order` so ON so.name = i.parent
			WHERE so.docstatus = 1 AND so.status IN %(statuses)s AND i.item_code IN %(items)s
				AND i.delivered_by_supplier = 0 AND i.qty > i.delivered_qty
			ORDER BY so.transaction_date, so.creation, i.idx""", {"statuses": OPEN_FOR_DELIVERY, "items": tuple(chunk)}, as_dict=True)
		for row in rows:
			if row.warehouse in wanted[row.item_code]:
				row.pending = flt(row.pending)
				out[(row.item_code, row.warehouse)].append(row)
	return out
