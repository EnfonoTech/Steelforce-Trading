# sf_trading/order_cancellation.py
"""Why a Sales Order, Purchase Order or Quotation was cancelled: a reason picked from a master, as on returns.

The Sales Return pattern (sales_return_reason.py), applied to orders. "Order Cancellation Reason" is
the list of standard reasons; the order stores the picked one in `custom_cancellation_reason` and
the full sentence -- the reason, plus whatever was written about it -- in the existing
`custom_cancellation_remark`, which every report and guard already reads. A reason marked Needs
Details (and "Other", always) must be explained.

The reason is set by the cancel dialog (`set_cancellation_reason`), or for a Sales Order or a
Quotation by the Request Cancellation dialog (`request_cancellation`), which also starts the approval.
The before_cancel guards in sales_order_governance and purchase_order_cancel refuse a cancel that
carries no reason (`require_reason`); a cancel from a script or the API is held to the same rule.

A Quotation is cancelled the way a Sales Order is: only a Sales Manager cancels it, anyone else asks,
and it picks from the same reasons a Sales Order does (it is cancelled for the reasons the order it
would have become is).
"""

import frappe
from frappe import _
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields
from frappe.custom.doctype.property_setter.property_setter import make_property_setter
from frappe.utils import cint, cstr

MASTER = "Order Cancellation Reason"
REASON_FIELD = "custom_cancellation_reason"
REMARK_FIELD = "custom_cancellation_remark"
OTHER = "Other"
ORDER_DOCTYPES = ("Sales Order", "Purchase Order", "Quotation")
BOTH = "Sales and Purchase Orders"
#: the reasons each document may pick, by the reason's Applies To
REASON_SCOPE = {
	"Sales Order": (BOTH, "Sales Order"),
	"Purchase Order": (BOTH, "Purchase Order"),
	"Quotation": (BOTH, "Sales Order"),
}
#: documents only a Sales Manager cancels; anyone else requests it through the approval
REQUESTABLE = ("Sales Order", "Quotation")
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
	"""after_migrate: the picked reason on every document that takes one, beside the remark.

	The Sales Order and Purchase Order remark fields belong to their own modules (sales_order_governance,
	purchase_order_cancel); the Quotation's is made here, the same field the Sales Order has."""
	# a Quotation keeps no Version history by default, so who cancelled it (or asked to) was nowhere to read
	if not frappe.get_meta("Quotation").track_changes:
		make_property_setter("Quotation", None, "track_changes", 1, "Check", for_doctype=True,
			validate_fields_for_doctype=False)
	create_custom_fields({"Quotation": [{
		"fieldname": REMARK_FIELD,
		"label": "Cancellation Remark",
		"fieldtype": "Small Text",
		"insert_after": "status",
		"allow_on_submit": 1,
		"read_only": 1,
		"no_copy": 1,
	}]}, ignore_validate=True, update=True)
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
		frappe.throw(_("Only Sales Orders, Purchase Orders and Quotations take a cancellation reason."))
	reason = cstr(reason).strip()
	details = cstr(details).strip()
	if not reason:
		frappe.throw(_("Pick a cancellation reason."), title=_("Cancellation Reason Required"))
	values = frappe.db.get_value(MASTER, reason, ["applies_to", "needs_details", "disabled"], as_dict=True)
	if not values:
		frappe.throw(_("{0} is not an Order Cancellation Reason.").format(frappe.bold(reason)))
	if cint(values.disabled):
		frappe.throw(_("Cancellation reason {0} is disabled.").format(frappe.bold(reason)))
	if values.applies_to not in REASON_SCOPE[doctype]:
		frappe.throw(_("{0} is a reason for a {1}.").format(frappe.bold(reason), _(values.applies_to)))
	if (cint(values.needs_details) or reason == OTHER) and not details:
		frappe.throw(_("Write what happened: {0} needs details.").format(frappe.bold(reason)),
			title=_("Details Required"))
	return reason + (" - " + details if details else "")


def _order_values(doctype: str, name: str, fields: list):
	"""The order's own fields, `workflow_state` only where the doctype has the column.

	A Quotation gets `workflow_state` when its cancellation workflow is made (ensure_quotation_workflow),
	which a site that cancels its Sales Orders directly never does: selecting it there is a SQL error."""
	meta = frappe.get_meta(doctype)
	fields = [f for f in fields if f != "workflow_state" or meta.has_field(f)]
	return frappe.db.get_value(doctype, name, fields, as_dict=True) or frappe._dict()


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

	On a Sales Order or a Quotation only those who may cancel it (a Sales Manager) reach this; a pending
	request's reason is the requester's and is never rewritten here."""
	frappe.has_permission(doctype, "cancel", doc=name, throw=True)
	values = _order_values(doctype, name, ["docstatus", "workflow_state"])
	if cint(values.docstatus) != 1:
		frappe.throw(_("Only a submitted order can be cancelled."))
	if doctype in REQUESTABLE:
		from sf_trading.sales_order_governance import CANCEL_APPROVER_ROLES

		if not set(frappe.get_roles()) & set(CANCEL_APPROVER_ROLES):
			frappe.throw(_("Only a Sales Manager cancels a {0}. Use Request Cancellation.").format(_(doctype)),
				frappe.PermissionError)
		if values.get("workflow_state") == REQUESTED_STATE:
			# approving the request cancels it with the requester's own reason
			return frappe.db.get_value(doctype, name, REMARK_FIELD)
	return _stamp(doctype, name, reason, details)


#: statuses past which a document is no longer cancelled through a request
FINISHED = ("Closed", "Completed", "Ordered", "Partially Ordered")


def _requestable(doctype: str):
	if doctype not in REQUESTABLE:
		frappe.throw(_("A {0} is not cancelled through a request.").format(_(doctype)))


@frappe.whitelist()
def can_request(doctype: str, name: str) -> bool:
	"""Whether this user may send this document for cancellation -- the form shows its button only then."""
	from sf_trading.approval_routing import CANCELLATION_WORKFLOWS

	if doctype not in REQUESTABLE or not frappe.has_permission(doctype, "write", doc=name):
		return False
	if not frappe.db.exists("PM Workflow", {"name": CANCELLATION_WORKFLOWS[doctype], "is_active": 1}):
		return False
	values = _order_values(doctype, name, ["docstatus", "workflow_state", "status"])
	if not values or cint(values.docstatus) != 1 or values.workflow_state == REQUESTED_STATE:
		return False
	if values.status in FINISHED:
		return False
	return bool(set(frappe.get_roles()) & set(REQUESTER_ROLES + ("System Manager",)))


@frappe.whitelist()
def request_cancellation(doctype: str, name: str, reason: str, details: str = None) -> dict:
	"""The Request Cancellation dialog: record why and send the document to the Sales Manager."""
	from permission_manager.permission_manager.workflow import apply_workflow

	from sf_trading.approval_routing import REQUEST

	_requestable(doctype)
	frappe.has_permission(doctype, "write", doc=name, throw=True)
	if cint(frappe.db.get_value(doctype, name, "docstatus")) != 1:
		frappe.throw(_("Only a submitted {0} can be cancelled.").format(_(doctype)))
	remark = _stamp(doctype, name, reason, details)
	apply_workflow(frappe.get_doc(doctype, name).as_dict(), REQUEST, remark)
	return frappe.db.get_value(doctype, name, ["workflow_state", REASON_FIELD, REMARK_FIELD], as_dict=True)


@frappe.whitelist()
def can_request_cancellation(sales_order: str) -> bool:
	"""The Sales Order's own name for `can_request`, kept for callers that already use it."""
	return can_request("Sales Order", sales_order)


@frappe.whitelist()
def request_sales_order_cancellation(sales_order: str, reason: str, details: str = None) -> dict:
	"""The Sales Order's own name for `request_cancellation`, kept for callers that already use it."""
	return request_cancellation("Sales Order", sales_order, reason, details)


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
