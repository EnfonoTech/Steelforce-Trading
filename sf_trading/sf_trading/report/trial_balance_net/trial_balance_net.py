# Copyright (c) 2026, Enfono Technologies and contributors
# For license information, please see license.txt

"""Trial Balance Net: ERPNext's Trial Balance with one Opening and one Closing column.

The figures are ERPNext's own -- its execute() runs unchanged, with the same filters -- so this
report always ties to the standard Trial Balance to the last digit, and any fix to that report
reaches this one. Only the columns are reshaped:

    Opening (Dr), Opening (Cr)  ->  Opening = Opening (Dr) - Opening (Cr)
    Debit, Credit               ->  unchanged
    Closing (Dr), Closing (Cr)  ->  Closing = Closing (Dr) - Closing (Cr)

so a credit balance shows as a negative figure.
"""

from frappe import _
from frappe.utils import flt

from erpnext.accounts.report.trial_balance import trial_balance
from erpnext.accounts.utils import get_currency_precision

NET_COLUMNS = (
	("opening", "Opening", "opening_debit", "opening_credit"),
	("closing", "Closing", "closing_debit", "closing_credit"),
)


def execute(filters=None):
	columns, data = trial_balance.execute(filters)
	precision = get_currency_precision() or 2
	return get_columns(columns), [net_row(row, precision) for row in data]


def net_row(row, precision):
	"""Fold each Dr / Cr pair into one signed figure; the blank spacer row stays blank."""
	if not row:
		return row
	for fieldname, _label, debit, credit in NET_COLUMNS:
		if debit in row or credit in row:
			# rounded, and `or 0.0`: a balance that nets to nothing is 0, not a -0.000 float residue
			row[fieldname] = flt(flt(row.pop(debit, 0)) - flt(row.pop(credit, 0)), precision) or 0.0
	return row


def get_columns(columns):
	"""ERPNext's columns, with each Dr / Cr pair replaced by its one net column, in place."""
	pairs = {debit: (fieldname, label) for fieldname, label, debit, _credit in NET_COLUMNS}
	credits = {credit for *_rest, credit in NET_COLUMNS}
	out = []
	for col in columns:
		if col["fieldname"] in credits:
			continue
		if col["fieldname"] in pairs:
			fieldname, label = pairs[col["fieldname"]]
			col = dict(col, fieldname=fieldname, label=_(label), width=140)
		out.append(col)
	return out
