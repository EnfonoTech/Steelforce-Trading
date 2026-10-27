# sf_trading/master_approval.py
"""Approval of a Customer, Supplier or Item before it can be bought from, sold to, sold or bought.

The approval itself runs on permission_manager's PM Workflow engine -- one workflow per master
("Customer Approval", "Supplier Approval", "Item Approval"), whose state lives in the master's own
`custom_approval_status`:

    Draft --Send for Approval--> Pending Approval --Approve--> Approved
                                                  --Reject---> Rejected --Send for Approval--> ...

Anyone who can open the master sends it; a Customer Approver (or Credit Approval Officer), a Supplier
Approver or an Item Master Approver approves or rejects it from the form or the Approvals page, with
PM's inbox, reminders, history and bulk approval. What is sf_trading's own:

* **Requirements.** A master cannot be sent for approval, nor approved, while it lacks what the other
  master rules ask of it (`missing_requirements`): a credit customer's Payment Terms, two contact
  numbers, an email and an attached supporting document; a customer's phone, CR (B2B) and document (VAT
  registered); a supplier's Payment Terms, supporting document, mobile, email, address and Tax ID (the
  Supplier Group exemptions of party_documents hold). An Item needs approval only.
* **The gate.** A Sales Order, Delivery Note or Sales Invoice refuses a customer, and a Purchase Order,
  Purchase Receipt, Purchase Invoice or supplier payment refuses a supplier, that the approval covers and
  has not approved; every item row is held to the same (`validate_master_approval_at_transaction`).
  Quotations, Supplier Quotations and Material Requests stay open: they come before anything is
  committed. Returns, opening entries and internal (inter-company) parties stand aside.
* **Who is covered.** A master created at or after SF Trading Settings > Approval Applies to Masters
  Created From (set the first time this is migrated). An older master is covered only when Strict
  Validation is ticked -- it then needs an approval like a new one -- or when it is itself waiting for
  approval or rejected (the credit customers that were Pending Verification carry over that way).
  "Approved" means approved through this approval: `custom_approved_on` is stamped, so a customer the
  earlier credit-customer approval marked Approved is not taken for one under Strict Validation. An
  approver approves the older masters in bulk from their list (`approve_masters`).
* **Bypass.** A System Manager, or a role / user on SF Trading Settings > Allowed to Bypass Master
  Approval, may bypass a master from its form or list, new or old, with a reason: the master is then
  usable at once, and the requirement gates of the other modules (documents, contacts, Payment Terms)
  stand aside for it too (`is_master_bypassed`). An expired document that carries a Financial
  Implication still blocks.

Each master is switched on in SF Trading Settings > Customer, Supplier and Item Approval; the
workflows answer permission_manager's applicability hook (`workflow_applicability`), so a master the
approval does not cover shows no workflow buttons and gets no inbox entry.
"""

from __future__ import annotations

import frappe
from frappe import _
from frappe.utils import cint, cstr, get_datetime, now_datetime

from sf_trading import switches

SETTINGS = "SF Trading Settings"
MASTERS = ("Customer", "Supplier", "Item")

STATUS_FIELD = "custom_approval_status"
APPROVED_BY = "custom_approved_by"
APPROVED_ON = "custom_approved_on"
BYPASSED = "custom_approval_bypassed"
BYPASSED_BY = "custom_approval_bypassed_by"
BYPASSED_ON = "custom_approval_bypassed_on"
BYPASS_REASON = "custom_approval_bypass_reason"

DRAFT = "Draft"
PENDING = "Pending Approval"
APPROVED = "Approved"
REJECTED = "Rejected"
#: the earlier credit-customer approval's waiting state, carried over as Pending Approval
OLD_PENDING = "Pending Verification"
#: a master someone put up for approval and that does not have it -- held back even when it is older
#: than the approval itself
HELD = (PENDING, REJECTED)

#: SF Trading Settings switch per master, and its value while nobody has set it
SWITCH = {"Customer": "master_approval_customer", "Supplier": "master_approval_supplier", "Item": "master_approval_item"}
SWITCH_DEFAULT = {"Customer": True, "Supplier": False, "Item": False}
STRICT = "master_approval_strict"
FROM = "master_approval_from"

APPROVER_ROLES = {
	"Customer": ("Customer Approver", "Credit Approval Officer", "System Manager"),
	"Supplier": ("Supplier Approver", "System Manager"),
	"Item": ("Item Master Approver", "System Manager"),
}

WORKFLOW = {"Customer": "Customer Approval", "Supplier": "Supplier Approval", "Item": "Item Approval"}
SEND = "Send for Approval"
APPROVE = "Approve"
REJECT = "Reject"
#: anyone who can open the master sends it for approval, and may edit it in any state
EVERYONE = "All"
#: a master the earlier rollout marked Approved without an approval may still be sent (Strict Validation)
LEGACY_APPROVED_CONDITION = 'not doc.get("custom_approved_on")'

CUSTOMER_VOUCHERS = ("Sales Order", "Delivery Note", "Sales Invoice")
SUPPLIER_VOUCHERS = ("Purchase Order", "Purchase Receipt", "Purchase Invoice", "Payment Entry")
ITEM_VOUCHERS = ("Sales Order", "Delivery Note", "Sales Invoice", "Purchase Order", "Purchase Receipt", "Purchase Invoice")

STATE_FIELDS = ("name", "creation", STATUS_FIELD, APPROVED_ON, BYPASSED)
INTERNAL_FLAG = {"Customer": "is_internal_customer", "Supplier": "is_internal_supplier"}


# ─── policy ──────────────────────────────────────────────────────────────────────────────────────


def is_enabled(doctype: str) -> bool:
	return doctype in SWITCH and switches.is_on(SWITCH[doctype], SWITCH_DEFAULT[doctype])


def is_strict() -> bool:
	return switches.is_on(STRICT, False)


def has_fields(doctype: str) -> bool:
	"""False on a site that has pulled this code but not migrated it yet (a shared bench)."""
	meta = frappe.get_meta(doctype)
	return bool(meta.has_field(STATUS_FIELD) and meta.has_field(APPROVED_ON) and meta.has_field(BYPASSED))


def rules_from():
	"""The moment the approval starts from: masters created since then are new."""
	value = (frappe.db.get_singles_dict(SETTINGS) or {}).get(FROM)
	return get_datetime(value) if value else None


def _is_new(state) -> bool:
	is_new = getattr(state, "is_new", None)
	return bool(callable(is_new) and is_new()) or not state.get("creation")


def is_new_master(state) -> bool:
	if _is_new(state):
		return True
	start = rules_from()
	return bool(start) and get_datetime(state.get("creation")) >= start


def is_bypassed(state) -> bool:
	return bool(cint(state.get(BYPASSED)))


def is_covered(doctype: str, state) -> bool:
	"""Does the approval apply to this master at all?"""
	if not is_enabled(doctype) or not has_fields(doctype) or is_bypassed(state):
		return False
	if is_new_master(state) or is_strict():
		return True
	return state.get(STATUS_FIELD) in HELD


def is_approved(state) -> bool:
	return state.get(STATUS_FIELD) == APPROVED and bool(state.get(APPROVED_ON))


def refusal_status(doctype: str, state) -> str | None:
	"""Why the master may not be used (its status, in words), or None when it may."""
	if not is_covered(doctype, state) or is_approved(state):
		return None
	status = state.get(STATUS_FIELD)
	if status == APPROVED:
		return _("approved before the approval workflow; it needs an approval now")
	return _(status) if status else _("not sent for approval")


def is_master_bypassed(doctype: str, name: str) -> bool:
	"""For the other master gates: a bypassed master is let through their requirement checks."""
	if doctype not in MASTERS or not name or not has_fields(doctype):
		return False
	return bool(cint(frappe.db.get_value(doctype, name, BYPASSED)))


def approver_roles(doctype: str) -> tuple:
	return APPROVER_ROLES.get(doctype, ())


def workflow_approver_roles(doctype: str) -> tuple:
	"""The approvers the PM Workflow names. Not System Manager: it approves from the form or the list
	(approve_masters) -- as a workflow approver every one of them would get an inbox entry for every master."""
	return tuple(r for r in approver_roles(doctype) if r != "System Manager")


def can_approve(doctype: str, user: str | None = None) -> bool:
	user = user or frappe.session.user
	return user == "Administrator" or bool(set(frappe.get_roles(user)) & set(approver_roles(doctype)))


def may_bypass(user: str | None = None) -> bool:
	"""System Manager, or a role / user on the bypass roster (SF Trading Settings)."""
	user = user or frappe.session.user
	if user == "Administrator" or "System Manager" in frappe.get_roles(user):
		return True
	from sf_trading.credit_customer_approval import may_bypass as on_roster

	return on_roster(user)


def _may_set_status_directly(user: str | None = None) -> bool:
	user = user or frappe.session.user
	return user == "Administrator" or "System Manager" in frappe.get_roles(user)


# ─── requirements ────────────────────────────────────────────────────────────────────────────────


def _dedupe(items) -> list:
	out = []
	for item in items:
		if item and item not in out:
			out.append(item)
	return out


def _expired(doc) -> list:
	from sf_trading.party_documents import expired_financial_rows

	return [
		_("{0} {1} expired on {2}").format(row.document_type, row.document_number or "",
			frappe.format_value(row.expiry_date, "Date")).replace("  ", " ")
		for row in expired_financial_rows(doc.doctype, doc.name)
	]


def _customer_requirements(doc) -> list:
	from sf_trading import party_documents as pd
	from sf_trading.party_completeness import missing_company_fields
	from sf_trading.sales_order_governance import missing_contact_phone, missing_credit_customer_requirements

	missing = list(missing_company_fields(doc))
	if missing_contact_phone("Customer", doc.name):
		missing.append(_("a Contact with a phone number"))
	if pd.is_credit_customer(doc):
		details = pd.missing_details(doc, after_insert=True)
		missing += details
		credit = missing_credit_customer_requirements(doc.name)
		if any("Supporting Document" in m for m in details):
			# the supporting document asked for above is the attachment this one asks for
			credit = [m for m in credit if "attachment" not in m]
		missing += credit
	if not any("Supporting Document" in m for m in missing):
		missing += pd.missing_document(doc)
	return _dedupe(missing + _expired(doc))


def _supplier_requirements(doc) -> list:
	from sf_trading import party_documents as pd

	missing = list(pd.missing_details(doc, after_insert=True))
	if (doc.get("supplier_type") == "Company" and not pd.completeness_exempt(doc)
			and not cstr(doc.get("tax_id")).strip()):
		missing.append(_("Tax ID"))
	return _dedupe(missing + _expired(doc))


def missing_requirements(doc) -> list:
	"""What this master still lacks before it may be sent for approval or approved."""
	if doc.doctype == "Customer":
		return _customer_requirements(doc)
	if doc.doctype == "Supplier":
		return _supplier_requirements(doc)
	return []


def _label(doc) -> str:
	return doc.get("customer_name") or doc.get("supplier_name") or doc.get("item_name") or doc.name


def _throw_missing(doc, missing):
	frappe.throw(
		_("{0} {1} cannot go for approval yet. Complete it first: {2}.").format(
			_(doc.doctype), frappe.bold(_label(doc)), ", ".join(missing)),
		title=_("Approval Requirements Missing"),
	)


# ─── the master's own save ───────────────────────────────────────────────────────────────────────


def _system_run() -> bool:
	flags = frappe.flags
	return bool(flags.in_patch or flags.in_install or flags.in_migrate)


def set_initial_status(doc, _method=None):
	"""before_insert on Customer / Supplier / Item: a new master starts as a Draft.

	A System Manager may bring a master in already approved (an import of reviewed masters, say); it is
	stamped as approved by them. Anything else that arrives with a status starts as a Draft all the same.
	"""
	if _system_run() or not is_enabled(doc.doctype) or not has_fields(doc.doctype):
		return
	if not rules_from():
		# the first master on a site that has not run setup() yet: the approval starts now
		frappe.db.set_single_value(SETTINGS, FROM, now_datetime(), update_modified=False)
	if doc.get(STATUS_FIELD) == APPROVED and _may_set_status_directly():
		doc.set(APPROVED_BY, frappe.session.user)
		doc.set(APPROVED_ON, now_datetime())
		return
	doc.set(STATUS_FIELD, DRAFT)
	doc.set(APPROVED_BY, None)
	doc.set(APPROVED_ON, None)


def validate_master(doc, _method=None):
	"""validate on Customer / Supplier / Item: the approval status moves by the workflow only, into
	Pending Approval and Approved only with every requirement met, and Approved is stamped."""
	if doc.is_new() or not has_fields(doc.doctype):
		return
	before = doc.get_doc_before_save()
	was = before.get(STATUS_FIELD) if before else None
	now = doc.get(STATUS_FIELD)
	if was == now:
		return

	by_workflow = bool(doc.flags.get("pm_workflow_action"))
	if not by_workflow and not _may_set_status_directly():
		frappe.throw(
			_("The Approval Status moves with the approval buttons (Send for Approval, Approve, Reject), not by "
			  "editing it."),
			title=_("Not Permitted"),
		)

	if now in (PENDING, APPROVED) and not is_bypassed(doc):
		missing = missing_requirements(doc)
		if missing:
			_throw_missing(doc, missing)

	if now == APPROVED:
		doc.set(APPROVED_BY, frappe.session.user)
		doc.set(APPROVED_ON, now_datetime())
	else:
		doc.set(APPROVED_BY, None)
		doc.set(APPROVED_ON, None)


# ─── the gate on documents raised against a master ───────────────────────────────────────────────


def _party(doc):
	if doc.doctype in CUSTOMER_VOUCHERS:
		return "Customer", doc.get("customer")
	if doc.doctype == "Payment Entry":
		# paying a supplier is held back; money received never is
		if doc.get("party_type") == "Supplier" and doc.get("payment_type") == "Pay":
			return "Supplier", doc.get("party")
		return None, None
	if doc.doctype in SUPPLIER_VOUCHERS:
		return "Supplier", doc.get("supplier")
	return None, None


def party_refusal(party_type: str, party: str) -> str | None:
	"""Why this customer / supplier may not be used on a voucher, or None when it may."""
	if not party or not is_enabled(party_type) or not has_fields(party_type):
		return None
	name_field = party_type.lower() + "_name"
	state = frappe.db.get_value(party_type, party, list(STATE_FIELDS) + [INTERNAL_FLAG[party_type], name_field],
		as_dict=True)
	if not state or cint(state.get(INTERNAL_FLAG[party_type])):
		return None
	reason = refusal_status(party_type, state)
	if not reason:
		return None
	return _("{0} {1} is not approved ({2}). Send it for approval from the {0}; an approver approves it from "
		"the {0} or the Approvals page.").format(_(party_type), frappe.bold(state.get(name_field) or party), reason)


def unapproved_items(item_codes) -> list:
	"""The item codes among these that the Item approval covers and has not approved."""
	codes = sorted({cstr(c) for c in item_codes if c})
	if not codes or not is_enabled("Item") or not has_fields("Item"):
		return []
	rows = frappe.get_all("Item", filters={"name": ["in", codes]}, fields=list(STATE_FIELDS))
	return [row.name for row in rows if refusal_status("Item", row)]


def validate_master_approval_at_transaction(doc, _method=None):
	"""validate on the selling and buying documents (and supplier payments): the customer or supplier
	and every item must be approved, where the approval covers them."""
	if doc.get("is_return") or doc.get("is_opening") == "Yes" or _system_run():
		return
	problems = []
	party_type, party = _party(doc)
	if party:
		text = party_refusal(party_type, party)
		if text:
			problems.append(text)
	if doc.doctype in ITEM_VOUCHERS:
		codes = unapproved_items(row.get("item_code") for row in doc.get("items") or [])
		if codes:
			shown = ", ".join(frappe.bold(c) for c in codes[:20])
			if len(codes) > 20:
				shown += " " + _("and {0} more").format(len(codes) - 20)
			problems.append(_("These items are not approved yet: {0}. An Item Master Approver approves them from "
				"the Item or the Approvals page.").format(shown))
	if problems:
		frappe.throw("<br><br>".join(problems), title=_("Master Not Approved"))


# ─── bypass, and approval from the list ──────────────────────────────────────────────────────────


def _check_doctype(doctype: str):
	if doctype not in MASTERS:
		frappe.throw(_("Only a Customer, Supplier or Item is approved here."))


def _close_open_actions(doctype: str, name: str):
	"""A bypassed or directly approved master leaves nobody's Approvals inbox waiting on it."""
	if not frappe.db.table_exists("PM Workflow Action"):
		return
	for action in frappe.get_all("PM Workflow Action", filters={"reference_doctype": doctype, "reference_name": name,
			"status": ["in", ["Open", "Forwarded"]]}, pluck="name"):
		frappe.db.set_value("PM Workflow Action", action, {"status": "Completed", "completed_by": frappe.session.user},
			update_modified=False)


def _comment(doctype: str, name: str, text: str):
	frappe.get_doc({"doctype": "Comment", "comment_type": "Info", "reference_doctype": doctype,
		"reference_name": name, "content": text}).insert(ignore_permissions=True)


def _names(names) -> list:
	if isinstance(names, str):
		names = frappe.parse_json(names) if names.strip().startswith("[") else [names]
	return [cstr(n) for n in names or [] if cstr(n).strip()]


@frappe.whitelist()
def bypass_approval(doctype: str, names, reason: str) -> dict:
	"""Make masters usable without the approval and its requirements. Logged on each master."""
	_check_doctype(doctype)
	if not may_bypass():
		frappe.throw(_("Not permitted to bypass master approval."), frappe.PermissionError)
	reason = cstr(reason).strip()
	if not reason:
		frappe.throw(_("Give a reason for the bypass."))
	done = []
	for name in _names(names):
		frappe.has_permission(doctype, "read", doc=name, throw=True)
		frappe.db.set_value(doctype, name, {BYPASSED: 1, BYPASSED_BY: frappe.session.user,
			BYPASSED_ON: now_datetime(), BYPASS_REASON: reason})
		_close_open_actions(doctype, name)
		_comment(doctype, name, _("Approval bypassed by {0}: {1}").format(
			frappe.session.user, frappe.utils.escape_html(reason)))
		done.append(name)
	return {"bypassed": done}


@frappe.whitelist()
def remove_bypass(doctype: str, name: str) -> None:
	_check_doctype(doctype)
	if not may_bypass():
		frappe.throw(_("Not permitted to bypass master approval."), frappe.PermissionError)
	frappe.has_permission(doctype, "read", doc=name, throw=True)
	frappe.db.set_value(doctype, name, {BYPASSED: 0, BYPASSED_BY: None, BYPASSED_ON: None, BYPASS_REASON: None})
	_comment(doctype, name, _("Approval bypass removed by {0}").format(frappe.session.user))


@frappe.whitelist()
def approve_masters(doctype: str, names) -> dict:
	"""An approver approves masters straight from their list or form -- the way through for the older
	masters Strict Validation brings in, which nobody sent. Each one is held to its requirements; one
	that falls short is skipped and says why."""
	_check_doctype(doctype)
	if not can_approve(doctype):
		frappe.throw(_("Only a {0} may approve a {1}.").format(
			" / ".join(_(r) for r in approver_roles(doctype)), _(doctype)), frappe.PermissionError)
	approved, skipped = [], []
	for name in _names(names):
		frappe.has_permission(doctype, "read", doc=name, throw=True)
		doc = frappe.get_doc(doctype, name)
		if is_approved(doc):
			skipped.append({"name": name, "reason": _("already approved")})
			continue
		missing = [] if is_bypassed(doc) else missing_requirements(doc)
		if missing:
			skipped.append({"name": name, "reason": ", ".join(missing)})
			continue
		frappe.db.set_value(doctype, name, {STATUS_FIELD: APPROVED, APPROVED_BY: frappe.session.user,
			APPROVED_ON: now_datetime()})
		_close_open_actions(doctype, name)
		_comment(doctype, name, _("Approved by {0}").format(frappe.session.user))
		approved.append(name)
	return {"approved": approved, "skipped": skipped}


@frappe.whitelist()
def get_approval_state(doctype: str, name: str) -> dict:
	"""What the master's form shows: whether the approval covers it, what it still lacks, which buttons."""
	_check_doctype(doctype)
	frappe.has_permission(doctype, "read", doc=name, throw=True)
	if not is_enabled(doctype) or not has_fields(doctype):
		return {"enabled": False}
	doc = frappe.get_doc(doctype, name)
	covered = is_covered(doctype, doc)
	approved = is_approved(doc)
	return {
		"enabled": True,
		"covered": covered,
		"strict": is_strict(),
		"status": doc.get(STATUS_FIELD) or "",
		"approved": approved,
		"bypassed": is_bypassed(doc),
		"can_bypass": may_bypass(),
		"can_approve": can_approve(doctype),
		# holds a role the PM Workflow's own Approve button is offered to
		"workflow_approver": bool(set(frappe.get_roles()) & set(workflow_approver_roles(doctype))),
		"missing": missing_requirements(doc) if covered and not approved else [],
	}


# ─── permission_manager ──────────────────────────────────────────────────────────────────────────


def workflow_applicability(doctype: str, doc=None):
	"""permission_manager's `pm_workflow_applicability` hook: a master the approval does not cover (an older
	one without Strict Validation, a bypassed one, or any while its switch is off) has no workflow -- no
	buttons, no inbox entry, and PM does not stamp a state on it."""
	if doctype not in MASTERS:
		return None
	if doc is None:
		return {"conditional": True}
	return {"conditional": True, "applies": is_covered(doctype, doc)}


def workflow_definition(doctype: str) -> dict:
	from sf_trading.approval_routing import _state, _transition

	transitions = [
		_transition(DRAFT, SEND, PENDING, EVERYONE),
		_transition(REJECTED, SEND, PENDING, EVERYONE),
		_transition(APPROVED, SEND, PENDING, EVERYONE, condition=LEGACY_APPROVED_CONDITION),
	]
	for role in workflow_approver_roles(doctype):
		transitions.append(_transition(PENDING, APPROVE, APPROVED, role))
		transitions.append(_transition(PENDING, REJECT, REJECTED, role, require_comment=1, is_return_for_correction=1))
	return {
		"doctype": "PM Workflow",
		"workflow_name": WORKFLOW[doctype],
		"document_type": doctype,
		"is_active": 1,
		"workflow_state_field": STATUS_FIELD,
		"states": [
			# nobody is waiting on a draft, an approved or a rejected master: no inbox entry for them
			_state(DRAFT, 0, EVERYONE, optional=1),
			_state(PENDING, 0, EVERYONE),
			_state(APPROVED, 0, EVERYONE, optional=1),
			_state(REJECTED, 0, EVERYONE, optional=1),
		],
		"transitions": transitions,
	}


def ensure_workflows() -> list:
	"""Create the three PM Workflows if this site does not have them. Never overwrites one: an approval
	someone has since adjusted on the site stays as they left it."""
	if not frappe.db.table_exists("PM Workflow"):
		return []
	from sf_trading.approval_routing import _ensure_masters

	created = []
	for doctype in MASTERS:
		definition = workflow_definition(doctype)
		if frappe.db.exists("PM Workflow", definition["workflow_name"]):
			continue
		_ensure_masters(definition)
		workflow = frappe.get_doc(definition)
		# saving a PM Workflow stamps its first state on every document with none, so every existing master
		# would read Draft. The older ones are not new masters: they keep no state until one is given.
		workflow.update_default_workflow_status = lambda: None
		workflow.insert(ignore_permissions=True)
		created.append(definition["workflow_name"])
	return created


def grant_approver_access():
	"""The approver roles may read and write their master: PM saves the master as the approving user."""
	from frappe.permissions import add_permission, update_permission_property

	for doctype in MASTERS:
		for role in approver_roles(doctype):
			if role == "System Manager":
				continue
			if not frappe.db.exists("Role", role):
				frappe.get_doc({"doctype": "Role", "role_name": role, "desk_access": 1}).insert(ignore_permissions=True)
			if frappe.db.exists("Custom DocPerm", {"parent": doctype, "role": role, "permlevel": 0, "read": 1, "write": 1}):
				continue
			add_permission(doctype, role, 0)
			for ptype in ("read", "write"):
				update_permission_property(doctype, role, 0, ptype, 1, validate=False)


def _seed_switches():
	"""A Check nobody has set reads its default in the code but blank on the form: store the default."""
	stored = set(frappe.db.sql_list("SELECT `field` FROM `tabSingles` WHERE `doctype` = %s", SETTINGS))
	for doctype, field in SWITCH.items():
		if field not in stored:
			frappe.db.set_single_value(SETTINGS, field, cint(SWITCH_DEFAULT[doctype]), update_modified=False)
	if STRICT not in stored:
		frappe.db.set_single_value(SETTINGS, STRICT, 0, update_modified=False)


def _carry_over_pending():
	"""The earlier credit-customer approval's Pending Verification is Pending Approval here. Its Approved
	customers carry no approval stamp: they are older masters, and Strict Validation asks them for one."""
	frappe.db.set_value("Customer", {STATUS_FIELD: OLD_PENDING}, STATUS_FIELD, PENDING, update_modified=False)


def setup():
	"""after_migrate: approver access, the three workflows, the switches, the carry-over of the earlier
	credit-customer approval, and -- the first time -- the moment the approval starts from."""
	if not frappe.db.exists("DocType", SETTINGS) or not all(has_fields(dt) for dt in MASTERS):
		return
	grant_approver_access()
	ensure_workflows()
	_seed_switches()
	_carry_over_pending()
	if not rules_from():
		frappe.db.set_single_value(SETTINGS, FROM, now_datetime(), update_modified=False)
