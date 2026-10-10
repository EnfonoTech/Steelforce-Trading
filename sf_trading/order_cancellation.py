# sf_trading/order_cancellation.py
"""Why a Sales Order or Purchase Order was cancelled: a reason picked from a master, as on returns.

The Sales Return pattern (sales_return_reason.py), applied to orders. "Order Cancellation Reason" is
the list of standard reasons; the order stores the picked one in `custom_cancellation_reason` and
the full sentence -- the reason, plus whatever was written about it -- in the existing
`custom_cancellation_remark`, which every report and guard already reads. A reason marked Needs
Details (and "Other", always) must be explained.

The reason is set by the cancel dialog (`set_cancellation_reason`), or for a Sales Order by the
Request Cancellation dialog (`request_sales_order_cancellation`), which also starts the approval.
The before_cancel guards in sales_order_governance and purchase_order_cancel refuse a cancel that
carries no reason (`require_reason`); a cancel from a script or the API is held to the same rule.
"""

import frappe
from frappe import _
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields
from frappe.utils import cint, cstr

MASTER = "Order Cancellation Reason"
REASON_FIELD = "custom_cancellation_reason"
REMARK_FIELD = "custom_cancellation_remark"
OTHER = "Other"
ORDER_DOCTYPES = ("Sales Order", "Purchase Order")
BOTH = "Sales and Purchase Orders"
REQUESTED_STATE = "Cancellation Requested"
REQUESTER_ROLES = ("Sales User", "Counter Sale", "Branch Head")

#: reason, applies to, needs details, description
REASONS = (
	("Customer Request", "Sales Order", 0, "The customer asked for the order to be cancelled."),
	("Duplicate Order", BOTH, 0, "The same order was entered twice."),
	("Wrong Item or Quantity", BOTH, 0, "The order carries the wrong item, quantity or unit."),
	("Price Changed", BOTH, 0, "The agreed price changed, so the order is raised again."),
	("Stock Not Available", "Sales Order", 0, "The goods cannot be supplied from stock."),
	("Delivery Date Missed", BOTH, 0, "The order can no longer be delivered when it was needed."),
	("Supplier Cannot Supply", "Purchase Order", 0, "The supplier cannot deliver the goods."),
	("Ordered From Another Supplier", "Purchase Order", 0, "The goods are bought elsewhere instead."),
	("Entered by Mistake", BOTH, 1, "The order should never have been raised."),
	("Other", BOTH, 1, "Any reason not covered above -- the details must explain it."),
)


def seed_reasons():
	"""after_install / after_migrate: the standard reasons, never overwriting one."""
	if not frappe.db.table_exists(MASTER):
		return
	for reason, applies_to, needs_details, description in REASONS:
		if frappe.db.exists(MASTER, reason):
			continue
		frappe.get_doc({"doctype": MASTER, "reason": reason, "applies_to": applies_to,
			"needs_details": needs_details, "description": description}).insert(ignore_permissions=True)


def ensure_custom_fields():
	"""after_migrate: the picked reason on both orders, beside the remark."""
	field = {
		"fieldname": REASON_FIELD,
		"label": "Cancellation Reason",
		"fieldtype": "Link",
		"options": MASTER,
		"insert_after": REMARK_FIELD,
		"allow_on_submit": 1,
		"read_only": 1,
		"no_copy": 1,
		"depends_on": "eval:doc." + REASON_FIELD,
	}
	create_custom_fields({dt: [dict(field)] for dt in ORDER_DOCTYPES}, ignore_validate=True, update=True)


def compose_remark(doctype: str, reason: str, details: str = None) -> str:
	"""Check the reason fits this order and return the remark it makes."""
	if doctype not in ORDER_DOCTYPES:
		frappe.throw(_("Only Sales Orders and Purchase Orders take a cancellation reason."))
	reason = cstr(reason).strip()
	details = cstr(details).strip()
	if not reason:
		frappe.throw(_("Pick a cancellation reason."), title=_("Cancellation Reason Required"))
	values = frappe.db.get_value(MASTER, reason, ["applies_to", "needs_details", "disabled"], as_dict=True)
	if not values:
		frappe.throw(_("{0} is not an Order Cancellation Reason.").format(frappe.bold(reason)))
	if cint(values.disabled):
		frappe.throw(_("Cancellation reason {0} is disabled.").format(frappe.bold(reason)))
	if values.applies_to not in (BOTH, doctype):
		frappe.throw(_("{0} is a reason for a {1}.").format(frappe.bold(reason), _(values.applies_to)))
	if (cint(values.needs_details) or reason == OTHER) and not details:
		frappe.throw(_("Write what happened: {0} needs details.").format(frappe.bold(reason)),
			title=_("Details Required"))
	return reason + (" - " + details if details else "")


def _stamp(doctype, name, reason, details):
	remark = compose_remark(doctype, reason, details)
	# update_modified=False: the open form still holds its own `modified`, and a cancel that then
	# fails must not leave that form unable to save
	frappe.db.set_value(doctype, name, {REASON_FIELD: cstr(reason).strip(), REMARK_FIELD: remark},
		update_modified=False)
	return remark


@frappe.whitelist()
def set_cancellation_reason(doctype: str, name: str, reason: str, details: str = None) -> str:
	"""The cancel dialog: record why, just before the cancel itself is sent.

	On a Sales Order only those who may cancel it (a Sales Manager) reach this; a pending request's
	reason is the requester's and is never rewritten here."""
	frappe.has_permission(doctype, "cancel", doc=name, throw=True)
	values = frappe.db.get_value(doctype, name, ["docstatus", "workflow_state"], as_dict=True) or frappe._dict()
	if cint(values.docstatus) != 1:
		frappe.throw(_("Only a submitted order can be cancelled."))
	if doctype == "Sales Order":
		from sf_trading.sales_order_governance import CANCEL_APPROVER_ROLES

		if not set(frappe.get_roles()) & set(CANCEL_APPROVER_ROLES):
			frappe.throw(_("Only a Sales Manager cancels a Sales Order. Use Request Cancellation."), frappe.PermissionError)
		if values.get("workflow_state") == REQUESTED_STATE:
			# approving the request cancels it with the requester's own reason
			return frappe.db.get_value(doctype, name, REMARK_FIELD)
	return _stamp(doctype, name, reason, details)


@frappe.whitelist()
def can_request_cancellation(sales_order: str) -> bool:
	"""Whether this user may send this order for cancellation -- the form shows its button only then."""
	from sf_trading.approval_routing import SO_WORKFLOW

	if not frappe.has_permission("Sales Order", "write", doc=sales_order):
		return False
	if not frappe.db.exists("PM Workflow", {"name": SO_WORKFLOW, "is_active": 1}):
		return False
	values = frappe.db.get_value("Sales Order", sales_order, ["docstatus", "workflow_state", "status"], as_dict=True)
	if not values or cint(values.docstatus) != 1 or values.workflow_state == REQUESTED_STATE:
		return False
	if values.status in ("Closed", "Completed"):
		return False
	return bool(set(frappe.get_roles()) & set(REQUESTER_ROLES + ("System Manager",)))


@frappe.whitelist()
def request_sales_order_cancellation(sales_order: str, reason: str, details: str = None) -> dict:
	"""The Request Cancellation dialog: record why and send the order to the Sales Manager."""
	from permission_manager.permission_manager.workflow import apply_workflow

	from sf_trading.approval_routing import REQUEST

	frappe.has_permission("Sales Order", "write", doc=sales_order, throw=True)
	if cint(frappe.db.get_value("Sales Order", sales_order, "docstatus")) != 1:
		frappe.throw(_("Only a submitted Sales Order can be cancelled."))
	remark = _stamp("Sales Order", sales_order, reason, details)
	apply_workflow(frappe.get_doc("Sales Order", sales_order).as_dict(), REQUEST, remark)
	return frappe.db.get_value("Sales Order", sales_order, ["workflow_state", REASON_FIELD, REMARK_FIELD], as_dict=True)


def require_reason(doc):
	"""before_cancel (both orders): a cancel names its reason, once the field exists on the site."""
	doctype = doc.get("doctype")
	if doctype not in ORDER_DOCTYPES or not frappe.get_meta(doctype).has_field(REASON_FIELD):
		return
	if not cstr(doc.get(REASON_FIELD)).strip():
		frappe.throw(
			_("Pick a cancellation reason for {0} {1}.").format(_(doctype), frappe.bold(doc.get("name") or "")),
			title=_("Cancellation Reason Required"),
		)
