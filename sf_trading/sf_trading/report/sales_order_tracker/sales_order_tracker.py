# Copyright (c) 2026, Enfono Technologies and contributors
# For license information, please see license.txt

"""Sales Order Tracker: every order with where it stands, in detail.

ERPNext flags a Sales Order "Overdue" when its delivery date has passed and less than 100% is
delivered. That is all it knows. An order that was billed and paid in full still reads Overdue if
the goods were delivered in part, and an order whose goods came back on a Sales Return reads
Overdue again, because a return takes the delivered quantity down. Nothing in ERPNext closes
such an order, so staff close them by hand.

This report puts what ERPNext does not look at next to the order, so a person can tell a real
overdue from one that only looks overdue:

* what the order was converted to (invoices, delivery notes) and how much is still open;
* whether the invoices are paid (and the advance taken on the order);
* the returns recorded against it (credit notes, delivery note returns), and how much of the
  returned stock still has to come back through a Delivery Note return -- the same figure as the
  Delivery Notes Pending Return report;
* a Tracking State and a Suggested Action.

Tracking State
    Overdue               delivery date passed, goods still owed, nothing settled or returned
    Overdue - Settled     overdue, but fully billed and every invoice paid: delivery came up short
    Overdue - Returned    overdue, and a return was recorded against it
    Converted             open, and already invoiced or delivered in part
    Not Due               open, delivery date not reached, nothing converted yet
    On Hold / Completed / Closed    as ERPNext says

The two "Overdue -" states are the ones to look at before closing; `close_orders` does the closing,
with a reason that is written to the order's comments.

Close Check says, from the data on the row, whether closing the order by hand is right:

    Ready To Close          billed in full, paid in full, goods returned and back in stock, the customer
                            credited, and a return reason that means nothing is owed again (Order
                            Cancellation, Over Supply, Duplicate Invoice)
    Review Before Closing   paid but short of delivery with no return; returned with an unpaid invoice,
                            goods still to come back, no credit note, or not billed in full; or returned
                            for a reason that may mean a replacement is owed (Damaged, Defective, Wrong
                            Item, Wrong Quantity, Quality Issue, Other, none recorded): a person decides
    Keep Open               goods or money still owed
    Closed                  ERPNext already closed or completed it

The reason is written beside it, and "Tick Ready To Close" on the report ticks every ready order.

Why Not Invoiced says why an open order has no invoice yet, from sf_trading/order_stock.py: an invoice or
Delivery Note saved and never submitted, the stock short (the line's own warehouse, and where else the rest
sits), the customer refused by the Sales Invoice gates, a delivery date not come, an order on hold, goods
delivered and never billed -- and, when none of those is the matter, "Stock Ready, Not Invoiced": the order
that is simply waiting for someone. Next Step says what the team does about it, in a sentence; Stock says
whether the warehouse holds what is still to be delivered (In Stock / Part Stock / No Stock) and Warehouse
which warehouse(s) the goods are to come from. Colour says who has to move: RED = somebody raises or submits
something today, ORANGE = chase the stock, GREEN = nothing to do yet, GRAY = on hold. The orders somebody has
to act on come first (oldest first), and "Needs Action Now" shows only those. "Stock Drill-Down" opens the
Sales Order Stock Drill Down report on the order: item by item, warehouse by warehouse, the orders competing
for the same goods.

Views: Order Wise (one row per order) and Item Wise (one row per order line: ordered, delivered,
returned, still to deliver and billed per item, and the stock behind the line). Filters: company, tracking
state, why not invoiced, stock, ERPNext status, customer, customer group, territory, branch, cost center,
sales person, item, item group, payment, converted to (invoice / delivery note / both / neither), has a
return, return stock status, order and delivery date windows, a single order.
"""

from collections import defaultdict

import frappe
from frappe import _
from frappe.utils import cint, cstr, date_diff, flt, getdate, nowdate

from sf_trading import order_stock

TOLERANCE = 0.005
CHUNK = 1000

OVERDUE = "Overdue"
OVERDUE_SETTLED = "Overdue - Settled"
OVERDUE_RETURNED = "Overdue - Returned"
CONVERTED = "Converted"
NOT_DUE = "Not Due"
ON_HOLD = "On Hold"
COMPLETED = "Completed"
CLOSED = "Closed"
NEEDS_ATTENTION = "Needs Attention"
ORDER_WISE = "Order Wise"
ITEM_WISE = "Item Wise"

# Return reasons (Sales Return Reason) after which nothing is owed to the customer again; any other
# reason, or none, may mean a replacement is still to be delivered
NO_REPLACEMENT_REASONS = ("Order Cancellation", "Over Supply", "Duplicate Invoice")

# Close Check
READY_TO_CLOSE = "Ready To Close"
REVIEW_BEFORE_CLOSING = "Review Before Closing"
KEEP_OPEN = "Keep Open"
ALREADY_CLOSED = "Closed"

# what became of the goods on the returns of an order
NO_RETURN = ""
STOCK_RESTORED = "Stock Restored"
AWAITING_DN_RETURN = "Awaiting DN Return"
ATTENTION_STATES = (OVERDUE, OVERDUE_SETTLED, OVERDUE_RETURNED)

PAID = "Paid"
PART_PAID = "Part Paid"
UNPAID = "Unpaid"
ADVANCE_ONLY = "Advance Only"
NOT_INVOICED = "Not Invoiced"

#: statuses a person may close from the report
CLOSABLE_STATUSES = ("To Deliver and Bill", "To Bill", "To Deliver")


def execute(filters=None):
	filters = frappe._dict(filters or {})
	if not filters.get("company"):
		frappe.throw(_("Company is required"))

	orders = get_orders(filters)
	facts = get_facts([o.name for o in orders], filters.company)
	# the stock, drafts and customer gates behind "why not invoiced": only for orders with something left to invoice
	facts.stock = order_stock.load([o for o in orders if order_stock.needs_reason(o)], filters.company)
	today = getdate(nowdate())
	rows = []
	for order in orders:
		row = build_row(order, facts, today)
		if wanted(row, filters):
			rows.append(row)

	rows.sort(key=row_order)
	currency = frappe.get_cached_value("Company", filters.company, "default_currency")
	summary = get_summary(rows, currency)
	if filters.get("view") == ITEM_WISE:
		return get_item_columns(), item_rows(rows, filters, facts), None, None, summary
	return get_columns(), rows, None, None, summary


# ─── data ────────────────────────────────────────────────────────────────────────────────────────


def get_orders(filters) -> list:
	meta = frappe.get_meta("Sales Order")
	conditions = {"docstatus": 1, "company": filters.company}
	if filters.get("sales_order"):
		conditions["name"] = filters.sales_order
	if filters.get("customer"):
		conditions["customer"] = filters.customer
	if filters.get("branch") and meta.has_field("branch"):
		conditions["branch"] = filters.branch
	conditions.update(_date_window("transaction_date", filters.get("from_date"), filters.get("to_date")))
	conditions.update(_date_window("delivery_date", filters.get("delivery_from"), filters.get("delivery_to")))
	for fieldname, key in (("customer_group", "customer_group"), ("territory", "territory"),
			("cost_center", "cost_center"), ("custom_sales_person", "sales_person"), ("status", "status")):
		if filters.get(key) and meta.has_field(fieldname):
			conditions[fieldname] = filters.get(key)
	by_item = orders_with_item(filters)
	if by_item is not None:
		if filters.get("sales_order"):
			by_item &= {filters.sales_order}
		conditions["name"] = ["in", sorted(by_item) or ["-"]]

	fields = ["name", "transaction_date", "delivery_date", "customer", "customer_name", "status", "grand_total",
		"per_delivered", "per_billed", "advance_paid", "skip_delivery_note"]
	for optional in ("branch", "customer_group", "territory", "cost_center", "custom_sales_person", "custom_payment_mode"):
		if meta.has_field(optional):
			fields.append(optional)
	return frappe.get_list("Sales Order", filters=conditions, fields=fields, order_by="transaction_date desc, name desc",
		limit_page_length=0)


def orders_with_item(filters):
	"""Names of the orders that carry the item / item group asked for, or None when neither was."""
	conditions = {"docstatus": 1}
	if filters.get("item_code"):
		conditions["item_code"] = filters.item_code
	if filters.get("item_group"):
		lft, rgt = frappe.db.get_value("Item Group", filters.item_group, ["lft", "rgt"]) or (0, 0)
		groups = frappe.get_all("Item Group", filters={"lft": [">=", lft], "rgt": ["<=", rgt]}, pluck="name")
		conditions["item_group"] = ["in", groups or [filters.item_group]]
	if len(conditions) == 1:
		return None
	return set(frappe.get_all("Sales Order Item", filters=conditions, pluck="parent", distinct=True))


def _date_window(fieldname, start, end) -> dict:
	if start and end:
		return {fieldname: ["between", [getdate(start), getdate(end)]]}
	if start:
		return {fieldname: [">=", getdate(start)]}
	if end:
		return {fieldname: ["<=", getdate(end)]}
	return {}


def _chunks(names):
	names = list(names)
	for i in range(0, len(names), CHUNK):
		yield names[i:i + CHUNK]


def get_facts(names, company) -> frappe._dict:
	"""Everything the report reads besides the orders themselves, keyed by order."""
	facts = frappe._dict(
		open_qty=defaultdict(float),
		invoices=defaultdict(dict),      # order -> {invoice: row}
		delivery_notes=defaultdict(dict),
		si_returns=defaultdict(dict),
		dn_returns=defaultdict(dict),
		pending_return_qty=defaultdict(float),
	)
	if not names:
		return facts

	# the Return Reason Template a credit note carries (a custom field; absent on a bare site)
	reason_column = "s.custom_return_reason_template" if frappe.get_meta("Sales Invoice").has_field(
		"custom_return_reason_template") else "NULL"
	invoice_orders = defaultdict(set)   # invoice -> orders it bills
	delivery_orders = defaultdict(set)  # delivery note -> orders it delivers

	for chunk in _chunks(names):
		for r in frappe.get_all("Sales Order Item", filters={"parent": ["in", chunk]},
				fields=["parent", "sum(qty) as qty", "sum(delivered_qty) as delivered"], group_by="parent"):
			facts.open_qty[r.parent] = max(0.0, flt(r.qty) - flt(r.delivered))

		for r in frappe.db.sql(
			"""SELECT DISTINCT i.sales_order AS so, s.name, s.grand_total, s.outstanding_amount, s.is_return, s.update_stock,
				""" + reason_column + """ AS reason
			FROM `tabSales Invoice Item` i INNER JOIN `tabSales Invoice` s ON s.name = i.parent
			WHERE s.docstatus = 1 AND i.sales_order IN %(names)s""", {"names": tuple(chunk)}, as_dict=True):
			if cint(r.is_return):
				facts.si_returns[r.so][r.name] = r
			else:
				facts.invoices[r.so][r.name] = r
				invoice_orders[r.name].add(r.so)

		for r in frappe.db.sql(
			"""SELECT DISTINCT i.against_sales_order AS so, d.name, d.grand_total, d.is_return
			FROM `tabDelivery Note Item` i INNER JOIN `tabDelivery Note` d ON d.name = i.parent
			WHERE d.docstatus = 1 AND i.against_sales_order IN %(names)s""", {"names": tuple(chunk)}, as_dict=True):
			if cint(r.is_return):
				facts.dn_returns[r.so][r.name] = r
			else:
				facts.delivery_notes[r.so][r.name] = r
				delivery_orders[r.name].add(r.so)

	# credit notes and delivery note returns name the document they return, not always the order
	for doctype, orders_of, bucket in (
		("Sales Invoice", invoice_orders, facts.si_returns),
		("Delivery Note", delivery_orders, facts.dn_returns),
	):
		for chunk in _chunks(orders_of):
			extra = ["custom_return_reason_template as reason"] if doctype == "Sales Invoice" and reason_column != "NULL" else []
			for r in frappe.get_all(doctype, filters={"docstatus": 1, "is_return": 1, "return_against": ["in", chunk]},
					fields=["name", "return_against", "grand_total"] + extra):
				for so in orders_of.get(r.return_against, ()):
					bucket[so][r.name] = r

	for r in pending_dn_return(company):
		for so in invoice_orders.get(r.invoice, ()):
			facts.pending_return_qty[so] += flt(r.pending_qty)
	return facts


def pending_dn_return(company) -> list:
	"""Delivery lines credited on a Sales Return whose goods never came back through a Delivery Note
	return (the Delivery Notes Pending Return report, line wise)."""
	from sf_trading.sf_trading.report.delivery_notes_pending_return import delivery_notes_pending_return as pending

	rows = pending.get_rows(frappe._dict(company=company))
	for r in rows:
		r.credited_qty = flt(r.credited_qty)
		r.returned_qty = flt(r.returned_qty)
	pending.allocate_pending(rows)
	return [r for r in rows if r.status == pending.PENDING]


# ─── one order ───────────────────────────────────────────────────────────────────────────────────


def payment_state(order, invoices: dict) -> tuple:
	"""(state, invoiced, outstanding) from the order's own invoices; the advance when none exists."""
	invoiced = flt(sum(flt(i.grand_total) for i in invoices.values()), 3)
	outstanding = flt(sum(flt(i.outstanding_amount) for i in invoices.values()), 3)
	if invoices:
		if outstanding <= TOLERANCE:
			return PAID, invoiced, 0.0
		if outstanding < invoiced - TOLERANCE:
			return PART_PAID, invoiced, outstanding
		return UNPAID, invoiced, outstanding
	if flt(order.advance_paid) > TOLERANCE:
		return ADVANCE_ONLY, 0.0, 0.0
	return NOT_INVOICED, 0.0, 0.0


def classify(status, days_overdue, paid_state, returned, converted, billed_in_full) -> str:
	"""The Tracking State of one order."""
	if status in (CLOSED, COMPLETED, ON_HOLD):
		return status
	if days_overdue > 0:
		if returned:
			return OVERDUE_RETURNED
		if billed_in_full and paid_state == PAID:
			return OVERDUE_SETTLED
		return OVERDUE
	return CONVERTED if converted else NOT_DUE


def suggested_action(state, outstanding, pending_return_qty, check=None, check_reason="") -> str:
	if state == OVERDUE:
		return _("Deliver the goods or follow up with the customer")
	if state == OVERDUE_SETTLED:
		return _("Paid in full: deliver the rest, or Close if the customer needs nothing more")
	if state == OVERDUE_RETURNED:
		parts = []
		if outstanding > TOLERANCE:
			parts.append(_("collect or credit the unpaid invoice"))
		if pending_return_qty > TOLERANCE:
			parts.append(_("raise the Delivery Note return that puts the goods back in stock"))
		if not parts:
			parts.append(_("Close: goods returned and nothing left to collect") if check == READY_TO_CLOSE
				else _("Review before closing: %s") % check_reason)
		return "; ".join(parts)
	if state == CONVERTED:
		return _("Bill or deliver the rest")
	return ""


def close_check(status, state, billed_in_full, paid_state, outstanding, returned, pending_return_qty,
		per_delivered, per_billed, reasons=(), credited=True) -> tuple:
	"""(check, reason): is closing this order by hand right, from what the row shows."""
	if status in (CLOSED, COMPLETED):
		return ALREADY_CLOSED, ""
	if status == ON_HOLD:
		return KEEP_OPEN, _("On hold")
	pending = flt(pending_return_qty, 3)
	if returned and billed_in_full and paid_state == PAID and pending <= TOLERANCE:
		if not credited:
			return REVIEW_BEFORE_CLOSING, _("The goods came back on a Delivery Note return but no credit note was raised for the customer")
		if not reasons:
			return REVIEW_BEFORE_CLOSING, _("Return reason not recorded: confirm no replacement is owed")
		replacement = [r for r in reasons if r not in NO_REPLACEMENT_REASONS]
		if replacement:
			return REVIEW_BEFORE_CLOSING, (
				_("Returned as %s: a replacement may still be owed") % ", ".join(sorted(replacement)))
		return READY_TO_CLOSE, _("Billed in full, paid in full, the returned goods are back in stock and the customer is credited")
	if state == OVERDUE_SETTLED:
		return REVIEW_BEFORE_CLOSING, (
			_("Paid in full but only %s%% delivered and nothing returned: confirm the customer needs no more")
			% round(flt(per_delivered)))
	parts = []
	if outstanding > TOLERANCE:
		parts.append(_("an invoice is unpaid (%s)") % flt(outstanding, 2))
	if pending > TOLERANCE:
		parts.append(_("returned goods still have to come back by Delivery Note return (qty %s)") % pending)
	if not billed_in_full:
		parts.append(_("billed %s%%") % round(flt(per_billed)))
	if returned:
		return REVIEW_BEFORE_CLOSING, "; ".join(parts) or _("Returned: check before closing")
	if flt(per_delivered, 2) < 100:
		parts.insert(0, _("delivered %s%%") % round(flt(per_delivered)))
	return KEEP_OPEN, "; ".join(parts)


def return_stock_status(si_returns: dict, dn_returns: dict, pending_return_qty) -> str:
	"""What became of the goods on an order's returns: back in stock, or still to come back through a
	Delivery Note return (the credit was given but the warehouse still shows them delivered)."""
	if not (si_returns or dn_returns):
		return NO_RETURN
	return AWAITING_DN_RETURN if flt(pending_return_qty) > TOLERANCE else STOCK_RESTORED


def converted_to(invoices, delivery_notes) -> str:
	if invoices and delivery_notes:
		return "Both"
	if invoices:
		return "Invoice"
	if delivery_notes:
		return "Delivery Note"
	return "Neither"


def build_row(order, facts, today) -> frappe._dict:
	name = order.name
	invoices = facts.invoices.get(name, {})
	delivery_notes = facts.delivery_notes.get(name, {})
	si_returns = facts.si_returns.get(name, {})
	dn_returns = facts.dn_returns.get(name, {})

	overdue = bool(
		order.delivery_date
		and not cint(order.skip_delivery_note)
		and flt(order.per_delivered, 2) < 100
		and getdate(order.delivery_date) < today
		and order.status not in (CLOSED, COMPLETED, ON_HOLD)
	)
	days_overdue = date_diff(today, order.delivery_date) if overdue else 0
	paid_state, invoiced, outstanding = payment_state(order, invoices)
	returned = bool(si_returns or dn_returns)
	converted = bool(invoices or delivery_notes)
	billed_in_full = flt(order.per_billed, 2) >= 100
	pending_qty = flt(facts.pending_return_qty.get(name), 3)

	stock = facts.get("stock")
	reason, why_detail, stock_state = order_stock.reason_for(order, stock, today) if stock else ("", "", "")
	held = stock.lines.get(name, []) if stock else []

	state = classify(order.status, days_overdue, paid_state, returned, converted, billed_in_full)
	reasons = sorted({cstr(r.get("reason")).strip() for r in si_returns.values() if cstr(r.get("reason")).strip()})
	check, check_reason = close_check(order.status, state, billed_in_full, paid_state, outstanding, returned,
		pending_qty, order.per_delivered, order.per_billed, reasons=reasons, credited=bool(si_returns) or not dn_returns)
	return frappe._dict(
		sales_order=name,
		transaction_date=order.transaction_date,
		delivery_date=order.delivery_date,
		days_overdue=days_overdue,
		customer=order.customer,
		customer_name=order.customer_name,
		branch=order.get("branch"),
		customer_group=order.get("customer_group"),
		territory=order.get("territory"),
		sales_person=order.get("custom_sales_person"),
		cost_center=order.get("cost_center"),
		status=order.status,
		converted_to=converted_to(invoices, delivery_notes),
		tracking_state=state,
		why_not_invoiced=reason,
		why_detail=why_detail,
		next_step=order_stock.next_step(reason, held),
		needs_action=1 if reason in order_stock.ACTIONABLE else 0,
		warehouses=order_stock.warehouses_of(held) if reason else "",
		stock_drilldown=name if reason else "",
		stock_check=stock_state,
		stock_there=1 if reason in order_stock.ACTIONABLE and stock_state == order_stock.IN_STOCK else 0,
		waiting_days=date_diff(today, order.transaction_date) if reason and order.get("transaction_date") else 0,
		needs_attention=state in ATTENTION_STATES,
		grand_total=flt(order.grand_total),
		per_delivered=flt(order.per_delivered),
		per_billed=flt(order.per_billed),
		open_qty=flt(facts.open_qty.get(name), 3),
		invoices=", ".join(sorted(invoices)),
		invoiced_amount=invoiced,
		payment_state=paid_state,
		outstanding_amount=outstanding,
		advance_paid=flt(order.advance_paid),
		delivery_notes=", ".join(sorted(delivery_notes)),
		returns=", ".join(sorted(list(si_returns) + list(dn_returns))),
		has_return=1 if returned else 0,
		return_stock=return_stock_status(si_returns, dn_returns, pending_qty),
		returned_amount=flt(sum(abs(flt(r.grand_total)) for r in si_returns.values()), 3),
		pending_return_qty=pending_qty,
		can_close=1 if order.status in CLOSABLE_STATUSES else 0,
		close_check=check,
		close_reason=check_reason,
		return_reasons=", ".join(reasons),
		suggested_action=suggested_action(state, outstanding, pending_qty, check, check_reason),
	)


def row_order(row) -> tuple:
	"""The orders somebody has to act on come first, the longest waiting first; then the ones that need attention
	(overdue, most days first); then the rest."""
	return (not row.needs_action, -cint(row.waiting_days) if row.needs_action else 0, not row.needs_attention,
		-cint(row.days_overdue), row.sales_order)


def wanted(row, filters) -> bool:
	state = filters.get("tracking_state")
	if state == NEEDS_ATTENTION and not row.needs_attention:
		return False
	if state and state != NEEDS_ATTENTION and row.tracking_state != state:
		return False
	if filters.get("payment_state") and row.payment_state != filters.payment_state:
		return False
	if filters.get("why_not_invoiced") and row.why_not_invoiced != filters.why_not_invoiced:
		return False
	if cint(filters.get("needs_action")) and not row.needs_action:
		return False
	if filters.get("stock_check") and row.stock_check != filters.stock_check:
		return False
	if cint(filters.get("has_return")) and not row.has_return:
		return False
	if filters.get("converted_to") and row.converted_to != filters.converted_to:
		return False
	if filters.get("close_check") and row.close_check != filters.close_check:
		return False
	if filters.get("return_stock") and row.return_stock != filters.return_stock:
		return False
	if not cint(filters.get("include_closed")) and not state and row.tracking_state in (CLOSED, COMPLETED):
		return False
	return True


# ─── what the page shows ─────────────────────────────────────────────────────────────────────────


def get_summary(rows, currency) -> list:
	def count(*states):
		return len([r for r in rows if r.tracking_state in states])

	return [
		{"label": _("Orders Shown"), "value": len(rows), "datatype": "Int", "indicator": "blue"},
		{"label": _("Overdue"), "value": count(OVERDUE), "datatype": "Int", "indicator": "red"},
		{"label": _("Overdue - Settled"), "value": count(OVERDUE_SETTLED), "datatype": "Int", "indicator": "orange"},
		{"label": _("Overdue - Returned"), "value": count(OVERDUE_RETURNED), "datatype": "Int", "indicator": "orange"},
		{"label": _("Converted, Open"), "value": count(CONVERTED), "datatype": "Int", "indicator": "blue"},
		{"label": _("Not Due"), "value": count(NOT_DUE), "datatype": "Int", "indicator": "green"},
		{"label": _("Needs Action Now"), "datatype": "Int", "indicator": "red",
			"value": len([r for r in rows if r.needs_action])},
		{"label": _("Stock There, Not Invoiced"), "datatype": "Int", "indicator": "red",
			"value": len([r for r in rows if r.stock_there])},
		{"label": _("Waiting For Stock"), "datatype": "Int", "indicator": "orange",
			"value": len([r for r in rows if r.why_not_invoiced in (order_stock.NO_STOCK, order_stock.PART_STOCK)])},
		{"label": _("Ready To Close"), "datatype": "Int", "indicator": "green",
			"value": len([r for r in rows if r.close_check == READY_TO_CLOSE])},
		{"label": _("Unpaid On Open Orders"), "datatype": "Currency", "currency": currency, "indicator": "red",
			"value": flt(sum(r.outstanding_amount for r in rows if r.tracking_state not in (CLOSED, COMPLETED)), 3)},
	]


def item_rows(rows, filters, facts=None) -> list:
	"""One row per order line of the orders shown, carrying the order's state and, for a line still to be
	delivered, the stock behind it."""
	by_order = {r.sales_order: r for r in rows}
	if not by_order:
		return []
	stock = (facts or {}).get("stock")
	behind = {(order, line.name): line for order, lines in (stock.lines if stock else {}).items() for line in lines}
	conditions = {"parent": ["in", list(by_order)]}
	if filters.get("item_code"):
		conditions["item_code"] = filters.item_code
	if filters.get("item_group"):
		lft, rgt = frappe.db.get_value("Item Group", filters.item_group, ["lft", "rgt"]) or (0, 0)
		conditions["item_group"] = ["in", frappe.get_all("Item Group", filters={"lft": [">=", lft], "rgt": ["<=", rgt]},
			pluck="name") or [filters.item_group]]
	lines = frappe.get_all("Sales Order Item", filters=conditions,
		fields=["name", "parent", "idx", "item_code", "item_name", "item_group", "uom", "qty", "delivered_qty", "returned_qty",
			"rate", "amount", "billed_amt", "warehouse"])
	out = []
	for line in lines:
		order = by_order.get(line.parent)
		if not order:
			continue
		held = behind.get((line.parent, line.name))
		out.append(frappe._dict(
			order,
			line_stock=held.state if held and held.tracked else "",
			stock_at_warehouse=held.actual if held and held.tracked else None,
			reserved_total=held.reserved if held and held.tracked else None,
			stock_elsewhere=", ".join("%s %s" % (warehouse, order_stock.qty(count)) for warehouse, count in
				sorted(held.elsewhere.items(), key=lambda kv: -kv[1])) if held and held.tracked else "",
			item_code=line.item_code,
			item_name=line.item_name,
			item_group=line.item_group,
			uom=line.uom,
			warehouse=line.warehouse,
			qty=flt(line.qty),
			delivered_qty=flt(line.delivered_qty),
			returned_qty=flt(line.returned_qty),
			pending_qty=max(0.0, flt(line.qty) - flt(line.delivered_qty)),
			rate=flt(line.rate),
			line_amount=flt(line.amount),
			billed_amt=flt(line.billed_amt),
			idx=line.idx,
		))
	out.sort(key=lambda r: row_order(r) + (r.idx,))
	return out


def get_item_columns() -> list:
	def col(fieldname, label, fieldtype, width=110, options=None):
		return {"fieldname": fieldname, "label": _(label), "fieldtype": fieldtype, "width": width, "options": options}

	return [
		col("sales_order", "Sales Order", "Link", 150, "Sales Order"),
		col("transaction_date", "Order Date", "Date", 95),
		col("customer_name", "Customer Name", "Data", 170),
		col("delivery_date", "Delivery Date", "Date", 95),
		col("days_overdue", "Days Overdue", "Int", 90),
		col("tracking_state", "Tracking State", "Data", 150),
		col("why_not_invoiced", "Why Not Invoiced", "Data", 170),
		col("next_step", "Next Step", "Data", 260),
		col("item_code", "Item", "Link", 130, "Item"),
		col("item_name", "Item Name", "Data", 200),
		col("item_group", "Item Group", "Link", 120, "Item Group"),
		col("uom", "UOM", "Link", 70, "UOM"),
		col("qty", "Ordered Qty", "Float", 100),
		col("delivered_qty", "Delivered Qty", "Float", 100),
		col("returned_qty", "Returned Qty", "Float", 100),
		col("pending_qty", "Still To Deliver", "Float", 105),
		col("line_stock", "Line Stock", "Data", 95),
		col("stock_at_warehouse", "Stock At Warehouse", "Float", 115),
		col("reserved_total", "Reserved (All Orders)", "Float", 125),
		col("stock_elsewhere", "Stock Elsewhere", "Data", 220),
		col("rate", "Rate", "Currency", 95),
		col("line_amount", "Line Amount", "Currency", 110),
		col("billed_amt", "Billed", "Currency", 105),
		col("warehouse", "Warehouse", "Link", 130, "Warehouse"),
		col("payment_state", "Payment", "Data", 100),
		col("return_stock", "Return Stock", "Data", 130),
		col("close_check", "Close?", "Data", 150),
		col("close_reason", "Close Check Reason", "Data", 320),
		col("suggested_action", "Suggested Action", "Data", 300),
		{"fieldname": "can_close", "label": _("Can Close"), "fieldtype": "Check", "width": 70, "hidden": 1},
	]


def get_columns() -> list:
	def col(fieldname, label, fieldtype, width=110, options=None):
		return {"fieldname": fieldname, "label": _(label), "fieldtype": fieldtype, "width": width, "options": options}

	return [
		col("sales_order", "Sales Order", "Link", 160, "Sales Order"),
		col("customer_name", "Customer Name", "Data", 180),
		col("why_not_invoiced", "Why Not Invoiced", "Data", 185),
		col("next_step", "Next Step", "Data", 300),
		col("stock_check", "Stock", "Data", 95),
		col("warehouses", "Warehouse", "Data", 140),
		col("waiting_days", "Days Waiting", "Int", 90),
		col("why_detail", "Why Not Invoiced - Details", "Data", 380),
		col("transaction_date", "Order Date", "Date", 95),
		col("customer", "Customer", "Link", 130, "Customer"),
		col("branch", "Branch", "Link", 100, "Branch"),
		col("sales_person", "Sales Person", "Link", 120, "Sales Person"),
		col("delivery_date", "Delivery Date", "Date", 95),
		col("days_overdue", "Days Overdue", "Int", 90),
		col("status", "ERPNext Status", "Data", 130),
		col("tracking_state", "Tracking State", "Data", 150),
		col("converted_to", "Converted To", "Data", 105),
		col("grand_total", "Order Total", "Currency", 110),
		col("per_delivered", "% Delivered", "Percent", 90),
		col("per_billed", "% Billed", "Percent", 90),
		col("open_qty", "Qty Still To Deliver", "Float", 110),
		col("invoices", "Invoices", "Data", 200),
		col("invoiced_amount", "Invoiced", "Currency", 105),
		col("payment_state", "Payment", "Data", 100),
		col("outstanding_amount", "Unpaid", "Currency", 105),
		col("advance_paid", "Advance On Order", "Currency", 110),
		col("delivery_notes", "Delivery Notes", "Data", 200),
		col("returns", "Returns", "Data", 200),
		col("returned_amount", "Returned (Credit)", "Currency", 115),
		col("return_stock", "Return Stock", "Data", 130),
		col("return_reasons", "Return Reason", "Data", 130),
		col("pending_return_qty", "Qty Awaiting DN Return", "Float", 120),
		col("close_check", "Close?", "Data", 150),
		col("close_reason", "Close Check Reason", "Data", 360),
		col("suggested_action", "Suggested Action", "Data", 340),
		col("stock_drilldown", "Stock Drill-Down", "Data", 115),
		{"fieldname": "can_close", "label": _("Can Close"), "fieldtype": "Check", "width": 70, "hidden": 1},
	]


# ─── closing ─────────────────────────────────────────────────────────────────────────────────────


@frappe.whitelist()
def close_orders(names, reason) -> dict:
	"""Close the selected Sales Orders (ERPNext's own Status > Close, reversible with Re-open) and write why
	on each. Orders that cannot be closed are named, not skipped silently."""
	names = frappe.parse_json(names) if isinstance(names, str) else names
	reason = cstr(reason).strip()
	if not names:
		frappe.throw(_("Select at least one Sales Order."))
	if not reason:
		frappe.throw(_("Say why these orders are being closed."))

	from erpnext.selling.doctype.sales_order.sales_order import update_status

	closed, refused = [], []
	for name in names:
		doc = frappe.get_doc("Sales Order", name)
		doc.check_permission("submit")
		if doc.docstatus != 1 or doc.status not in CLOSABLE_STATUSES:
			refused.append({"name": name, "status": doc.status})
			continue
		update_status("Closed", name)
		doc.add_comment("Comment", _("Closed from the Sales Order Tracker: %s") % reason)
		closed.append(name)
	return {"closed": closed, "refused": refused}
