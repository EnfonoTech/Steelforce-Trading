# Copyright (c) 2026, Enfono Technologies and contributors
# For license information, please see license.txt

"""How the Cost of Goods Sold on a Delivery Note or Sales Invoice was reached.

One block per voucher: the COGS the stock ledger gives, what the GL booked for it (Cost of Goods Sold,
or Stock Delivered But Not Billed for a Delivery Note the company holds until invoiced) and the difference; under it one row per item line (warehouse, valuation method, qty,
rate, COGS); under each line the documents the warehouse stock was made of, pro rata
(sf_trading/cogs_breakdown.py). When stock reached the warehouse by a transfer the source row is that
transfer, with a link to Transfer Cost Breakdown -- this report never expands the source warehouse.

A Sales Invoice that moved no stock itself is answered by the Delivery Notes it billed. A return line
shows as negative COGS, without a source breakdown. Read only: it recomputes on every open.
"""

from collections import defaultdict

import frappe
from frappe import _
from frappe.utils import add_days, flt, getdate, nowdate

from sf_trading import cogs_breakdown as cb

VOUCHER_TYPES = ("Delivery Note", "Sales Invoice")
#: past this many item lines the source documents are left out -- pick a voucher to see them
MAX_EXPANDED_LINES = 300


def execute(filters=None):
	filters = frappe._dict(filters or {})
	if not filters.company:
		frappe.throw(_("Company is required"))
	if filters.voucher_no and not filters.voucher_type:
		frappe.throw(_("Pick the Voucher Type of {0}.").format(filters.voucher_no))
	for doctype in VOUCHER_TYPES:
		frappe.has_permission(doctype, "read", throw=True)

	message = None
	vouchers = get_vouchers(filters)
	if filters.voucher_no and filters.voucher_type == "Sales Invoice" and not vouchers:
		# an invoice that billed a Delivery Note: the COGS belongs to the note
		notes = frappe.get_all("Sales Invoice Item", filters={"parent": filters.voucher_no,
			"delivery_note": ["is", "set"]}, pluck="delivery_note", distinct=True)
		vouchers = [("Delivery Note", n) for n in notes if frappe.db.get_value("Delivery Note", n, "docstatus") == 1]
		if vouchers:
			message = _("{0} moved no stock itself; its cost is on the Delivery Notes it billed: {1}.").format(
				filters.voucher_no, ", ".join(v[1] for v in vouchers))
		else:
			# invoiced ahead of delivery (sbnd.py): the cost is booked at the rate frozen on the row,
			# and the stock -- with the breakdown -- moves on the Delivery Note that follows
			message = _("{0} moved no stock and billed no Delivery Note. GL Booked: {1}. Its stock leaves on "
				"the Delivery Note made from it; open the breakdown from there.").format(
				filters.voucher_no, frappe.format(cb.gl_cogs("Sales Invoice", filters.voucher_no), "Currency"))

	lines = []
	for voucher_type, voucher_no in vouchers:
		for e in cb.voucher_entries(voucher_type, voucher_no):
			if filters.item_code and e.item_code != filters.item_code:
				continue
			if filters.warehouse and e.warehouse != filters.warehouse:
				continue
			lines.append(e)

	expand = len(lines) <= MAX_EXPANDED_LINES
	if not expand:
		message = _("{0} item lines: source documents are shown for one voucher at a time. "
			"Pick a Voucher No to see them.").format(len(lines))

	currency = frappe.get_cached_value("Company", filters.company, "default_currency")
	rows = build_rows(lines, filters, expand, currency)
	return get_columns(), rows, message, None, get_summary(rows, currency)


def get_vouchers(filters) -> list:
	if filters.voucher_no:
		exists = frappe.db.exists("Stock Ledger Entry", {"voucher_type": filters.voucher_type,
			"voucher_no": filters.voucher_no, "is_cancelled": 0})
		return [(filters.voucher_type, filters.voucher_no)] if exists else []

	conditions = ["company = %(company)s", "is_cancelled = 0", "voucher_type in %(types)s",
		"posting_date between %(from_date)s and %(to_date)s"]
	values = {"company": filters.company,
		"types": [filters.voucher_type] if filters.voucher_type else list(VOUCHER_TYPES),
		"from_date": getdate(filters.from_date or add_days(nowdate(), -30)),
		"to_date": getdate(filters.to_date or nowdate())}
	if filters.item_code:
		conditions.append("item_code = %(item)s")
		values["item"] = filters.item_code
	if filters.warehouse:
		conditions.append("warehouse = %(warehouse)s")
		values["warehouse"] = filters.warehouse
	rows = frappe.db.sql(
		f"""select distinct voucher_type, voucher_no from `tabStock Ledger Entry`
		where {" and ".join(conditions)} order by posting_date desc, voucher_no desc""",
		values,
	)
	if filters.customer:
		rows = [r for r in rows if frappe.db.get_value(r[0], r[1], "customer") == filters.customer]
	return [tuple(r) for r in rows]


def build_rows(lines, filters, expand, currency):
	by_voucher = defaultdict(list)
	for e in lines:
		by_voucher[(e.voucher_type, e.voucher_no)].append(e)

	rows = []
	for (voucher_type, voucher_no), entries in by_voucher.items():
		cogs = sum(-flt(e.stock_value_difference) for e in entries)
		booked = flt(cb.gl_cogs(voucher_type, voucher_no))
		rows.append({
			"indent": 0, "doc_type": voucher_type, "document": voucher_no,
			"posting_date": entries[0].posting_date,
			"customer": frappe.db.get_value(voucher_type, voucher_no, "customer"),
			"value": cogs, "gl_cogs": booked, "difference": cogs - booked, "currency": currency,
		})
		for e in entries:
			qty = -flt(e.actual_qty)
			value = -flt(e.stock_value_difference)
			rows.append({
				"indent": 1, "item_code": e.item_code,
				"item_name": frappe.get_cached_value("Item", e.item_code, "item_name"),
				"warehouse": e.warehouse, "valuation_method": cb.valuation_method(e.item_code),
				"qty": qty, "rate": (value / qty) if qty else 0, "value": value, "currency": currency,
			})
			# a return brings stock back at the original rate: there is nothing to break down
			if not expand or e.actual_qty >= 0:
				continue
			for s in cb.compact(cb.split(cb.sources_before(e), qty)):
				transfer = cb.is_transfer(s.voucher_type, s.voucher_no)
				rows.append({
					"indent": 2, "doc_type": s.voucher_type, "document": s.voucher_no,
					"posting_date": s.posting_date, "warehouse": e.warehouse,
					"source_type": _(cb.source_label(s.voucher_type, s.voucher_no))
					if s.voucher_type else _("Other documents ({0})").format(s.others),
					"qty": s.qty, "rate": s.rate, "value": s.value, "currency": currency,
					"breakdown": 1 if transfer else 0, "company": filters.company,
				})
	return rows


def get_summary(rows, currency):
	vouchers = [r for r in rows if r["indent"] == 0]
	cogs = sum(flt(r["value"]) for r in vouchers)
	booked = sum(flt(r["gl_cogs"]) for r in vouchers)
	return [
		{"label": _("Vouchers"), "value": len(vouchers), "datatype": "Int"},
		{"label": _("COGS"), "value": cogs, "datatype": "Currency", "currency": currency},
		{"label": _("GL Booked"), "value": booked, "datatype": "Currency", "currency": currency},
		{"label": _("Difference"), "value": cogs - booked, "datatype": "Currency", "currency": currency,
		 "indicator": "Green" if abs(cogs - booked) < 0.01 else "Red"},
	]


def get_columns():
	return [
		{"label": _("Type"), "fieldname": "doc_type", "fieldtype": "Link", "options": "DocType", "width": 170},
		{"label": _("Document"), "fieldname": "document", "fieldtype": "Dynamic Link", "options": "doc_type", "width": 180},
		{"label": _("Date"), "fieldname": "posting_date", "fieldtype": "Date", "width": 95},
		{"label": _("Customer"), "fieldname": "customer", "fieldtype": "Link", "options": "Customer", "width": 160},
		{"label": _("Item"), "fieldname": "item_code", "fieldtype": "Link", "options": "Item", "width": 120},
		{"label": _("Item Name"), "fieldname": "item_name", "fieldtype": "Data", "width": 160},
		{"label": _("Warehouse"), "fieldname": "warehouse", "fieldtype": "Link", "options": "Warehouse", "width": 120},
		{"label": _("Valuation Method"), "fieldname": "valuation_method", "fieldtype": "Data", "width": 120},
		{"label": _("Source"), "fieldname": "source_type", "fieldtype": "Data", "width": 120},
		{"label": _("Qty"), "fieldname": "qty", "fieldtype": "Float", "precision": 3, "width": 80},
		{"label": _("Rate"), "fieldname": "rate", "fieldtype": "Currency", "options": "currency", "precision": 4, "width": 110},
		{"label": _("COGS / Value"), "fieldname": "value", "fieldtype": "Currency", "options": "currency", "width": 120},
		{"label": _("GL Booked"), "fieldname": "gl_cogs", "fieldtype": "Currency", "options": "currency", "width": 110},
		{"label": _("Difference"), "fieldname": "difference", "fieldtype": "Currency", "options": "currency", "width": 100},
		{"label": _("Cost Breakdown"), "fieldname": "breakdown", "fieldtype": "Data", "width": 140},
		{"label": _("Currency"), "fieldname": "currency", "fieldtype": "Link", "options": "Currency", "hidden": 1, "width": 80},
	]
