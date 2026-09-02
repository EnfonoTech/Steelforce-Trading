# Copyright (c) 2026, Enfono Technologies and contributors
# For license information, please see license.txt

from frappe import _

from sf_trading.open_items import (
	document_columns,
	fold_to_documents,
	invoiced_items_to_be_delivered,
	report_columns,
	shows_documents,
)


def execute(filters=None):
	filters = filters or {}
	rows = invoiced_items_to_be_delivered(filters)

	# Document Rows is the same answer folded, never a second query: the totals a Number Card
	# reads off the item view are the totals it reads off this one.
	if shows_documents(filters):
		return (
			document_columns(
				"Sales Invoice", "Customer", "delivered_qty", _("Delivered Qty"), _("Invoice Total")
			),
			fold_to_documents(rows, "Sales Invoice", total_field="base_grand_total"),
		)

	return report_columns("Sales Invoice", "Customer", "delivered_qty", _("Delivered Qty")), rows
