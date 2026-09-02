# Copyright (c) 2026, Enfono Technologies and contributors
# For license information, please see license.txt

"""Sales Orders raised and not yet invoiced, by item row or by order.

The order layer of the open-item family. Its siblings all begin at a delivery or an invoice, so
an order that was placed and never billed showed up nowhere; core deleted its own version of
this report in v13 (`erpnext/patches/v13_0/delete_old_sales_reports.py`), leaving Sales Order
Analysis, which knows nothing about branch or ageing.

`Delivered Items Pending Billing` overlaps deliberately and does not contradict: it keys on the
Delivery Note, this keys on the order, and an order whose goods went out on a note appears in
both — here with its Delivered Qty column filled, so the reader can see which pending orders
have already shipped.
"""

from frappe import _

from sf_trading.open_items import (
	document_columns,
	fold_to_documents,
	ordered_items_pending_billing,
	report_columns,
	shows_documents,
)


# Where the order's own billed figure disagrees with the invoices, or something about the order
# changes what the row means — a draft invoice already raised, an advance received, a Closed
# order, a foreign currency. Said in words rather than resolved silently.
REMARKS_COLUMN = {"label": _("Remarks"), "fieldname": "remarks", "fieldtype": "Data", "width": 320}


def execute(filters=None):
	filters = filters or {}
	rows = ordered_items_pending_billing(filters)

	if shows_documents(filters):
		columns = document_columns(
			"Sales Order", "Customer", "billed_qty", _("Billed Qty"), _("Order Total")
		)
		# Delivered Qty rides along in the folded view too: it is summed by fold_to_documents
		# because the item rows carry it
		columns.insert(-2, {
			"label": _("Delivered Qty"),
			"fieldname": "delivered_qty",
			"fieldtype": "Float",
			"width": 110,
		})
		columns.append(REMARKS_COLUMN)
		return columns, fold_to_documents(rows, "Sales Order", total_field="base_grand_total")

	columns = report_columns("Sales Order", "Customer", "billed_qty", _("Billed Qty"))
	columns.insert(-2, {
		"label": _("Delivered Qty"),
		"fieldname": "delivered_qty",
		"fieldtype": "Float",
		"width": 110,
	})
	columns.append(REMARKS_COLUMN)
	return columns, rows
