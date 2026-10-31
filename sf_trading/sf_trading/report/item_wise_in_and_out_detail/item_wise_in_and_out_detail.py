# Copyright (c) 2026, Enfono Technologies and contributors
# For license information, please see license.txt

"""Item-wise In and Out Detail: the invoice lines behind Item-wise In and Out, one row each -- the
purchase or sale, its date, party and payment mode, and for a sale when the stock it took was bought
and how many days that was (first in, first out). Opened from a row of Item-wise In and Out with its
filters, or on its own; the rules are that report's (item_wise_in_and_out.py), read through the same
`collect`, so these lines add up to the figure they were opened from.
"""

import frappe
from frappe import _
from frappe.utils import flt

from sf_trading.sf_trading.report.item_wise_in_and_out.item_wise_in_and_out import (
	IN,
	collect,
	get_summary,
)

#: past this many lines the first ones are shown -- pick an Item or a shorter period for the rest
MAX_LINES = 5000


def execute(filters=None):
	lines, currency = collect(filters)
	lines.sort(key=lambda r: (r.item_code, r.posted, r.side))
	message = None
	if len(lines) > MAX_LINES:
		message = _("{0} invoice lines: the first {1} are shown. Pick an Item or a shorter period to see "
			"them all.").format(len(lines), MAX_LINES)
	rows = [line_row(line, currency) for line in lines[:MAX_LINES]]
	return get_columns(), rows, message, None, get_summary(lines, currency)


def line_row(line, currency) -> dict:
	row = {
		"item_code": line.item_code, "item_name": line.item_name, "side": line.side,
		"voucher_type": line.voucher_type, "document": line.document, "posting_date": line.posting_date,
		"party": line.party, "payment_mode": line.payment_mode, "currency": currency,
	}
	if line.side == IN:
		row.update(in_qty=flt(line.stock_qty), in_amount=flt(line.base_net_amount))
	else:
		row.update(out_qty=flt(line.stock_qty), out_amount=flt(line.base_net_amount))
		# a return brings stock back: it was not held, so it has no turn around
		if not line.is_return:
			row.update(purchase_date=line.purchase_date, tat_days=line.tat_days, unmatched_qty=line.unmatched_qty)
	return row


def get_columns():
	def money(fieldname, label, width=115):
		return {"fieldname": fieldname, "label": label, "fieldtype": "Currency", "options": "currency", "width": width}

	return [
		{"fieldname": "item_code", "label": _("Item"), "fieldtype": "Link", "options": "Item", "width": 140},
		{"fieldname": "item_name", "label": _("Item Name"), "fieldtype": "Data", "width": 180},
		{"fieldname": "side", "label": _("In / Out"), "fieldtype": "Data", "width": 90},
		{"fieldname": "voucher_type", "label": _("Voucher Type"), "fieldtype": "Link", "options": "DocType", "width": 120},
		{"fieldname": "document", "label": _("Document"), "fieldtype": "Dynamic Link", "options": "voucher_type", "width": 150},
		{"fieldname": "posting_date", "label": _("Date"), "fieldtype": "Date", "width": 95},
		{"fieldname": "party", "label": _("Supplier / Customer"), "fieldtype": "Data", "width": 180},
		{"fieldname": "payment_mode", "label": _("Payment Mode"), "fieldtype": "Data", "width": 120},
		{"fieldname": "in_qty", "label": _("In Qty"), "fieldtype": "Float", "precision": 3, "width": 90},
		money("in_amount", _("In Amount")),
		{"fieldname": "out_qty", "label": _("Out Qty"), "fieldtype": "Float", "precision": 3, "width": 90},
		money("out_amount", _("Out Amount")),
		{"fieldname": "purchase_date", "label": _("Purchased On"), "fieldtype": "Date", "width": 105},
		{"fieldname": "tat_days", "label": _("Turn Around (Days)"), "fieldtype": "Float", "precision": 1, "width": 125},
		{"fieldname": "unmatched_qty", "label": _("Qty Without Purchase"), "fieldtype": "Float", "precision": 3, "width": 140},
		# last on purpose: a hidden column anywhere else shifts the grid's headers off their cells
		{"fieldname": "currency", "label": _("Currency"), "fieldtype": "Link", "options": "Currency", "hidden": 1},
	]
