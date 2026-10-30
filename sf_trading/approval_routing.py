# sf_trading/approval_routing.py
"""Two approvals routed through permission_manager's PM Workflow engine.

**Sales Order and Quotation cancellation.** A submitted order is not cancelled by whoever presses Cancel. A sales
user asks for it -- Actions > Request Cancellation on the order, with a reason -- and a Sales
Manager approves it from the Approvals page or the order itself, at which point the engine cancels
the order, or rejects it, which puts the order back as it was. Submitting an order is untouched:
the workflow governs submitted orders only (`workflow_applicability`), so a draft submits exactly
as before. The reason lands in the order's own Cancellation Remark, the field the cancel guard in
sales_order_governance.py already demands. A Quotation goes through the same request, in its own
Quotation Cancellation workflow (`ensure_quotation_workflow`).

**Stock Reconciliation approval.** A stock reconciliation (purpose "Stock Reconciliation", not an
opening-stock load) goes to the Inventory Head and then to the Operations Head (2026-10-13, client:
a second approval, Shafeek) before it posts, and needs an attachment -- the count sheet behind it --
before it can be sent, approved, or submitted any other way. Direct submit is refused while the
workflow is active (`guard_submit`), so the approval cannot be stepped around. The Operations Head's
approval is the one that posts it, and the engine posts as the approving user, so the role carries the
read / write / submit on Stock Reconciliation it needs (`grant_second_approver_access`).

The workflows themselves are site records, the way every other approval on this site is kept;
`ensure_workflows` creates them (idempotently) on a site that asks for them, and
`ensure_operations_approval` adds the second approval to one that already exists. Who holds the
Operations Head role is a site decision: nothing here assigns it.
"""

import frappe
from frappe import _
from frappe.utils import cint, cstr

SALES_ORDER = "Sales Order"
QUOTATION = "Quotation"
STOCK_RECONCILIATION = "Stock Reconciliation"

SO_WORKFLOW = "Sales Order Cancellation"
QTN_WORKFLOW = "Quotation Cancellation"
#: the documents cancelled through a request, and the workflow that carries it
CANCELLATION_WORKFLOWS = {SALES_ORDER: SO_WORKFLOW, QUOTATION: QTN_WORKFLOW}
SUBMITTED = "Submitted"
REQUESTED = "Cancellation Requested"
CANCELLED = "Cancelled"
REQUEST = "Request Cancellation"
APPROVE = "Approve Cancellation"
REJECT = "Reject Cancellation"
REMARK_FIELD = "custom_cancellation_remark"
REASON_FIELD = "custom_cancellation_reason"
APPROVER_ROLE = "Sales Manager"
REQUESTER_ROLES = ("Sales User", "Counter Sale", "Branch Head")

SR_WORKFLOW = "Stock Reconciliation Approval"
SR_PURPOSE = "Stock Reconciliation"
SR_APPROVER_ROLE = "Inventory Head"
SR_PREPARER_ROLES = ("Stock User", "Stock Manager")
#: the second approval, after the Inventory Head's
SR_SECOND_STATE = "Pending Operations Head"
SR_SECOND_ROLE = "Operations Head"
#: what the role needs on a Stock Reconciliation for the engine to post it as that user (it saves and
#: then submits as the approver); no create, no cancel, no amend
SR_SECOND_PERMISSIONS = ("read", "write", "submit")

# indicator colour for a Workflow State this module has to create
STATE_STYLES = {SUBMITTED: "Primary", REQUESTED: "Warning", CANCELLED: "Danger", "Pending": "Warning",
	SR_SECOND_STATE: "Warning", "Approved": "Success", "Rejected": "Danger",
	# the Customer / Supplier / Item approval (master_approval.py)
	"Pending Approval": "Warning"}


def _active(doctype) -> bool:
	return bool(frappe.db.exists("PM Workflow", {"document_type": doctype, "is_active": 1}))


def workflow_applicability(doctype: str, doc=None):
	"""Answer permission_manager's `pm_workflow_applicability` hook for the two doctypes here.

	Both are routed in part, so both are `conditional`: they stay off the boot-time list of
	wholly-governed doctypes, and the engine asks about each document.
	"""
	if doctype in CANCELLATION_WORKFLOWS:
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
	"""Sales Order and Quotation before_update_after_submit: a cancellation request carries its reason.

	The engine moves the order into Cancellation Requested with a save; the comment given with the
	action reaches the document through `doc.flags.pm_workflow_comment` (permission_manager's
	apply_workflow) and becomes the Cancellation Remark. A request with no reason is refused. A
	rejected request clears the remark, so the next request -- or a later direct cancel -- has to
	give its own.
	"""
	before = doc.get_doc_before_save()
	was = before.get("workflow_state") if before else None
	now = doc.get("workflow_state")
	if doc.flags.get("pm_workflow_action") or REQUESTED in (was, now):
		# runs before the save's own validation: a document raised before a field became mandatory
		# (Sales Person, say) must still be able to ask for, and get, its cancellation. The engine
		# does not flag its own saves, so a move into or out of Cancellation Requested is the sign.
		doc.flags.ignore_mandatory = True
	if now == REQUESTED and was != REQUESTED:
		reason = cstr(doc.flags.get("pm_workflow_comment")).strip() or cstr(doc.get(REMARK_FIELD)).strip()
		if not reason:
			frappe.throw(
				_("Give a reason with the cancellation request."), title=_("Cancellation Reason Required")
			)
		# the reason itself is picked from the Order Cancellation Reason list by the order's
		# Request Cancellation button (order_cancellation.request_sales_order_cancellation)
		if doc.meta.has_field(REASON_FIELD) and not cstr(doc.get(REASON_FIELD)).strip():
			frappe.throw(
				_("Use the Request Cancellation button on the order to pick a cancellation reason."),
				title=_("Cancellation Reason Required"),
			)
		doc.set(REMARK_FIELD, reason)
	elif was == REQUESTED and now == SUBMITTED:
		doc.set(REMARK_FIELD, None)
		if doc.meta.has_field(REASON_FIELD):
			doc.set(REASON_FIELD, None)


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


#: PM shows a transition only while its condition holds. The reason is stamped by the order's own
#: Request Cancellation button (order_cancellation.request_sales_order_cancellation) just before it
#: applies this transition, so PM's own Actions entry -- which has no reason picker -- never shows.
REQUEST_CONDITION = 'doc.get("custom_cancellation_reason")'


def ensure_request_condition():
	"""after_migrate: an existing cancellation workflow gets the request condition too
	(ensure_workflows never touches a workflow that already exists)."""
	for doctype, workflow in CANCELLATION_WORKFLOWS.items():
		if not frappe.db.exists("PM Workflow", workflow):
			continue
		rows = frappe.get_all("PM Workflow Transition", filters={"parent": workflow, "parenttype": "PM Workflow",
			"action": REQUEST}, fields=["name", "condition"])
		changed = False
		for row in rows:
			if not cstr(row.condition).strip():
				frappe.db.set_value("PM Workflow Transition", row.name, "condition", REQUEST_CONDITION,
					update_modified=False)
				changed = True
		if changed:
			frappe.clear_cache(doctype=doctype)


def ensure_quotation_workflow():
	"""after_migrate: a site that cancels its Sales Orders through a request gets the same for Quotations.

	Follows the Sales Order's: only where that workflow exists and is active, and never over a Quotation
	Cancellation someone has already set up or changed."""
	if frappe.db.exists("PM Workflow", QTN_WORKFLOW):
		return
	if not frappe.db.exists("PM Workflow", {"name": SO_WORKFLOW, "is_active": 1}):
		return
	definition = quotation_workflow()
	_ensure_masters(definition)
	# The engine stamps every existing Quotation's workflow_state with set_value, which also moves its
	# `modified` / `modified_by` to now and the deploying user: every quotation would read as edited
	# today, and a cancelled one as cancelled today. Put both back as they were.
	stamps = frappe.db.sql("select name, modified, modified_by from `tabQuotation`", as_dict=True)
	frappe.get_doc(definition).insert(ignore_permissions=True)
	for row in stamps:
		frappe.db.sql("update `tabQuotation` set modified = %s, modified_by = %s where name = %s",
			(row.modified, row.modified_by, row.name))


def sales_order_workflow() -> dict:
	return _cancellation_workflow(SO_WORKFLOW, SALES_ORDER)


def quotation_workflow() -> dict:
	return _cancellation_workflow(QTN_WORKFLOW, QUOTATION)


def _cancellation_workflow(workflow_name: str, doctype: str) -> dict:
	transitions = [_transition(SUBMITTED, REQUEST, REQUESTED, role, require_comment=1, condition=REQUEST_CONDITION)
		for role in REQUESTER_ROLES]
	transitions += [
		_transition(REQUESTED, APPROVE, CANCELLED, APPROVER_ROLE),
		_transition(REQUESTED, REJECT, SUBMITTED, APPROVER_ROLE),
	]
	return {
		"doctype": "PM Workflow",
		"workflow_name": workflow_name,
		"document_type": doctype,
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
		# the Inventory Head hands over to the Operations Head; only that second approval posts it
		_transition("Pending", "Approve", SR_SECOND_STATE, SR_APPROVER_ROLE, allow_self_approval=0, require_attachment=1),
		_transition("Pending", "Reject", "Rejected", SR_APPROVER_ROLE, allow_self_approval=0, require_comment=1,
			is_return_for_correction=1),
		_transition(SR_SECOND_STATE, "Approve", "Approved", SR_SECOND_ROLE, allow_self_approval=0, require_attachment=1),
		_transition(SR_SECOND_STATE, "Reject", "Rejected", SR_SECOND_ROLE, allow_self_approval=0, require_comment=1,
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
			_state(SR_SECOND_STATE, 0, SR_SECOND_ROLE),
			_state("Approved", 1, SR_APPROVER_ROLE),
			_state("Rejected", 0, "Stock User"),
		],
		"transitions": transitions,
	}


def _ensure_masters(definition):
	"""The roles, states and actions a workflow names, for a site that has not used them yet."""
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


def grant_second_approver_access():
	"""The Operations Head role exists and may read, write and submit a Stock Reconciliation: the engine
	saves and submits as the approving user, so without it the approval fails at the very last step.
	Idempotent; the same way permission_manager gives the roles of its own workflows what they need."""
	from frappe.permissions import add_permission, update_permission_property

	if not frappe.db.exists("Role", SR_SECOND_ROLE):
		frappe.get_doc({"doctype": "Role", "role_name": SR_SECOND_ROLE, "desk_access": 1}).insert(ignore_permissions=True)
	add_permission(STOCK_RECONCILIATION, SR_SECOND_ROLE, 0)
	for ptype in SR_SECOND_PERMISSIONS:
		update_permission_property(STOCK_RECONCILIATION, SR_SECOND_ROLE, 0, ptype, 1, validate=False)


def ensure_operations_approval() -> bool:
	"""Add the Operations Head's second approval to a Stock Reconciliation Approval workflow that
	already exists (`ensure_workflows` never touches one, so a site that has it needs this once --
	patches/v0_1/stock_reconciliation_operations_approval.py).

	The Inventory Head's Approve now hands over to the new state instead of posting; the Operations
	Head's Approve posts and its Reject returns the count to whoever prepared it, like the first one's.
	Idempotent, and a workflow somebody has since reshaped (no second state to add, or no plain
	Inventory-Head-approves-and-posts transition to hand over from) is left exactly as it is.
	Returns whether it changed anything."""
	if not frappe.db.exists("PM Workflow", SR_WORKFLOW):
		return False
	workflow = frappe.get_doc("PM Workflow", SR_WORKFLOW)
	if any(row.state == SR_SECOND_STATE for row in workflow.states):
		return False
	handover = [t for t in workflow.transitions
		if t.state == "Pending" and t.action == "Approve" and t.next_state == "Approved"]
	if not handover:
		frappe.logger("sf_trading").warning("%s has no Pending -> Approved approval to hand over from; left alone", SR_WORKFLOW)
		return False

	second = stock_reconciliation_workflow()
	added_states = [row for row in second["states"] if row["state"] == SR_SECOND_STATE]
	added_transitions = [row for row in second["transitions"] if row["state"] == SR_SECOND_STATE]
	_ensure_masters({"states": added_states, "transitions": added_transitions})
	grant_second_approver_access()
	for transition in handover:
		transition.next_state = SR_SECOND_STATE
	for row in added_states:
		workflow.append("states", row)
	for row in added_transitions:
		workflow.append("transitions", row)
	workflow.save(ignore_permissions=True)
	frappe.clear_cache(doctype=STOCK_RECONCILIATION)
	return True


TRANSFER_WORKFLOW = "Stock Entry - Material Transfer"
TRANSFER_SEND = ("Draft", "Send for Acceptance")
TRANSFER_ACCEPT = ("Pending Acceptance", "Accept")
#: what each rewritten condition now contains, so a second run recognises its own work
TRANSFER_SEND_MARK = '.get("s_warehouse")'
TRANSFER_ACCEPT_MARK = 'doc.get("owner")'


def _default_keeper_of(row_field: str) -> str:
	"""Workflow-condition expression: the session user holds the DEFAULT Warehouse User Permission
	of the warehouse the first item row names in `row_field` -- the same test the Accept and Reject
	transitions already use for the receiving warehouse."""
	return ('frappe.db.get_value("User Permission", {"user": frappe.session.user, "allow": "Warehouse", '
		'"for_value": doc.get("items")[0].get("%s") if doc.get("items") else None, "is_default": 1}, "name")'
		% row_field)


def transfer_send_condition(current: str = "") -> str:
	"""Only the source warehouse's keeper sends a transfer for acceptance."""
	keeper = _default_keeper_of("s_warehouse")
	return "(%s) and %s" % (current, keeper) if current else keeper


def transfer_accept_condition(current: str = "") -> str:
	"""Nobody accepts a transfer they created themselves: the receiving keeper accepts, never the sender."""
	own = 'frappe.session.user != doc.get("owner")'
	return "%s and (%s)" % (own, current) if current else own


def ensure_transfer_segregation() -> bool:
	"""A stock transfer is sent by the keeper of the warehouse it leaves and accepted by a different
	person, the keeper of the warehouse it reaches (client, 2026-10-20: MAT-STE-2026-06511 was
	created and accepted by the same user, who keeps the receiving warehouse and only holds a plain
	permission on the source).

	The "Stock Entry - Material Transfer" workflow is a site record, so the two conditions are added
	to the transitions it already has, wrapped around whatever each carries now. Administrator is not
	gated by transition conditions, so a stuck transfer can always be moved. Idempotent: a condition
	that already carries its rule is left alone, and a site without the workflow is untouched.
	Rows are written directly -- saving the PM Workflow would also stamp every blank workflow_state
	(PMWorkflow.on_update). Returns whether it changed anything."""
	if not frappe.db.exists("PM Workflow", TRANSFER_WORKFLOW):
		return False
	rows = frappe.db.get_all("PM Workflow Transition",
		filters={"parent": TRANSFER_WORKFLOW, "parenttype": "PM Workflow"},
		fields=["name", "state", "action", "condition"])
	changed = False
	for key, mark, build in ((TRANSFER_SEND, TRANSFER_SEND_MARK, transfer_send_condition),
			(TRANSFER_ACCEPT, TRANSFER_ACCEPT_MARK, transfer_accept_condition)):
		for row in rows:
			current = cstr(row.condition).strip()
			if (row.state, row.action) != key or mark in current:
				continue
			frappe.db.set_value("PM Workflow Transition", row.name, "condition", build(current),
				update_modified=False)
			changed = True
	if changed:
		frappe.clear_document_cache("PM Workflow", TRANSFER_WORKFLOW)
		frappe.clear_cache(doctype="Stock Entry")
	return changed


def ensure_workflows():
	"""Create the two PM Workflows if this site does not have them yet. Never overwrites one that
	exists -- an approval someone has since adjusted on the site stays as they left it."""
	created = []
	for definition in (sales_order_workflow(), stock_reconciliation_workflow()):
		if frappe.db.exists("PM Workflow", definition["workflow_name"]):
			continue
		_ensure_masters(definition)
		if definition["workflow_name"] == SR_WORKFLOW:
			grant_second_approver_access()
		frappe.get_doc(definition).insert(ignore_permissions=True)
		created.append(definition["workflow_name"])
	return created
