# sf_trading/sales_order_payment_mode.py
"""Payment Mode on the Sales Order, so the order says how it is meant to be settled.

The Sales Invoice has carried `custom_payment_mode` (Cash / Credit / Cheque) for a long time: it
is what decides which payment popup the cashier gets and whether the invoice is simply submitted
on credit. An order taken with the same intent had nowhere to record it, so an invoice raised
from that order arrived with the field empty -- and an empty mode is treated as "not Credit",
which means the cash popup, even for an order everybody agreed was on credit.

Adding the field with the *same fieldname* is all that is needed to carry it across:
`frappe.model.mapper.map_fields` copies every target field whose fieldname matches a source
field unless either side is `no_copy` (frappe/model/mapper.py:173), and neither side is. So
Sales Order -> Sales Invoice now brings the mode with it, with no mapper code at all.

The options carry a leading blank so the field is genuinely optional -- an order with no mode
behaves exactly as it did before, and leaves the invoice's own mode alone (the mapper skips a
value that is None or "").
"""

from frappe.custom.doctype.custom_field.custom_field import create_custom_fields

FIELD = "custom_payment_mode"


def ensure_custom_fields():
	"""after_migrate: the order-side twin of Sales Invoice's Payment Mode."""
	create_custom_fields(
		{
			"Sales Order": [
				{
					"fieldname": FIELD,
					"label": "Payment Mode",
					"fieldtype": "Select",
					"options": "\nCash\nCredit\nCheque",
					"insert_after": "delivery_date",
					"description": (
						"How the order is to be settled. Carried onto the Sales Invoice raised "
						"from it, and it decides which payment popup Receive Payment offers."
					),
				}
			]
		},
		ignore_validate=True,
	)
