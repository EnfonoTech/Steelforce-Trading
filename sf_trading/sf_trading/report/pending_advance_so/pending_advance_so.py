# Copyright (c) 2026, Enfono Technologies and contributors
# For license information, please see license.txt

"""Sales Orders carrying a customer advance that no invoice has been booked against.

The sales-side twin of the Pending Advance PO report, and it reads the same way: money has
already been received against an order nobody has billed, so it is a liability sitting in an
advance account rather than income.

Where the advance comes from
----------------------------
`Sales Order.advance_paid` is a denormalised figure ERPNext refreshes from the `Advance
Payment Ledger Entry` table: `set_total_advance_paid` calls
`calculate_total_advance_from_ledger`, which sums the APLE rows whose `against_voucher_no` is
the order. Nothing is written to `GL Entry` or `Payment Ledger Entry` against a Sales Order,
so APLE is the only ledger that knows an order-level advance exists -- querying either of the
other two returns nothing and makes it look as though no advance was ever received.

This report reads the stored field, because that is what the order form shows, and a figure
nobody can reconcile against the document is worse than no figure. It re-sums APLE alongside
it and reports any disagreement in Remarks rather than quietly preferring one source, since
the field is only as fresh as the last time ERPNext refreshed it.

Currency
--------
`advance_paid` is denominated in `party_account_currency` -- the customer control account's
currency -- while `grand_total` is in the order's own currency. The two are not comparable
when they differ, so every money column here is the company's currency and the order's own
currency is only named, never totalled.

Current state only
------------------
APLE carries no posting date, so an order's advance cannot be rebuilt as of a past date the
way the other open-item reports rebuild billing state. The From/To window therefore bounds the
*order date*, and the advance is always its balance right now.
"""

import frappe
from frappe import _
from frappe.query_builder.functions import Sum
from frappe.utils import date_diff, flt, fmt_money, getdate, nowdate

from sf_trading.open_items import QTY_PRECISION, apply_user_permissions, posting_range

# Both sides of every comparison here are money. A half-thousandth is below the smallest unit
# any currency on this site is kept in, so anything under it is rounding rather than a real
# difference.
MONEY_TOLERANCE = 0.005


def execute(filters=None):
	filters = frappe._dict(filters or {})
	if not filters.get("company"):
		frappe.throw(_("Select a Company."))

	rows = pending_advance_orders(filters)
	return columns(rows), rows


def pending_advance_orders(filters):
	"""Orders with an advance against them and no submitted sales invoice."""
	company = filters.get("company")
	company_currency = frappe.get_cached_value("Company", company, "default_currency")

	ledger = advance_ledger_balances(company)
	orders = advance_orders(filters, ledger)
	if not orders:
		return []

	submitted, drafts = invoice_links(orders)

	rows = []
	for order in orders.values():
		# a submitted invoice is what stops an order being pending; everything else about it
		# -- delivery state, billed percentage -- is reported, not filtered on
		if submitted.get(order.sales_order):
			continue
		rows.append(
			build_row(
				order,
				ledger=ledger,
				drafts=sorted(drafts.get(order.sales_order) or ()),
				company_currency=company_currency,
			)
		)

	rows.sort(key=lambda row: flt(row["advance_paid"]), reverse=True)
	return rows


def advance_ledger_balances(company):
	"""{order: advance balance} read straight from the advance payment ledger.

	Mirrors `calculate_total_advance_from_ledger`, absolute value included: the rows carry the
	payment's own sign.

	Orders whose rows net to nothing are left out. That is the normal end state once an advance
	has been adjusted against an invoice, and keeping them would put every historically settled
	order into the candidate list.
	"""
	aple = frappe.qb.DocType("Advance Payment Ledger Entry")
	rows = (
		frappe.qb.from_(aple)
		.select(aple.against_voucher_no.as_("sales_order"), Sum(aple.amount).as_("net"))
		.where(
			(aple.company == company)
			& (aple.delinked == 0)
			& (aple.against_voucher_type == "Sales Order")
		)
		.groupby(aple.against_voucher_no)
		.run(as_dict=True)
	)

	balances = {}
	for row in rows:
		balance = abs(flt(row.net))
		if balance > MONEY_TOLERANCE:
			balances[row.sales_order] = balance
	return balances


def advance_orders(filters, ledger):
	"""Submitted orders carrying an advance, folded to one entry per order.

	Joined to the item table for two reasons: the delivered quantity lives there, and
	`apply_user_permissions` narrows on the item-level cost center and warehouse, so a branch
	user sees only their own orders. That yields one row per item, summed back into one entry
	per order here rather than in SQL, which keeps the query free of a GROUP BY over
	non-aggregated parent columns.
	"""
	so = frappe.qb.DocType("Sales Order")
	soi = frappe.qb.DocType("Sales Order Item")

	carries_advance = so.advance_paid > 0
	# an order whose stored field has gone stale still belongs in the answer when the advance
	# ledger says money is sitting against it
	if ledger:
		carries_advance = carries_advance | so.name.isin(list(ledger))

	query = (
		frappe.qb.from_(so)
		.join(soi)
		.on(so.name == soi.parent)
		.select(
			so.name.as_("sales_order"),
			so.company,
			so.customer,
			so.customer_name,
			so.transaction_date,
			so.status,
			so.currency,
			so.base_grand_total,
			so.advance_paid,
			so.per_billed,
			soi.qty,
			soi.delivered_qty,
		)
		.where((so.docstatus == 1) & (so.company == filters.get("company")) & carries_advance)
	)

	query = apply_user_permissions(query, so, soi, "Sales Order")

	# Closed orders are deliberately NOT excluded. A closed order has been abandoned rather
	# than fulfilled, so an advance still sitting against one is money taken for goods nobody
	# is going to receive -- the most urgent row on the report, not the least. Hiding it would
	# quietly drop it out of the total; the Status column and a remark name it instead.
	from_date, to_date = posting_range(filters)
	if from_date:
		query = query.where(so.transaction_date >= from_date)
	if to_date:
		query = query.where(so.transaction_date <= to_date)

	if filters.get("customer"):
		query = query.where(so.customer == filters.get("customer"))

	orders = {}
	for row in query.run(as_dict=True):
		order = orders.get(row.sales_order)
		if not order:
			order = orders[row.sales_order] = frappe._dict(
				sales_order=row.sales_order,
				company=row.company,
				customer=row.customer,
				customer_name=row.customer_name,
				transaction_date=row.transaction_date,
				status=row.status,
				currency=row.currency,
				base_grand_total=flt(row.base_grand_total),
				advance_paid=flt(row.advance_paid),
				per_billed=flt(row.per_billed),
				ordered_qty=0.0,
				delivered_qty=0.0,
			)
		order.ordered_qty += flt(row.qty)
		order.delivered_qty += flt(row.delivered_qty)

	return orders


def invoice_links(orders):
	"""({order: submitted invoices}, {order: draft invoices}) for the given orders.

	A submitted invoice is what makes an order stop being pending. A draft is not -- nothing is
	booked until it is submitted -- but it is worth naming, because whoever is chasing the
	invoice needs to know one is already sitting in someone's drafts rather than raising a
	second one.

	Three routes are read, because an invoice reaches an order by any of them: naming the order
	directly in `sales_order`, naming the order's own row in `so_detail`, or naming only the
	delivery row in `dn_detail` when the invoice was raised from a Delivery Note and the mapper
	did not carry the order link across. Miss the third and an order that has genuinely been
	invoiced would sit here open forever with no way to clear it.
	"""
	sii = frappe.qb.DocType("Sales Invoice Item")
	si = frappe.qb.DocType("Sales Invoice")
	soi = frappe.qb.DocType("Sales Order Item")
	dni = frappe.qb.DocType("Delivery Note Item")

	wanted = list(orders)
	rows = (
		frappe.qb.from_(sii)
		.join(si)
		.on(si.name == sii.parent)
		.left_join(soi)
		.on(soi.name == sii.so_detail)
		.left_join(dni)
		.on(dni.name == sii.dn_detail)
		.select(
			sii.sales_order,
			soi.parent.as_("linked_order"),
			dni.against_sales_order.as_("delivery_order"),
			si.name.as_("invoice"),
			si.docstatus,
		)
		.where(
			(si.docstatus < 2)
			& (
				sii.sales_order.isin(wanted)
				| soi.parent.isin(wanted)
				| dni.against_sales_order.isin(wanted)
			)
		)
		.run(as_dict=True)
	)

	submitted, drafts = {}, {}
	for row in rows:
		order = row.sales_order or row.linked_order or row.delivery_order
		if order not in orders:
			continue
		bucket = submitted if row.docstatus == 1 else drafts
		# an invoice names an order once per item row, so collect names not rows
		bucket.setdefault(order, set()).add(row.invoice)

	return submitted, drafts


def build_row(order, ledger, drafts, company_currency):
	order_value = flt(order.base_grand_total)
	advance = flt(order.advance_paid)

	return {
		"customer": order.customer,
		"customer_name": order.customer_name,
		"sales_order": order.sales_order,
		"transaction_date": order.transaction_date,
		"advance_paid": advance,
		# every Currency column reading Company:company:default_currency needs this
		"company": order.company,
		"status": order.status,
		"base_grand_total": order_value,
		"balance_amount": order_value - advance,
		"advance_pct": (advance / order_value * 100) if order_value else 0.0,
		"currency": order.currency,
		"delivered": delivered_state(order),
		"per_billed": flt(order.per_billed),
		"draft_invoice": drafts[0] if len(drafts) == 1 else None,
		"age": date_diff(nowdate(), getdate(order.transaction_date)),
		"remarks": remarks(order, ledger, drafts, company_currency),
	}


def delivered_state(order):
	"""Whether the goods the advance was taken for have gone out."""
	ordered = flt(order.ordered_qty, QTY_PRECISION)
	delivered = flt(order.delivered_qty, QTY_PRECISION)

	if delivered <= 0:
		return _("No")
	# nothing ordered cannot have been delivered in full, so do not let a zero-quantity order
	# fall through the >= test and claim it is complete
	if ordered <= 0:
		return _("Partial")
	if delivered >= ordered:
		return _("Yes")
	return _("Partial")


def remarks(order, ledger, drafts, company_currency):
	"""Anything about the row the figures alone would not tell the reader."""
	notes = []

	field_advance = flt(order.advance_paid)
	ledger_advance = flt(ledger.get(order.sales_order))
	if abs(field_advance - ledger_advance) > MONEY_TOLERANCE:
		notes.append(
			_("Order shows {0} but the advance ledger holds {1} - reconcile before acting on this row").format(
				fmt_money(field_advance, currency=company_currency),
				fmt_money(ledger_advance, currency=company_currency),
			)
		)

	# Every row here has no submitted invoice naming it, so a billed percentage means the
	# invoice was raised without the link. This report cannot see such an invoice and would
	# hold the row open forever, which is exactly why it says so.
	if flt(order.per_billed) >= 100:
		notes.append(_("Marked fully billed, but no sales invoice names this order"))

	if len(drafts) > 1:
		notes.append(_("{0} draft invoices already raised").format(len(drafts)))

	if order.status == "Closed":
		notes.append(_("Order is closed"))

	return "; ".join(notes)


def columns(rows=None):
	"""Report columns. Remarks only appears when a row actually has something to say."""
	remarked = any((row.get("remarks") or "") for row in (rows or ()))

	definitions = [
		{
			"label": _("Customer"),
			"fieldname": "customer",
			"fieldtype": "Link",
			"options": "Customer",
			"width": 210,
		},
		{
			"label": _("SO No."),
			"fieldname": "sales_order",
			"fieldtype": "Link",
			"options": "Sales Order",
			"width": 165,
		},
		{"label": _("SO Date"), "fieldname": "transaction_date", "fieldtype": "Date", "width": 95},
		{
			"label": _("Advance Received"),
			"fieldname": "advance_paid",
			"fieldtype": "Currency",
			"options": "Company:company:default_currency",
			"width": 145,
		},
		{
			"label": _("SO Amount"),
			"fieldname": "base_grand_total",
			"fieldtype": "Currency",
			"options": "Company:company:default_currency",
			"width": 125,
		},
		{
			"label": _("Balance to Collect"),
			"fieldname": "balance_amount",
			"fieldtype": "Currency",
			"options": "Company:company:default_currency",
			"width": 145,
		},
		{
			# a ratio has no meaningful sum, so it stays out of the total row
			"label": _("Advance %"),
			"fieldname": "advance_pct",
			"fieldtype": "Percent",
			"width": 95,
			"disable_total": 1,
		},
		{"label": _("Status"), "fieldname": "status", "fieldtype": "Data", "width": 135},
		{"label": _("Delivered"), "fieldname": "delivered", "fieldtype": "Data", "width": 90},
		{
			"label": _("Billed %"),
			"fieldname": "per_billed",
			"fieldtype": "Percent",
			"width": 85,
			"disable_total": 1,
		},
		{
			"label": _("Draft Invoice"),
			"fieldname": "draft_invoice",
			"fieldtype": "Link",
			"options": "Sales Invoice",
			"width": 145,
		},
		{
			# named, not totalled -- it tells the reader the money columns beside it are a
			# conversion, which matters on any order raised in another currency
			"label": _("Order Currency"),
			"fieldname": "currency",
			"fieldtype": "Link",
			"options": "Currency",
			"width": 110,
		},
		{
			"label": _("Age (Days)"),
			"fieldname": "age",
			"fieldtype": "Int",
			"width": 85,
			"disable_total": 1,
		},
	]

	if remarked:
		definitions.append(
			{"label": _("Remarks"), "fieldname": "remarks", "fieldtype": "Data", "width": 280}
		)

	return definitions
