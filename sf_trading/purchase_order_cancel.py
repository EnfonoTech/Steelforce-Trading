# sf_trading/purchase_order_cancel.py
"""A Purchase Order needs a remark before it can be cancelled -- the Sales Order rule, for buying.

Same shape as sales_order_governance's cancel remark (GS Issue 17): the remark lives in
`custom_cancellation_remark` on the order itself (allow_on_submit, read-only, no_copy), the desk asks
for it before the cancel call fires (public/js/sales_order_cancel.js), and the refusal lives here,
in `before_cancel`, because the desk is not the only way an order gets cancelled. `before_cancel`
runs before docstatus flips to 2, so throwing genuinely leaves the order standing.

Unlike the Sales Order there is no role restriction: who may cancel a Purchase Order is already
decided by role permissions and the Purchase Order approval workflow; the client asked for the
reason, not for a new gate.
"""

import frappe
from frappe import _
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields
from frappe.utils import cstr

REMARK_FIELD = "custom_cancellation_remark"


def ensure_custom_fields():
	"""after_migrate: the remark field on Purchase Order, if it is not already there."""
	create_custom_fields(
		{
			"Purchase Order": [
				{
					"fieldname": REMARK_FIELD,
					"label": "Cancellation Remark",
					"fieldtype": "Small Text",
					"insert_after": "status",
					"allow_on_submit": 1,
					"read_only": 1,
					"no_copy": 1,
					"depends_on": f"eval:doc.{REMARK_FIELD}",
				}
			]
		},
		ignore_validate=True,
		update=True,
	)


def before_cancel_require_remark(doc, _method=None):
	"""Purchase Order before_cancel: a remark is mandatory, and so is the reason picked from the
	Order Cancellation Reason list (sf_trading/order_cancellation.py)."""
	if not cstr(doc.get(REMARK_FIELD)).strip():
		frappe.throw(
			_("A remark is required to cancel a Purchase Order."),
			title=_("Cancellation Remark Required"),
		)
	from sf_trading.order_cancellation import require_reason

	require_reason(doc)
