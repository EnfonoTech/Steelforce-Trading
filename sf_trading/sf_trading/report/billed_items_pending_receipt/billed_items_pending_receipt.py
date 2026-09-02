# Copyright (c) 2026, Enfono Technologies and contributors
# For license information, please see license.txt

from frappe import _

from sf_trading.open_items import (
	billed_items_pending_receipt,
	document_columns,
	fold_to_documents,
	report_columns,
	shows_documents,
)


def execute(filters=None):
	filters = filters or {}
	rows = billed_items_pending_receipt(filters)

	# Document Rows is the same answer folded, never a second query: the totals a Number Card
	# reads off the item view are the totals it reads off this one.
	if shows_documents(filters):
		return (
			document_columns(
				"Purchase Invoice", "Supplier", "received_qty", _("Received Qty"), _("Invoice Total")
			),
			fold_to_documents(rows, "Purchase Invoice", total_field="base_grand_total"),
		)

	return report_columns("Purchase Invoice", "Supplier", "received_qty", _("Received Qty")), rows
