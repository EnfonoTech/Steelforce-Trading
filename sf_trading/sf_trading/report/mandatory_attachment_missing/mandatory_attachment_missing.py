# Copyright (c) 2026, Enfono Technologies and contributors
# For license information, please see license.txt

"""Mandatory Attachment Missing: vouchers that moved through a PM Workflow step requiring an
attachment, and have no attachment now.

Nothing about which vouchers need one is written here: it is read from PM Workflow itself -- every
active workflow with a transition ticked Require Attachment. The engine checks that box only at the
moment the step is taken (permission_manager workflow.apply_workflow), so a voucher still turns up
without a file when it was deleted afterwards, or when the step was taken by a route the check did
not cover.

Each completed step is resolved the way the engine resolves it, because the requirement often
depends on who took it and on the voucher: e.g. a Sales Return sent by Counter Sale needs a file,
the same step sent by Accounts User does not; a Purchase Order needs one unless it is verbal.
  * the step = a Completed PM Workflow Action (its state, its user); where it went = the next
    step's state, or the voucher's state now if it was the last one
  * the transitions that user could take from that state (role / user / approver matrix), with
    their conditions on the voucher (Administrator is not held to conditions, as in the engine),
    the first of each action by idx -- the one the engine would have run
  * required when every such transition into that next state is ticked Require Attachment

Only vouchers that actually went through a step are reported: drafts not yet sent, and old
vouchers stamped with a state when the workflow was installed, never skipped a requirement.
Conditions are evaluated on the voucher as it is now.
"""

import frappe
from frappe import _
from frappe.utils import get_fullname, getdate

from permission_manager.permission_manager.workflow import (
	get_allowed_transitions_for_user,
	get_workflow_name,
	is_transition_condition_satisfied,
)


def execute(filters=None):
	filters = frappe._dict(filters or {})
	if filters.from_date and filters.to_date and getdate(filters.from_date) > getdate(filters.to_date):
		frappe.throw(_("From Date cannot be after To Date"))
	return get_columns(), get_data(filters)


def governed_doctypes() -> dict:
	"""doctype -> active PM Workflows on it that require an attachment on some step."""
	rows = frappe.db.sql(
		"""
		SELECT DISTINCT w.document_type, w.name
		FROM `tabPM Workflow` w
		INNER JOIN `tabPM Workflow Transition` t ON t.parent = w.name AND t.parenttype = 'PM Workflow'
		WHERE w.is_active = 1 AND t.require_attachment = 1
		""",
		as_dict=True,
	)
	out = {}
	for r in rows:
		out.setdefault(r.document_type, set()).add(r.name)
	return out


@frappe.whitelist()
@frappe.validate_and_sanitize_search_inputs
def document_type_query(doctype, txt, searchfield, start, page_len, filters):
	"""Document Type filter: only the doctypes this report covers."""
	names = sorted(dt for dt in governed_doctypes() if txt.lower() in dt.lower())
	return [[dt] for dt in names[start : start + page_len]]


def get_data(filters):
	governed = governed_doctypes()
	if filters.document_type:
		governed = {k: v for k, v in governed.items() if k == filters.document_type}
	governed = {dt: wfs for dt, wfs in governed.items() if frappe.has_permission(dt, "read")}
	if not governed:
		return []

	steps = frappe.get_all(
		"PM Workflow Action",
		filters={"reference_doctype": ["in", list(governed)], "status": "Completed"},
		fields=["reference_doctype", "reference_name", "workflow_state", "completed_by", "creation"],
		order_by="creation asc",
	)
	by_doc = {}
	for step in steps:
		by_doc.setdefault((step.reference_doctype, step.reference_name), []).append(step)
	set_moved_on(by_doc)

	data = []
	for (doctype, name), doc_steps in by_doc.items():
		row = check_document(doctype, name, doc_steps, governed[doctype], filters)
		if row:
			data.append(row)
	set_attached_by(data)
	data.sort(key=lambda r: r.moved_on, reverse=True)
	return data


def set_attached_by(data):
	"""Who attached the voucher's last file, from the Attachment comment frappe adds on every
	upload. Blank when nothing was ever attached."""
	if not data:
		return
	last = {}
	for c in frappe.get_all(
		"Comment",
		filters={
			"comment_type": "Attachment",
			"reference_doctype": ["in", list({r.document_type for r in data})],
			"reference_name": ["in", [r.document for r in data]],
		},
		fields=["reference_doctype", "reference_name", "owner"],
		order_by="creation asc",
	):
		last[(c.reference_doctype, c.reference_name)] = c.owner

	for r in data:
		attached = last.get((r.document_type, r.document))
		r.attached_by = get_fullname(attached) if attached else ""


def set_moved_on(by_doc):
	"""step.moved_on = when the step was taken. The engine marks an action Completed without
	touching its timestamps, so its own creation / modified is when the voucher ARRIVED at that
	state; the move itself is the Workflow comment ("Moved to ...") the engine adds, by the same
	user, at or after that."""
	comments = {}
	doctypes = {dt for dt, _name in by_doc}
	names = [name for _dt, name in by_doc]
	for i in range(0, len(names), 1000):
		for c in frappe.get_all(
			"Comment",
			filters={
				"comment_type": "Workflow",
				"reference_doctype": ["in", list(doctypes)],
				"reference_name": ["in", names[i : i + 1000]],
			},
			fields=["reference_doctype", "reference_name", "owner", "creation"],
			order_by="creation asc",
		):
			comments.setdefault((c.reference_doctype, c.reference_name), []).append(c)

	for key, doc_steps in by_doc.items():
		unused = list(comments.get(key, []))
		for step in doc_steps:
			match = next(
				(c for c in unused if c.owner == step.completed_by and c.creation >= step.creation), None
			)
			if match:
				unused.remove(match)
			step.moved_on = match.creation if match else step.creation


def check_document(doctype, name, doc_steps, workflows, filters):
	"""The voucher's last step that required an attachment, when it has none; else None."""
	if frappe.db.exists("File", {"attached_to_doctype": doctype, "attached_to_name": name}):
		return None
	if not frappe.db.exists(doctype, name):
		return None
	doc = frappe.get_doc(doctype, name)
	if doc.docstatus == 2 or not frappe.has_permission(doctype, "read", doc=doc):
		return None
	if filters.company and doc.get("company") and doc.company != filters.company:
		return None

	workflow = get_workflow_name(doctype, name, doc=doc)
	if workflow not in workflows:
		return None
	state_field = frappe.db.get_value("PM Workflow", workflow, "workflow_state_field") or "workflow_state"

	found = None
	history = rule_history(workflow)
	for i, step in enumerate(doc_steps):
		next_state = doc_steps[i + 1].workflow_state if i + 1 < len(doc_steps) else doc.get(state_field)
		if step_required_attachment(workflow, step, next_state, doc, history):
			found = (step, next_state)
	if not found:
		return None

	step, next_state = found
	moved_on = getdate(step.moved_on)
	if filters.from_date and moved_on < getdate(filters.from_date):
		return None
	if filters.to_date and moved_on > getdate(filters.to_date):
		return None
	if filters.moved_by and step.completed_by != filters.moved_by:
		return None

	return frappe._dict(
		document_type=doctype,
		document=name,
		company=doc.get("company"),
		current_state=doc.get(state_field),
		moved_from=step.workflow_state,
		moved_to=next_state,
		moved_by=step.completed_by,
		moved_on=moved_on,
	)


def step_required_attachment(workflow, step, next_state, doc, history) -> bool:
	user = step.completed_by
	if not user or not next_state:
		return False
	when = step.moved_on
	transitions = get_allowed_transitions_for_user(workflow, step.workflow_state, user, doc)
	# a transition added after the step did not exist when it was taken
	transitions = [t for t in transitions if not t.creation or t.creation <= when]
	if user != "Administrator":
		transitions = [t for t in transitions if is_transition_condition_satisfied(t, doc)]
	# the engine runs the first transition of the chosen action, by idx
	first_of_action = {}
	for t in transitions:
		first_of_action.setdefault(t.action, t)
	taken = [t for t in first_of_action.values() if t.next_state == next_state]
	return bool(taken) and all(required_at(t, when, history) for t in taken)


def rule_history(workflow) -> dict:
	"""transition row -> [(when, old, new)] of its Require Attachment, from the workflow's Version
	log -- the box is often ticked long after the workflow went live, and a step taken before that
	was never asked for a file."""
	if not hasattr(frappe.local, "_wam_history"):
		frappe.local._wam_history = {}
	cache = frappe.local._wam_history
	if workflow in cache:
		return cache[workflow]

	out = {}
	for version in frappe.get_all(
		"Version",
		filters={"ref_doctype": "PM Workflow", "docname": workflow, "data": ["like", "%require_attachment%"]},
		fields=["creation", "data"],
		order_by="creation asc",
	):
		data = frappe.parse_json(version.data) or {}
		for table, _idx, row_name, changes in data.get("row_changed") or []:
			if table != "transitions":
				continue
			for field, old, new in changes:
				if field == "require_attachment":
					out.setdefault(row_name, []).append((version.creation, old, new))
	cache[workflow] = out
	return out


def required_at(transition, when, history) -> bool:
	"""Whether the transition's Require Attachment was ticked at `when`."""
	changes = history.get(transition.name)
	if not changes:
		return bool(transition.require_attachment)
	before = [new for at, _old, new in changes if at <= when]
	return bool(before[-1] if before else changes[0][1])


def get_columns():
	return [
		{"label": _("Document Type"), "fieldname": "document_type", "fieldtype": "Link", "options": "DocType", "width": 140},
		{"label": _("Document"), "fieldname": "document", "fieldtype": "Dynamic Link", "options": "document_type", "width": 170},
		{"label": _("Company"), "fieldname": "company", "fieldtype": "Link", "options": "Company", "width": 170},
		{"label": _("Current State"), "fieldname": "current_state", "fieldtype": "Data", "width": 120},
		{"label": _("Moved From"), "fieldname": "moved_from", "fieldtype": "Data", "width": 110},
		{"label": _("Moved To"), "fieldname": "moved_to", "fieldtype": "Data", "width": 110},
		{"label": _("Moved By"), "fieldname": "moved_by", "fieldtype": "Link", "options": "User", "width": 180},
		{"label": _("Moved On"), "fieldname": "moved_on", "fieldtype": "Date", "width": 100},
		{"label": _("Attached By"), "fieldname": "attached_by", "fieldtype": "Data", "width": 150},
	]
