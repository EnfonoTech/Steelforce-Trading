# apps/sf_trading/sf_trading/api/payment_advice_workflow.py
"""PM Workflow for Payment Advice — modelled on the live "Payment Request Approval".

permission_manager's PM Workflow is its own approval engine (there is no native Frappe
Workflow record behind it), driven by a `workflow_state` field on the target doctype. This
module creates the equivalent workflow for Payment Advice and tells the rest of sf_trading
whether it is active, because two behaviours have to step aside when it is:

  * `PaymentAdvice.validate_approver()` — the single-approver rule ("only the linked user may
    submit") would fight the workflow, where an Accountant approves documents raised by
    someone else. When a workflow is active the workflow's own permissions decide.
  * the payment automation — with an approval workflow in force, a scheduled run must not
    submit advices behind the approvers' backs. It stops at drafts and records why.

Creation is idempotent and non-destructive: an existing workflow is left exactly as it is
unless `force=1` is passed, so a workflow tuned on site is never overwritten by a deploy.

Shape copied from Payment Request Approval as it runs on prod:
    Draft ──Send for Approval──▶ Pending Approval ──Approve──▶ Approved (docstatus 1)
                                        └──────Reject──────▶ Rejected ──▶ Pending Approval
with require_attachment on Approve, require_comment + return-for-correction on Reject,
a 3-day reminder and 7-day escalation. Roles match it too: Purchase User raises, Accountant
approves.
"""

import frappe
from frappe import _
from frappe.utils import cint

WORKFLOW_NAME = "Payment Advice Approval"
DOCTYPE = "Payment Advice"

STATE_DRAFT = "Draft"
STATE_PENDING = "Pending Approval"
STATE_APPROVED = "Approved"
STATE_REJECTED = "Rejected"

# Roles are the live Payment Request Approval's, unchanged: the raiser sends it up, the
# Accountant approves. Keeping them identical means one approval convention across both
# documents rather than two rules staff have to remember.
ROLE_PREPARER = "Purchase User"
ROLE_APPROVER = "Accountant"


def pm_workflow_available():
    """Is permission_manager installed on this site?"""
    return bool(frappe.db.exists("DocType", "PM Workflow"))


def has_active_workflow(company=None):
    """True when a PM Workflow is live for Payment Advice.

    Callers use this to stand down their own approval logic. Cheap and cached per request,
    because it runs inside document validation.
    """
    if not pm_workflow_available():
        return False

    key = "sf_payment_advice_workflow_active_%s" % (company or "*")
    cached = frappe.local.cache.get(key) if hasattr(frappe.local, "cache") else None
    if cached is not None:
        return cached

    filters = {"document_type": DOCTYPE, "is_active": 1}
    if company:
        filters["company"] = ["in", [company, ""]]

    active = bool(frappe.db.exists("PM Workflow", filters))

    if hasattr(frappe.local, "cache"):
        frappe.local.cache[key] = active
    return active


def _default_email_alert():
    """Follow whatever the site's other PM Workflows do about approval email.

    Steel Force has these switched off while no outgoing Email Account exists — every
    transition would otherwise log "Failed to send workflow action email". A new workflow that
    ignored that would quietly start filling the Error Log again. With no workflows to learn
    from (a fresh site) email stays on, which is permission_manager's own default.
    """
    existing = frappe.get_all("PM Workflow", pluck="send_email_alert")
    if not existing:
        return 1
    return 1 if any(cint(v) for v in existing) else 0


def _states(company):
    """Editable-by roles per state, mirroring the Payment Request workflow."""
    return [
        {"state": STATE_DRAFT, "doc_status": "0", "allow_edit": ROLE_PREPARER},
        {"state": STATE_PENDING, "doc_status": "0", "allow_edit": ROLE_APPROVER},
        {"state": STATE_APPROVED, "doc_status": "1", "allow_edit": ROLE_APPROVER},
        {"state": STATE_REJECTED, "doc_status": "0", "allow_edit": ROLE_PREPARER},
    ]


def _transitions(company):
    return [
        {
            "state": STATE_DRAFT,
            "action": "Send for Approval",
            "next_state": STATE_PENDING,
            "allowed": ROLE_PREPARER,
            "allow_self_approval": 1,
        },
        {
            "state": STATE_PENDING,
            "action": "Approve",
            "next_state": STATE_APPROVED,
            "allowed": ROLE_APPROVER,
            # same discipline as Payment Request: an approval carries its paperwork
            "require_attachment": 1,
            # the field defaults to 1, and Payment Request Approval leaves it off here: whoever
            # raised the advice must not be the one approving it, even holding the approver role
            "allow_self_approval": 0,
        },
        {
            "state": STATE_PENDING,
            "action": "Reject",
            "next_state": STATE_REJECTED,
            "allowed": ROLE_APPROVER,
            "require_comment": 1,
            "is_return_for_correction": 1,
            "allow_self_approval": 0,
        },
        {
            "state": STATE_REJECTED,
            "action": "Send for Approval",
            "next_state": STATE_PENDING,
            "allowed": ROLE_PREPARER,
            "allow_self_approval": 1,
        },
    ]


@frappe.whitelist()
def setup_workflow(company: str = None, force: int = 0):
    """Create (or optionally rebuild) the Payment Advice PM Workflow.

    Idempotent: an existing workflow is reported and left untouched unless force=1.
    """
    frappe.only_for(("System Manager", "Accounts Manager"))

    if not pm_workflow_available():
        frappe.throw(
            _("permission_manager is not installed on this site, so PM Workflow cannot be created.")
        )

    company = company or frappe.defaults.get_user_default("Company") or frappe.db.get_single_value(
        "Global Defaults", "default_company"
    )
    if not company:
        frappe.throw(_("Company is required."))

    for role in (ROLE_PREPARER, ROLE_APPROVER):
        if not frappe.db.exists("Role", role):
            frappe.throw(_("Role %s does not exist on this site.") % frappe.bold(role))

    existing = frappe.db.exists("PM Workflow", WORKFLOW_NAME)
    if existing and not cint(force):
        return {
            "created": False,
            "workflow": WORKFLOW_NAME,
            "message": _("%s already exists and was left untouched.") % WORKFLOW_NAME,
        }

    doc = frappe.get_doc("PM Workflow", WORKFLOW_NAME) if existing else frappe.new_doc("PM Workflow")
    doc.update(
        {
            "workflow_name": WORKFLOW_NAME,
            "document_type": DOCTYPE,
            "company": company,
            "is_active": 1,
            "send_email_alert": _default_email_alert(),
            "allow_descendants": 1,
            "workflow_state_field": "workflow_state",
            "reminder_after_days": 3,
            "escalate_after_days": 7,
        }
    )

    doc.set("states", [])
    for state in _states(company):
        state = dict(state)
        state.update({"company": company, "send_email": 1, "edit_permission_type": "Role"})
        doc.append("states", state)

    doc.set("transitions", [])
    for transition in _transitions(company):
        transition = dict(transition)
        transition.update(
            {
                "company": company,
                "approver_type": "Role",
                "priority": "Medium",
                # Payment Request Approval sets this on every transition; keep parity so the
                # approver-matrix engine treats both workflows the same way.
                "matrix_level": 1,
            }
        )
        doc.append("transitions", transition)

    doc.save(ignore_permissions=True)
    frappe.db.commit()

    return {
        "created": not bool(existing),
        "rebuilt": bool(existing and cint(force)),
        "workflow": doc.name,
        "states": [s.state for s in doc.states],
        "transitions": ["%s → %s (%s)" % (t.state, t.next_state, t.action) for t in doc.transitions],
        "message": _("%s is active. Payment Advice submission is now controlled by the workflow.")
        % doc.name,
    }


@frappe.whitelist()
def workflow_status():
    """What the desk should tell a user about advice approval on this site."""
    if not pm_workflow_available():
        return {"available": False, "active": False, "reason": _("permission_manager not installed")}

    workflow = frappe.db.get_value(
        "PM Workflow",
        {"document_type": DOCTYPE},
        ["name", "is_active", "company"],
        as_dict=True,
    )
    if not workflow:
        return {"available": True, "active": False, "reason": _("No workflow created yet")}

    return {
        "available": True,
        "active": bool(cint(workflow.is_active)),
        "workflow": workflow.name,
        "company": workflow.company,
    }
