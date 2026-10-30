# Copyright (c) 2026, Enfono Technologies and contributors
# For license information, please see license.txt

"""How a Material Transfer's cost was reached in its source warehouse.

One row per transferred line: source and target warehouse, valuation method, qty, the transfer rate
(the source's outgoing rate, which is also the rate it arrives at in the target), value, and the
source balance just before it (qty, value, Moving Average rate). Under each line, the documents that
balance was made of -- receipts, purchases, a Stock Reconciliation, earlier transfers -- pro rata
(sf_trading/cogs_breakdown.py). An earlier transfer carries its own link back to this report, so a
chain is followed one hop at a time. Read only: it recomputes on every open.
"""

import frappe
from frappe import _
from frappe.utils import add_days, flt, getdate, nowdate

from sf_trading import cogs_breakdown as cb

#: past this many transfer lines the source documents are left out -- pick a Stock Entry to see them
MAX_EXPANDED_LINES = 300


def execute(filters=None):
	filters = frappe._dict(filters or {})
	if not filters.company:
		frappe.throw(_("Company is required"))
	frappe.has_permission("Stock Entry", "read", throw=True)

	legs = get_outgoing_legs(filters)
	expand = len(legs) <= MAX_EXPANDED_LINES
	message = None if expand else _(
		"{0} transfer lines: source documents are shown for one Stock Entry at a time. Pick a Stock Entry to see them."
	).format(len(legs))

	currency = frappe.get_cached_value("Company", filters.company, "default_currency")
	rows = []
	for e in legs:
		qty = -flt(e.actual_qty)
		value = -flt(e.stock_value_difference)
		before = cb.balance_before(e)
		rows.append({
			"indent": 0, "doc_type": "Stock Entry", "document": e.voucher_no, "posting_date": e.posting_date,
			"item_code": e.item_code, "item_name": frappe.get_cached_value("Item", e.item_code, "item_name"),
			"source_warehouse": e.warehouse, "target_warehouse": e.target_warehouse,
			"valuation_method": cb.valuation_method(e.item_code),
			"qty": qty, "rate": (value / qty) if qty else 0, "value": value,
			"balance_qty": before.qty, "balance_value": before.value, "balance_rate": before.rate,
			"currency": currency,
		})
		if not expand:
			continue
		for s in cb.compact(cb.split(cb.sources_before(e), qty)):
			transfer = cb.is_transfer(s.voucher_type, s.voucher_no)
			rows.append({
				"indent": 1, "doc_type": s.voucher_type, "document": s.voucher_no, "posting_date": s.posting_date,
				"source_type": _(cb.source_label(s.voucher_type, s.voucher_no))
					if s.voucher_type else _("Other documents ({0})").format(s.others),
				"qty": s.qty, "rate": s.rate, "value": s.value, "currency": currency,
				"breakdown": 1 if transfer else 0, "company": filters.company,
			})

	return get_columns(), rows, message, None, get_summary(legs, currency)


def get_outgoing_legs(filters) -> list:
	"""The source-warehouse leg of each Material Transfer line, with the line's target warehouse."""
	conditions = ["sle.company = %(company)s", "sle.is_cancelled = 0", "sle.voucher_type = 'Stock Entry'",
		"sle.actual_qty < 0", "se.purpose = 'Material Transfer'", "se.docstatus = 1"]
	values = {"company": filters.company}
	if filters.stock_entry:
		conditions.append("sle.voucher_no = %(stock_entry)s")
		values["stock_entry"] = filters.stock_entry
	else:
		conditions.append("sle.posting_date between %(from_date)s and %(to_date)s")
		values["from_date"] = getdate(filters.from_date or add_days(nowdate(), -30))
		values["to_date"] = getdate(filters.to_date or nowdate())
	for field, column in (("item_code", "sle.item_code"), ("source_warehouse", "sle.warehouse"),
	                      ("target_warehouse", "sed.t_warehouse")):
		if filters.get(field):
			conditions.append(f"{column} = %({field})s")
			values[field] = filters.get(field)
	if filters.item_group:
		conditions.append("exists (select 1 from `tabItem` i where i.name = sle.item_code and i.item_group = %(item_group)s)")
		values["item_group"] = filters.item_group

	return frappe.db.sql(
		f"""select {cb.sle_fields("sle")}, sed.t_warehouse as target_warehouse
		from `tabStock Ledger Entry` sle
		join `tabStock Entry` se on se.name = sle.voucher_no
		left join `tabStock Entry Detail` sed on sed.name = sle.voucher_detail_no
		where {" and ".join(conditions)}
		order by posted desc, sle.creation desc""",
		values,
		as_dict=True,
	)


def get_summary(legs, currency):
	value = sum(-flt(e.stock_value_difference) for e in legs)
	return [
		{"label": _("Transfers"), "value": len({e.voucher_no for e in legs}), "datatype": "Int"},
		{"label": _("Lines"), "value": len(legs), "datatype": "Int"},
		{"label": _("Value Transferred"), "value": value, "datatype": "Currency", "currency": currency},
	]


def get_columns():
	return [
		{"label": _("Type"), "fieldname": "doc_type", "fieldtype": "Link", "options": "DocType", "width": 170},
		{"label": _("Document"), "fieldname": "document", "fieldtype": "Dynamic Link", "options": "doc_type", "width": 180},
		{"label": _("Date"), "fieldname": "posting_date", "fieldtype": "Date", "width": 95},
		{"label": _("Item"), "fieldname": "item_code", "fieldtype": "Link", "options": "Item", "width": 120},
		{"label": _("Item Name"), "fieldname": "item_name", "fieldtype": "Data", "width": 160},
		{"label": _("Source Warehouse"), "fieldname": "source_warehouse", "fieldtype": "Link", "options": "Warehouse", "width": 120},
		{"label": _("Target Warehouse"), "fieldname": "target_warehouse", "fieldtype": "Link", "options": "Warehouse", "width": 120},
		{"label": _("Valuation Method"), "fieldname": "valuation_method", "fieldtype": "Data", "width": 120},
		{"label": _("Source"), "fieldname": "source_type", "fieldtype": "Data", "width": 120},
		{"label": _("Qty"), "fieldname": "qty", "fieldtype": "Float", "precision": 3, "width": 80},
		{"label": _("Rate"), "fieldname": "rate", "fieldtype": "Currency", "options": "currency", "precision": 4, "width": 110},
		{"label": _("Value"), "fieldname": "value", "fieldtype": "Currency", "options": "currency", "width": 120},
		{"label": _("Balance Qty Before"), "fieldname": "balance_qty", "fieldtype": "Float", "precision": 3, "width": 110},
		{"label": _("Balance Value Before"), "fieldname": "balance_value", "fieldtype": "Currency", "options": "currency", "width": 130},
		{"label": _("Average Rate Before"), "fieldname": "balance_rate", "fieldtype": "Currency", "options": "currency", "precision": 4, "width": 120},
		{"label": _("Cost Breakdown"), "fieldname": "breakdown", "fieldtype": "Data", "width": 140},
		{"label": _("Currency"), "fieldname": "currency", "fieldtype": "Link", "options": "Currency", "hidden": 1, "width": 80},
	]
