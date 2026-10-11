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
"""

from collections import defaultdict

import frappe
from frappe import _
from frappe.utils import cint, cstr, date_diff, flt, getdate, nowdate

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
	today = getdate(nowdate())
	rows = []
	for order in orders:
		row = build_row(order, facts, today)
		if wanted(row, filters):
			rows.append(row)

	rows.sort(key=lambda r: (not r.needs_attention, -r.days_overdue, r.sales_order))
	currency = frappe.get_cached_value("Company", filters.company, "default_currency")
	return get_columns(), rows, None, None, get_summary(rows, currency)


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

	fields = ["name", "transaction_date", "delivery_date", "customer", "customer_name", "status", "grand_total",
		"per_delivered", "per_billed", "advance_paid", "skip_delivery_note"]
	if meta.has_field("branch"):
		fields.append("branch")
	return frappe.get_list("Sales Order", filters=conditions, fields=fields, order_by="transaction_date desc, name desc",
		limit_page_length=0)


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

	invoice_orders = defaultdict(set)   # invoice -> orders it bills
	delivery_orders = defaultdict(set)  # delivery note -> orders it delivers

	for chunk in _chunks(names):
		for r in frappe.get_all("Sales Order Item", filters={"parent": ["in", chunk]},
				fields=["parent", "sum(qty) as qty", "sum(delivered_qty) as delivered"], group_by="parent"):
			facts.open_qty[r.parent] = max(0.0, flt(r.qty) - flt(r.delivered))

		for r in frappe.db.sql(
			"""SELECT DISTINCT i.sales_order AS so, s.name, s.grand_total, s.outstanding_amount, s.is_return, s.update_stock
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
			for r in frappe.get_all(doctype, filters={"docstatus": 1, "is_return": 1, "return_against": ["in", chunk]},
					fields=["name", "return_against", "grand_total"]):
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


def suggested_action(state, outstanding, pending_return_qty) -> str:
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
			parts.append(_("Close: goods returned and nothing left to collect"))
		return "; ".join(parts)
	if state == CONVERTED:
		return _("Bill or deliver the rest")
	return ""


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

	state = classify(order.status, days_overdue, paid_state, returned, converted, billed_in_full)
	return frappe._dict(
		sales_order=name,
		transaction_date=order.transaction_date,
		delivery_date=order.delivery_date,
		days_overdue=days_overdue,
		customer=order.customer,
		customer_name=order.customer_name,
		branch=order.get("branch"),
		status=order.status,
		tracking_state=state,
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
		returned_amount=flt(sum(abs(flt(r.grand_total)) for r in si_returns.values()), 3),
		pending_return_qty=pending_qty,
		can_close=1 if order.status in CLOSABLE_STATUSES else 0,
		suggested_action=suggested_action(state, outstanding, pending_qty),
	)


def wanted(row, filters) -> bool:
	state = filters.get("tracking_state")
	if state == NEEDS_ATTENTION and not row.needs_attention:
		return False
	if state and state != NEEDS_ATTENTION and row.tracking_state != state:
		return False
	if filters.get("payment_state") and row.payment_state != filters.payment_state:
		return False
	if cint(filters.get("has_return")) and not row.has_return:
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
		{"label": _("Unpaid On Open Orders"), "datatype": "Currency", "currency": currency, "indicator": "red",
			"value": flt(sum(r.outstanding_amount for r in rows if r.tracking_state not in (CLOSED, COMPLETED)), 3)},
	]


def get_columns() -> list:
	def col(fieldname, label, fieldtype, width=110, options=None):
		return {"fieldname": fieldname, "label": _(label), "fieldtype": fieldtype, "width": width, "options": options}

	return [
		col("sales_order", "Sales Order", "Link", 150, "Sales Order"),
		col("transaction_date", "Order Date", "Date", 95),
		col("customer", "Customer", "Link", 130, "Customer"),
		col("customer_name", "Customer Name", "Data", 180),
		col("branch", "Branch", "Link", 100, "Branch"),
		col("delivery_date", "Delivery Date", "Date", 95),
		col("days_overdue", "Days Overdue", "Int", 90),
		col("status", "ERPNext Status", "Data", 130),
		col("tracking_state", "Tracking State", "Data", 150),
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
		col("pending_return_qty", "Qty Awaiting DN Return", "Float", 120),
		col("suggested_action", "Suggested Action", "Data", 340),
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
