# Copyright (c) 2026, Enfono Technologies and contributors
# For license information, please see license.txt

"""Item-wise In and Out: per item, what came in on Purchase Invoices and went out on Sales Invoices,
Cash or Credit, and the turn around time -- how many days the stock that was sold had been held
since it was bought.

One row per item. Its invoices are in Item-wise In and Out Detail, opened from a row the way the DCR
opens DCR Detail: the item, or one of its figures (In, Out, Cash Sales...), with this report's
filters carried over. Both reports read through `collect` below, so a figure here is the sum of the
lines it opens.

PAYMENT MODE
------------
Sales Invoice: its own Payment Mode (Cash / Credit / Cheque), read the way Branch Bonus Eligibility
reads it (bonus_eligibility.py), so one invoice is the same thing in both reports: an invoice with
none (the import) takes the mode of the invoice it returns, and failing that is Credit when its
customer is a credit customer (a credit limit above zero) and Cash otherwise.
Purchase Invoice: Cash when "Is Paid" is ticked (paid on the invoice), Credit otherwise.

Cheque has a column of its own. The site reads it both ways -- the credit limit and the Sales Order
credit documents treat it as post-dated credit, the bonus sheet as cash, the DCR on its own -- so
this report does not decide for the reader.

TURN AROUND TIME
----------------
First in, first out per item, company-wide: every sale is matched against the oldest purchased
quantity not yet matched to an earlier sale, and its turn around time is the days between the two,
averaged over the quantity matched. The match runs over the item's whole history up to To Date, so
a sale in the period can be matched to a purchase made before it. Quantities are in stock UOM.

What no purchase explains -- stock brought in by an opening balance, a Stock Reconciliation or a
transfer from another company -- is shown as "Qty Without Purchase" and left out of the average.
Returns are listed with their own (negative) quantities and do not take part in the matching.

Read only; one query per side, no per-row lookups.
"""

from collections import defaultdict, deque

import frappe
from frappe import _
from frappe.utils import date_diff, flt, get_first_day, getdate, nowdate

from sf_trading.bonus_eligibility import _credit_customers
from sf_trading.sf_trading.report.item_wise_cash_flow.item_wise_cash_flow import (
	get_permitted_scope,
	item_groups,
	visible,
)

IN, OUT = "In", "Out"
CASH, CREDIT, CHEQUE = "Cash", "Credit", "Cheque"

LINES_SQL = {
	"Purchase Invoice": """
		select line.name as line, doc.name as document, doc.posting_date,
			timestamp(doc.posting_date, doc.posting_time) as posted, doc.supplier as party, doc.branch,
			doc.is_return, if(doc.is_paid, 'Cash', 'Credit') as payment_mode,
			line.item_code, line.item_name, line.item_group, line.stock_qty, line.base_net_amount
		from `tabPurchase Invoice Item` line
		join `tabPurchase Invoice` doc on doc.name = line.parent
		where doc.docstatus = 1 and doc.company = %(company)s and doc.posting_date <= %(to_date)s
			and line.item_code in %(items)s
	""",
	"Sales Invoice": """
		select line.name as line, doc.name as document, doc.posting_date,
			timestamp(doc.posting_date, doc.posting_time) as posted, doc.customer as party, doc.branch,
			doc.is_return, doc.return_against, nullif(doc.custom_payment_mode, '') as payment_mode,
			line.item_code, line.item_name, line.item_group, line.stock_qty, line.base_net_amount
		from `tabSales Invoice Item` line
		join `tabSales Invoice` doc on doc.name = line.parent
		where doc.docstatus = 1 and doc.company = %(company)s and doc.posting_date <= %(to_date)s
			and line.item_code in %(items)s
	""",
}

#: the items with a line in the period -- only their history is read
ITEMS_SQL = """
	select distinct line.item_code from `tab{doctype} Item` line
	join `tab{doctype}` doc on doc.name = line.parent
	where doc.docstatus = 1 and doc.company = %(company)s
		and doc.posting_date between %(from_date)s and %(to_date)s
		and (%(item_code)s = '' or line.item_code = %(item_code)s)
"""


def execute(filters=None):
	lines, currency = collect(filters)
	return get_columns(), build_rows(lines, currency), None, None, get_summary(lines, currency)


def collect(filters):
	"""The invoice lines in the period that pass the filters, turn around time stamped, and the currency."""
	filters = frappe._dict(filters or {})
	if not filters.company:
		frappe.throw(_("Company is required"))
	filters.from_date = getdate(filters.from_date or get_first_day(nowdate()))
	filters.to_date = getdate(filters.to_date or nowdate())
	if filters.from_date > filters.to_date:
		frappe.throw(_("From Date cannot be after To Date"))

	can_buy = frappe.has_permission("Purchase Invoice", "read")
	can_sell = frappe.has_permission("Sales Invoice", "read")
	if not (can_buy or can_sell):
		frappe.throw(_("Not permitted to read Sales or Purchase Invoices"), frappe.PermissionError)
	sides = {"Purchase Invoice": can_buy, "Sales Invoice": can_sell}

	history = read_history(filters, sides)
	match_fifo(history)
	lines = [line for line in history if in_period(line, filters)]
	return lines, frappe.get_cached_value("Company", filters.company, "default_currency")


def read_history(filters, sides) -> list:
	"""Every invoice line, both sides, of the items moved in the period, up to To Date."""
	params = {"company": filters.company, "from_date": filters.from_date, "to_date": filters.to_date,
		"item_code": filters.item_code or ""}
	items = set()
	for doctype, allowed in sides.items():
		if allowed:
			items.update(frappe.db.sql_list(ITEMS_SQL.format(doctype=doctype), params))
	if not items:
		return []

	params["items"] = tuple(items)
	scope = get_permitted_scope()
	branches = set(filters.get("branch") or [])
	groups = item_groups(filters.item_group) if filters.get("item_group") else None
	history = []
	for doctype, allowed in sides.items():
		if not allowed:
			continue
		for line in frappe.db.sql(LINES_SQL[doctype], params, as_dict=True):
			if groups is not None and line.item_group not in groups:
				continue
			line.voucher_type = doctype
			line.side = IN if doctype == "Purchase Invoice" else OUT
			# branch and party scope are left to in_period: an earlier purchase in another branch
			# still explains how long the stock was held
			line.visible = visible(line, doctype, scope, branches, None)
			history.append(line)
	set_sales_modes([line for line in history if line.side == OUT])
	return history


def set_sales_modes(lines):
	"""A sales line with no Payment Mode: its returned invoice's, else Credit for a credit customer, else Cash."""
	blank = [line for line in lines if not line.payment_mode]
	if not blank:
		return
	originals = {line.return_against for line in blank if line.is_return and line.return_against}
	original_modes = dict(frappe.get_all("Sales Invoice", filters={"name": ["in", list(originals)]},
		fields=["name", "custom_payment_mode"], as_list=True)) if originals else {}
	credit_customers = _credit_customers({line.party for line in blank})
	for line in blank:
		mode = original_modes.get(line.return_against) if line.is_return else None
		line.payment_mode = mode or (CREDIT if line.party in credit_customers else CASH)


def match_fifo(history):
	"""Stamp each sale line with its turn around time, first in first out per item."""
	by_item = defaultdict(list)
	for line in history:
		by_item[line.item_code].append(line)
	for lines in by_item.values():
		# a purchase and a sale at the same moment: the purchase first
		lines.sort(key=lambda r: (r.posted, 0 if r.side == IN else 1))
		held = deque()  # [qty, purchase_date]
		for line in lines:
			qty = flt(line.stock_qty)
			if line.is_return or qty <= 0:
				continue
			if line.side == IN:
				held.append([qty, line.posting_date])
				continue
			matched_qty, matched_days, first = 0.0, 0.0, None
			while qty > 1e-9 and held:
				take = min(qty, held[0][0])
				days = date_diff(line.posting_date, held[0][1])
				first = first or held[0][1]
				matched_qty += take
				matched_days += take * days
				qty -= take
				held[0][0] -= take
				if held[0][0] <= 1e-9:
					held.popleft()
			line.matched_qty = matched_qty
			line.matched_days = matched_days
			line.purchase_date = first
			line.unmatched_qty = max(qty, 0.0)
			line.tat_days = matched_days / matched_qty if matched_qty else None


def in_period(line, filters) -> bool:
	if not line.visible or not (filters.from_date <= line.posting_date <= filters.to_date):
		return False
	if filters.item_code and line.item_code != filters.item_code:
		return False
	if filters.side and line.side != filters.side:
		return False
	if filters.payment_mode and line.payment_mode != filters.payment_mode:
		return False
	if filters.customer and (line.side != OUT or line.party != filters.customer):
		return False
	if filters.supplier and (line.side != IN or line.party != filters.supplier):
		return False
	return True


def build_rows(lines, currency) -> list:
	by_item = defaultdict(list)
	for line in lines:
		by_item[line.item_code].append(line)

	rows = []
	for item_code in sorted(by_item):
		item_lines = sorted(by_item[item_code], key=lambda r: (r.posted, r.side))
		t = totals(item_lines)
		sides = {line.side for line in item_lines}
		rows.append({
			"item_code": item_code, "item_name": item_lines[0].item_name,
			# what the item's lines were, so the row can be read without opening them
			"side": _("In & Out") if len(sides) > 1 else next(iter(sides)),
			"payment_mode": " / ".join(sorted({line.payment_mode for line in item_lines})),
			"in_qty": t.in_qty, "in_amount": t.in_amount, "out_qty": t.out_qty, "out_amount": t.out_amount,
			"cash_in": t.cash_in, "credit_in": t.credit_in, "cash_out": t.cash_out, "cheque_out": t.cheque_out,
			"credit_out": t.credit_out,
			"tat_days": t.tat_days, "unmatched_qty": t.unmatched_qty, "currency": currency,
		})
	return rows


def totals(lines) -> frappe._dict:
	t = frappe._dict(in_qty=0.0, in_amount=0.0, out_qty=0.0, out_amount=0.0, cash_in=0.0, credit_in=0.0,
		cash_out=0.0, cheque_out=0.0, credit_out=0.0, unmatched_qty=0.0)
	matched_qty = matched_days = 0.0
	for line in lines:
		amount = flt(line.base_net_amount)
		if line.side == IN:
			t.in_qty += flt(line.stock_qty)
			t.in_amount += amount
			t["credit_in" if line.payment_mode == CREDIT else "cash_in"] += amount
		else:
			t.out_qty += flt(line.stock_qty)
			t.out_amount += amount
			t[{CREDIT: "credit_out", CHEQUE: "cheque_out"}.get(line.payment_mode, "cash_out")] += amount
			matched_qty += flt(line.get("matched_qty"))
			matched_days += flt(line.get("matched_days"))
			t.unmatched_qty += flt(line.get("unmatched_qty"))
	t.tat_days = matched_days / matched_qty if matched_qty else None
	return t


def get_summary(lines, currency):
	t = totals(lines)

	def money(label, value, indicator):
		return {"label": label, "value": flt(value, 3), "datatype": "Currency", "currency": currency,
			"indicator": indicator}

	return [
		{"label": _("Items"), "value": len({line.item_code for line in lines}), "datatype": "Int"},
		money(_("Purchases (In)"), t.in_amount, "Blue"),
		money(_("Cash Purchases"), t.cash_in, "Blue"),
		money(_("Credit Purchases"), t.credit_in, "Orange"),
		money(_("Sales (Out)"), t.out_amount, "Green"),
		money(_("Cash Sales"), t.cash_out, "Green"),
		money(_("Cheque Sales"), t.cheque_out, "Blue"),
		money(_("Credit Sales"), t.credit_out, "Orange"),
		{"label": _("Avg Turn Around (Days)"), "value": flt(t.tat_days, 1) if t.tat_days is not None else 0,
		 "datatype": "Float"},
	]


def get_columns():
	def money(fieldname, label, width=115):
		return {"fieldname": fieldname, "label": label, "fieldtype": "Currency", "options": "currency", "width": width}

	return [
		{"fieldname": "item_code", "label": _("Item"), "fieldtype": "Link", "options": "Item", "width": 160},
		{"fieldname": "item_name", "label": _("Item Name"), "fieldtype": "Data", "width": 180},
		{"fieldname": "side", "label": _("In / Out"), "fieldtype": "Data", "width": 90},
		{"fieldname": "payment_mode", "label": _("Payment Mode"), "fieldtype": "Data", "width": 120},
		{"fieldname": "in_qty", "label": _("In Qty"), "fieldtype": "Float", "precision": 3, "width": 90},
		money("in_amount", _("In Amount")),
		{"fieldname": "out_qty", "label": _("Out Qty"), "fieldtype": "Float", "precision": 3, "width": 90},
		money("out_amount", _("Out Amount")),
		{"fieldname": "tat_days", "label": _("Turn Around (Days)"), "fieldtype": "Float", "precision": 1, "width": 125},
		{"fieldname": "unmatched_qty", "label": _("Qty Without Purchase"), "fieldtype": "Float", "precision": 3, "width": 140},
		money("cash_in", _("Cash Purchases")),
		money("credit_in", _("Credit Purchases")),
		money("cash_out", _("Cash Sales")),
		money("cheque_out", _("Cheque Sales")),
		money("credit_out", _("Credit Sales")),
		# last on purpose: a hidden column anywhere else shifts the grid's headers off their cells
		{"fieldname": "currency", "label": _("Currency"), "fieldtype": "Link", "options": "Currency", "hidden": 1},
	]
