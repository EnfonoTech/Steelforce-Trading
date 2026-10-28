# Copyright (c) 2026, Enfono Technologies and contributors
# For license information, please see license.txt

"""Every returned item line, sales or purchase, with its branch and why it came back.

A return is raised one of two ways on each side, and the report reads one of them at a time so a
return made through both (a credit note whose Delivery Note was returned too, or a debit note whose
Purchase Receipt return purchase_return.auto_create_pr_return made for it) is never counted twice:

    Return Through       Sales                     Purchase
    Credit / Debit Note  Sales Invoice return      Purchase Invoice return
    Delivery / Receipt   Delivery Note return      Purchase Receipt return

Quantities and amounts are shown positive (a return carries them negative); amounts are company
currency, net of VAT. The reason columns (Return Reason Template, Return/Debit Reason) exist only
on the invoices; the report reads them only where the document has the field.

Item Wise adds the item's sales (or purchases) through the same kind of document, with the same
filters and dates, so Return % reads as how much of what went out (or came in) in the period came
back. It compares stock quantities, so items sold in more than one UOM still add up.
"""

from collections import defaultdict

import frappe
from frappe import _
from frappe.utils import flt

from sf_trading.sales_target import allowed_branches

SALES, PURCHASE = "Sales", "Purchase"
INVOICE, STOCK = "Credit / Debit Note", "Delivery Note / Purchase Receipt"
LINE_WISE, ITEM_WISE = "Line Wise", "Item Wise"

SOURCES = {
	(SALES, INVOICE): ("Sales Invoice", "customer", "customer_name"),
	(SALES, STOCK): ("Delivery Note", "customer", "customer_name"),
	(PURCHASE, INVOICE): ("Purchase Invoice", "supplier", "supplier_name"),
	(PURCHASE, STOCK): ("Purchase Receipt", "supplier", "supplier_name"),
}


def execute(filters=None):
	filters = frappe._dict(filters or {})
	if not filters.company:
		frappe.throw(_("Company is required"))
	side = filters.return_type or SALES
	doctype, party_field, party_name_field = SOURCES[(side, filters.return_through or INVOICE)]
	frappe.has_permission(doctype, "read", throw=True)

	branches = allowed_branches()
	if filters.branch and branches is not None and filters.branch not in branches:
		frappe.throw(_("You are not permitted to see branch {0}.").format(frappe.bold(filters.branch)))
	if filters.branch:
		branches = [filters.branch]

	currency = frappe.get_cached_value("Company", filters.company, "default_currency")
	rows = get_lines(doctype, party_field, party_name_field, side, filters, branches, returns=True)
	for r in rows:
		r.currency = currency
	summary = get_summary(rows, currency)

	view = filters.view or LINE_WISE
	if view == ITEM_WISE:
		moved = get_lines(doctype, party_field, party_name_field, side, filters, branches, returns=False)
		rows = group_by_item(rows, moved, currency)
	return get_columns(view, doctype, side), rows, None, None, summary


def get_lines(doctype, party_field, party_name_field, side, filters, branches, returns):
	"""Submitted item lines of `doctype` -- its returns, or (returns=False) its ordinary documents."""
	meta = frappe.get_meta(doctype)

	def header(field, alias=None):
		return f"d.{field} AS {alias or field}" if meta.has_field(field) else f"NULL AS {alias or field}"

	conditions = ["d.docstatus = 1", "d.company = %(company)s", "d.is_return = %(is_return)s"]
	values = {"company": filters.company, "is_return": 1 if returns else 0}
	if filters.from_date:
		conditions.append("d.posting_date >= %(from_date)s")
		values["from_date"] = filters.from_date
	if filters.to_date:
		conditions.append("d.posting_date <= %(to_date)s")
		values["to_date"] = filters.to_date
	if branches is not None:
		# a document with no Branch cannot be told to be inside the user's branches
		if not branches or not meta.has_field("branch"):
			return []
		conditions.append("d.branch IN %(branches)s")
		values["branches"] = tuple(branches)
	party = filters.customer if side == SALES else filters.supplier
	if party:
		conditions.append(f"d.{party_field} = %(party)s")
		values["party"] = party
	if filters.item_code:
		conditions.append("r.item_code = %(item_code)s")
		values["item_code"] = filters.item_code
	if filters.item_group:
		# the group and everything under it, by the tree's own bounds
		lft, rgt = frappe.db.get_value("Item Group", filters.item_group, ["lft", "rgt"]) or (0, 0)
		conditions.append("ig.lft >= %(ig_lft)s AND ig.rgt <= %(ig_rgt)s")
		values["ig_lft"], values["ig_rgt"] = lft, rgt
	if filters.warehouse:
		conditions.append("r.warehouse = %(warehouse)s")
		values["warehouse"] = filters.warehouse
	if side == SALES and filters.sales_person:
		if not meta.has_field("custom_sales_person"):
			# a Delivery Note carries no sales person, so none of them match one (as with a reason below)
			return []
		conditions.append("d.custom_sales_person = %(sales_person)s")
		values["sales_person"] = filters.sales_person

	reason = filters.sales_return_reason if side == SALES else filters.purchase_return_reason
	if returns and reason:
		if not meta.has_field("custom_return_reason_template"):
			# a Delivery Note / Purchase Receipt return names no reason, so none of them match one
			return []
		conditions.append("d.custom_return_reason_template = %(reason)s")
		values["reason"] = reason

	return frappe.db.sql(
		f"""
		SELECT
			d.name AS voucher_no,
			%(doctype)s AS voucher_type,
			d.posting_date,
			d.return_against,
			{header("branch")},
			d.{party_field} AS party,
			d.{party_name_field} AS party_name,
			{header("custom_return_reason_template", "reason_template")},
			{header("custom_return_reason", "reason")},
			{header("custom_sales_person", "sales_person")},
			{header("custom_payment_mode", "payment_mode")},
			{header("bill_no")},
			r.item_code,
			r.item_name,
			it.item_group,
			r.warehouse,
			ABS(r.qty) AS qty,
			r.uom,
			ABS(r.stock_qty) AS stock_qty,
			r.stock_uom,
			r.base_net_rate AS rate,
			ABS(r.base_net_amount) AS amount
		FROM `tab{doctype}` d
		INNER JOIN `tab{doctype} Item` r ON r.parent = d.name AND r.parenttype = %(doctype)s
		LEFT JOIN `tabItem` it ON it.name = r.item_code
		LEFT JOIN `tabItem Group` ig ON ig.name = it.item_group
		WHERE {" AND ".join(conditions)}
		ORDER BY d.posting_date, d.name, r.idx
		""",
		{**values, "doctype": doctype},
		as_dict=True,
	)


def group_by_item(returns, moved, currency):
	"""One row per branch and item: what came back against what went out (or came in)."""
	out = {}

	def row(line):
		key = (line.branch, line.item_code)
		if key not in out:
			out[key] = frappe._dict(
				branch=line.branch, item_code=line.item_code, item_name=line.item_name,
				item_group=line.item_group, stock_uom=line.stock_uom, returned_qty=0.0,
				return_value=0.0, moved_qty=0.0, moved_value=0.0, currency=currency, vouchers=set(),
			)
		return out[key]

	for line in returns:
		r = row(line)
		r.returned_qty += flt(line.stock_qty)
		r.return_value += flt(line.amount)
		r.vouchers.add(line.voucher_no)
	moved_by_key = defaultdict(lambda: [0.0, 0.0])
	for line in moved:
		totals = moved_by_key[(line.branch, line.item_code)]
		totals[0] += flt(line.stock_qty)
		totals[1] += flt(line.amount)
	for key, r in out.items():
		# only the items that came back: an item never returned has nothing to report here
		r.moved_qty, r.moved_value = moved_by_key.get(key, (0.0, 0.0))
		r.returns = len(r.pop("vouchers"))
		r.return_percent = r.returned_qty / r.moved_qty * 100 if r.moved_qty else None

	return sorted(out.values(), key=lambda r: (-r.return_value, r.branch or "", r.item_code or ""))


def get_summary(rows, currency):
	return [
		{"label": _("Returns"), "value": len({r.voucher_no for r in rows}), "datatype": "Int"},
		{"label": _("Lines"), "value": len(rows), "datatype": "Int"},
		{"label": _("Items"), "value": len({r.item_code for r in rows}), "datatype": "Int"},
		{
			"label": _("Return Value"),
			"value": sum(flt(r.amount) for r in rows),
			"datatype": "Currency",
			"currency": currency,
			"indicator": "Red",
		},
	]


def get_columns(view, doctype, side):
	party_type = "Customer" if side == SALES else "Supplier"
	moved = _("Sold Qty") if side == SALES else _("Bought Qty")
	moved_value = _("Sales Value") if side == SALES else _("Purchase Value")

	if view == ITEM_WISE:
		return [
			{"label": _("Branch"), "fieldname": "branch", "fieldtype": "Link", "options": "Branch", "width": 100},
			{"label": _("Item"), "fieldname": "item_code", "fieldtype": "Link", "options": "Item", "width": 130},
			{"label": _("Item Name"), "fieldname": "item_name", "fieldtype": "Data", "width": 220},
			{"label": _("Item Group"), "fieldname": "item_group", "fieldtype": "Link", "options": "Item Group", "width": 140},
			{"label": _("Stock UOM"), "fieldname": "stock_uom", "fieldtype": "Link", "options": "UOM", "width": 90},
			# a return is counted once per item it carries, so this does not add up across rows (the
			# Returns card has the true count); nor does a per-item percentage
			{"label": _("Returns"), "fieldname": "returns", "fieldtype": "Int", "width": 80, "disable_total": 1},
			{"label": _("Returned Qty"), "fieldname": "returned_qty", "fieldtype": "Float", "width": 110},
			{"label": _("Return Value"), "fieldname": "return_value", "fieldtype": "Currency", "options": "currency", "width": 120},
			{"label": moved, "fieldname": "moved_qty", "fieldtype": "Float", "width": 110},
			{"label": moved_value, "fieldname": "moved_value", "fieldtype": "Currency", "options": "currency", "width": 120},
			{"label": _("Return %"), "fieldname": "return_percent", "fieldtype": "Percent", "width": 90, "disable_total": 1},
		]

	columns = [
		{"label": _("Date"), "fieldname": "posting_date", "fieldtype": "Date", "width": 100},
		{"label": _("Return No"), "fieldname": "voucher_no", "fieldtype": "Link", "options": doctype, "width": 150},
		{"label": _("Return Against"), "fieldname": "return_against", "fieldtype": "Link", "options": doctype, "width": 150},
		{"label": _("Branch"), "fieldname": "branch", "fieldtype": "Link", "options": "Branch", "width": 90},
		{"label": _(party_type), "fieldname": "party", "fieldtype": "Link", "options": party_type, "width": 150},
		{"label": _("{0} Name").format(_(party_type)), "fieldname": "party_name", "fieldtype": "Data", "width": 180},
		{"label": _("Item"), "fieldname": "item_code", "fieldtype": "Link", "options": "Item", "width": 130},
		{"label": _("Item Name"), "fieldname": "item_name", "fieldtype": "Data", "width": 200},
		{"label": _("Item Group"), "fieldname": "item_group", "fieldtype": "Link", "options": "Item Group", "width": 130},
		{"label": _("Warehouse"), "fieldname": "warehouse", "fieldtype": "Link", "options": "Warehouse", "width": 140},
		{"label": _("Qty"), "fieldname": "qty", "fieldtype": "Float", "width": 80},
		{"label": _("UOM"), "fieldname": "uom", "fieldtype": "Link", "options": "UOM", "width": 70},
		{"label": _("Rate"), "fieldname": "rate", "fieldtype": "Currency", "options": "currency", "width": 100, "disable_total": 1},
		{"label": _("Amount"), "fieldname": "amount", "fieldtype": "Currency", "options": "currency", "width": 110},
	]
	if frappe.get_meta(doctype).has_field("custom_return_reason_template"):
		reason_doctype = "Sales Return Reason" if side == SALES else "Purchase Return Reason"
		columns += [
			{"label": _("Return Reason Template"), "fieldname": "reason_template", "fieldtype": "Link", "options": reason_doctype, "width": 150},
			{"label": _("Return/Debit Reason"), "fieldname": "reason", "fieldtype": "Data", "width": 200},
		]
	if doctype == "Sales Invoice":
		columns += [
			{"label": _("Sales Person"), "fieldname": "sales_person", "fieldtype": "Link", "options": "Sales Person", "width": 130},
			{"label": _("Payment Mode"), "fieldname": "payment_mode", "fieldtype": "Data", "width": 100},
		]
	if doctype == "Purchase Invoice":
		columns.append({"label": _("Supplier Invoice No"), "fieldname": "bill_no", "fieldtype": "Data", "width": 140})
	return columns
