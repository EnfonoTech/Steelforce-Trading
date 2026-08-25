# sf_trading/sales_order_fields.py
"""The Sales Invoice's own fields, brought to the Sales Order.

An order and the invoice raised from it describe the same sale, and this site records three things
about a sale that ERPNext does not: who sold it, how it is being settled, and who is delivering it.
All three lived on the invoice only, so an order carried none of them and the invoice raised from it
started blank -- which for the payment mode meant a credit order producing an invoice that popped
the cash drawer.

Same fieldnames as the invoice, on purpose. `frappe.model.mapper.map_fields` copies every target
field whose fieldname matches a source field unless either side is `no_copy`
(frappe/model/mapper.py:173), so all three ride from order to invoice with no mapper code at all.

  * **Payment Mode** -- Cash / Credit / Cheque, with a leading blank so it stays optional. An order
    with none behaves exactly as before and leaves the invoice's own mode alone.
  * **Sales Person** -- mandatory, as it is on the invoice, and filled from the user's own Sales
    Person permission by public/js/sales_order_parity.js.
  * **Delivery Person** -- shown on a cash sale only, restricted to the drivers of the order's
    branch, and cleared when the mode is anything else (the field would otherwise merely hide and
    keep its value). The overdue-driver check the invoice runs applies here too.
"""

from frappe.custom.doctype.custom_field.custom_field import create_custom_fields

PAYMENT_MODE = "custom_payment_mode"
SALES_PERSON = "custom_sales_person"
DRIVER = "custom_driver"


def ensure_custom_fields():
	"""after_migrate: the order-side twins of the invoice's three fields."""
	create_custom_fields(
		{
			"Sales Order": [
				{
					"fieldname": SALES_PERSON,
					"label": "Sales Person",
					"fieldtype": "Link",
					"options": "Sales Person",
					"insert_after": "naming_series",
					"reqd": 1,
					# the field is filled from the user's own permission, so it must not then be
					# restricted by it -- exactly as on the invoice
					"ignore_user_permissions": 1,
				},
				{
					"fieldname": PAYMENT_MODE,
					"label": "Payment Mode",
					"fieldtype": "Select",
					"options": "\nCash\nCredit\nCheque",
					"insert_after": "delivery_date",
					"description": (
						"How the order is to be settled. Carried onto the Sales Invoice raised from "
						"it, and it decides which payment popup Receive Payment offers."
					),
				},
				{
					"fieldname": DRIVER,
					"label": "Delivery Person",
					"fieldtype": "Link",
					"options": "Driver",
					"insert_after": PAYMENT_MODE,
					"depends_on": 'eval:doc.custom_payment_mode=="Cash"',
					"in_standard_filter": 1,
					"description": "Only on a cash sale, and only the drivers of this order's branch.",
				},
			]
		},
		ignore_validate=True,
	)
