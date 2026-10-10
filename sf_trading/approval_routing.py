# sf_trading/approval_routing.py
"""Two approvals routed through permission_manager's PM Workflow engine.

**Sales Order cancellation.** A submitted order is not cancelled by whoever presses Cancel. A sales
user asks for it -- Actions > Request Cancellation on the order, with a reason -- and a Sales
Manager approves it from the Approvals page or the order itself, at which point the engine cancels
the order, or rejects it, which puts the order back as it was. Submitting an order is untouched:
the workflow governs submitted orders only (`workflow_applicability`), so a draft submits exactly
as before. The reason lands in the order's own Cancellation Remark, the field the cancel guard in
sales_order_governance.py already demands.

**Stock Reconciliation approval.** A stock reconciliation (purpose "Stock Reconciliation", not an
opening-stock load) goes to the Inventory Head before it posts, and needs an attachment -- the count
sheet behind it -- before it can be sent, approved, or submitted any other way. Direct submit is
refused while the workflow is active (`guard_submit`), so the approval cannot be stepped around.

The workflows themselves are site records, the way every other approval on this site is kept;
`ensure_workflows` creates them (idempotently) on a site that asks for them.
"""

import frappe
from frappe import _
from frappe.utils import cint, cstr

SALES_ORDER = "Sales Order"
STOCK_RECONCILIATION = "Stock Reconciliation"

SO_WORKFLOW = "Sales Order Cancellation"
SUBMITTED = "Submitted"
REQUESTED = "Cancellation Requested"
CANCELLED = "Cancelled"
REQUEST = "Request Cancellation"
APPROVE = "Approve Cancellation"
REJECT = "Reject Cancellation"
REMARK_FIELD = "custom_cancellation_remark"
APPROVER_ROLE = "Sales Manager"
REQUESTER_ROLES = ("Sales User", "Counter Sale", "Branch Head")

SR_WORKFLOW = "Stock Reconciliation Approval"
SR_PURPOSE = "Stock Reconciliation"
SR_APPROVER_ROLE = "Inventory Head"
SR_PREPARER_ROLES = ("Stock User", "Stock Manager")

# indicator colour for a Workflow State this module has to create
STATE_STYLES = {SUBMITTED: "Primary", REQUESTED: "Warning", CANCELLED: "Danger", "Pending": "Warning",
	"Approved": "Success", "Rejected": "Danger"}


def _active(doctype) -> bool:
	return bool(frappe.db.exists("PM Workflow", {"document_type": doctype, "is_active": 1}))


def workflow_applicability(doctype: str, doc=None):
	"""Answer permission_manager's `pm_workflow_applicability` hook for the two doctypes here.

	Both are routed in part, so both are `conditional`: they stay off the boot-time list of
	wholly-governed doctypes, and the engine asks about each document.
	"""
	if doctype == SALES_ORDER:
		if doc is None:
			return {"conditional": True}
		return {"conditional": True, "applies": cint(doc.get("docstatus")) >= 1}
	if doctype == STOCK_RECONCILIATION:
		if doc is None:
			return {"conditional": True}
		governed = doc.get("purpose") == SR_PURPOSE and _active(STOCK_RECONCILIATION)
		return {"conditional": True, "applies": governed, "guard_submit": governed}
	return None


# ── Sales Order cancellation ────────────────────────────────────────────────────────────────────


def capture_cancellation_reason(doc, _method=None):
	"""Sales Order before_update_after_submit: a cancellation request carries its reason.

	The engine moves the order into Cancellation Requested with a save; the comment given with the
	action reaches the document through `doc.flags.pm_workflow_comment` (permission_manager's
	apply_workflow) and becomes the Cancellation Remark. A request with no reason is refused. A
	rejected request clears the remark, so the next request -- or a later direct cancel -- has to
	give its own.
	"""
	before = doc.get_doc_before_save()
	was = before.get("workflow_state") if before else None
	now = doc.get("workflow_state")
	if now == REQUESTED and was != REQUESTED:
		reason = cstr(doc.flags.get("pm_workflow_comment")).strip() or cstr(doc.get(REMARK_FIELD)).strip()
		if not reason:
			frappe.throw(
				_("Give a reason with the cancellation request."), title=_("Cancellation Reason Required")
			)
		doc.set(REMARK_FIELD, reason)
	elif was == REQUESTED and now == SUBMITTED:
		doc.set(REMARK_FIELD, None)


# ── Stock Reconciliation attachment ─────────────────────────────────────────────────────────────


def require_attachment(doc, _method=None):
	"""Stock Reconciliation before_submit: a reconciliation posts only with its count sheet attached.

	The workflow already asks for it at Send for Approval; this is the same rule for every other
	route to submit. Opening-stock loads (and Administrator's migration scripts) are exempt.
	"""
	if doc.get("purpose") != SR_PURPOSE or frappe.session.user == "Administrator":
		return
	if not frappe.db.exists("File", {"attached_to_doctype": doc.doctype, "attached_to_name": doc.name}):
		frappe.throw(
			_("Attach the count sheet (or other supporting document) before this Stock Reconciliation is submitted."),
			title=_("Attachment Required"),
		)


# ── the workflows ───────────────────────────────────────────────────────────────────────────────


def _state(state, doc_status, allow_edit, optional=0):
	return {"state": state, "doc_status": str(doc_status), "is_optional_state": optional,
		"edit_permission_type": "Role", "allow_edit": allow_edit, "avoid_status_override": 1}


def _transition(state, action, next_state, role, **extra):
	return dict({"state": state, "action": action, "next_state": next_state, "approver_type": "Role",
		"allowed": role, "allow_self_approval": 1}, **extra)


def sales_order_workflow() -> dict:
	transitions = [_transition(SUBMITTED, REQUEST, REQUESTED, role, require_comment=1) for role in REQUESTER_ROLES]
	transitions += [
		_transition(REQUESTED, APPROVE, CANCELLED, APPROVER_ROLE),
		_transition(REQUESTED, REJECT, SUBMITTED, APPROVER_ROLE),
	]
	return {
		"doctype": "PM Workflow",
		"workflow_name": SO_WORKFLOW,
		"document_type": SALES_ORDER,
		"is_active": 1,
		"override_status": 1,
		"workflow_state_field": "workflow_state",
		"states": [
			# no action is created for a plain submitted order: nobody is waiting on it
			_state(SUBMITTED, 1, "Sales User", optional=1),
			_state(REQUESTED, 1, APPROVER_ROLE),
			_state(CANCELLED, 2, APPROVER_ROLE),
		],
		"transitions": transitions,
	}


def stock_reconciliation_workflow() -> dict:
	transitions = []
	for role in SR_PREPARER_ROLES:
		transitions.append(_transition("Draft", "Send for Approval", "Pending", role, require_attachment=1))
		transitions.append(_transition("Rejected", "Send for Approval", "Pending", role, require_attachment=1))
	transitions += [
		_transition("Pending", "Approve", "Approved", SR_APPROVER_ROLE, allow_self_approval=0, require_attachment=1),
		_transition("Pending", "Reject", "Rejected", SR_APPROVER_ROLE, allow_self_approval=0, require_comment=1,
			is_return_for_correction=1),
	]
	return {
		"doctype": "PM Workflow",
		"workflow_name": SR_WORKFLOW,
		"document_type": STOCK_RECONCILIATION,
		"is_active": 1,
		"workflow_state_field": "workflow_state",
		"states": [
			_state("Draft", 0, "Stock User"),
			_state("Pending", 0, SR_APPROVER_ROLE),
			_state("Approved", 1, SR_APPROVER_ROLE),
			_state("Rejected", 0, "Stock User"),
		],
		"transitions": transitions,
	}


def ensure_workflows():
	"""Create the two PM Workflows if this site does not have them yet. Never overwrites one that
	exists -- an approval someone has since adjusted on the site stays as they left it."""
	created = []
	for definition in (sales_order_workflow(), stock_reconciliation_workflow()):
		if frappe.db.exists("PM Workflow", definition["workflow_name"]):
			continue
		for role in {t["allowed"] for t in definition["transitions"]} | {s["allow_edit"] for s in definition["states"]}:
			if not frappe.db.exists("Role", role):
				frappe.get_doc({"doctype": "Role", "role_name": role, "desk_access": 1}).insert(ignore_permissions=True)
		# states and actions are links to the shared masters, which a site only has for the
		# names someone has already used
		for state in {s["state"] for s in definition["states"]}:
			if not frappe.db.exists("Workflow State", state):
				frappe.get_doc({"doctype": "Workflow State", "workflow_state_name": state,
					"style": STATE_STYLES.get(state, "")}).insert(ignore_permissions=True)
		for action in {t["action"] for t in definition["transitions"]}:
			if not frappe.db.exists("Workflow Action Master", action):
				frappe.get_doc({"doctype": "Workflow Action Master", "workflow_action_name": action}).insert(
					ignore_permissions=True
				)
		frappe.get_doc(definition).insert(ignore_permissions=True)
		created.append(definition["workflow_name"])
	return created
