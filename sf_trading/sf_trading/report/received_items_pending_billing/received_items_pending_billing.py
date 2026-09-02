# Copyright (c) 2026, Enfono Technologies and contributors
# For license information, please see license.txt

from frappe import _

from sf_trading.open_items import (
	document_columns,
	fold_to_documents,
	received_items_pending_billing,
	report_columns,
	shows_documents,
)


def execute(filters=None):
	filters = filters or {}
	rows = received_items_pending_billing(filters)

	# Document Rows is the same answer folded, never a second query: the totals a Number Card
	# reads off the item view are the totals it reads off this one.
	if shows_documents(filters):
		return (
			document_columns(
				"Purchase Receipt", "Supplier", "billed_qty", _("Billed Qty"), _("Receipt Total")
			),
			fold_to_documents(rows, "Purchase Receipt", total_field="base_grand_total"),
		)

	return report_columns("Purchase Receipt", "Supplier", "billed_qty", _("Billed Qty")), rows
