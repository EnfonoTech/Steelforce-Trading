# sf_trading/sales_return_reason.py
"""Why a Sales Return is being made, standardised -- a picker backed by a master, appending into
an existing field rather than replacing it.

sales_return.py already owns the return WINDOW (how many days late a return may be raised) and the
return APPROVAL (PM Workflow above a threshold). Both are about whether a return may go through at
all. This is a narrower, unrelated concern: once a return is allowed, what it says about itself.

`custom_return_reason` ("Return/Debit Reason") is a pre-existing free-text field, in place since
2024, used elsewhere (reporting, print, debit notes) -- this module never repurposes or replaces
it. `custom_return_reason_template` (a Link to Sales Return Reason, fixtures/custom_field.json) is
a picker addition only: choosing a standardised reason there APPENDS its label into whatever is
already typed in Return/Debit Reason, without disturbing it; the append/undo mechanics are entirely
client-side (see public/js/sales_return_reason.js) -- this module holds no opinion on how the text
in custom_return_reason got there.

"Other" is the one reason that says nothing on its own, so it is the one case that must not be left
at just the picker's own label with nothing added. The form nudges that live (focuses
custom_return_reason on picking "Other"); this is the same rule checked again server-side, because
a client-only rule is a rule a stale draft or a direct API call can skip -- sales_return.py's own
docstring makes the same point about the window check, for the same reason.

Since 2026-10-10 (client tracker #4) the picker is also REQUIRED on a return: every return names its
reason from the list, so returns can be counted by reason. The form marks it mandatory
(mandatory_depends_on on the Custom Field, which Frappe only evaluates in the browser);
require_reason_template is the server half, for the saves no browser makes. It is enforced where
the person raising the return can still answer -- their own save, their own submit, their own
"Send for Approval" -- and never on an approver's action: the approver cannot edit the return from
the inbox, so refusing there would strand a return that was already in the chain before the rule
existed (planned_payment.require_plan_before_approval explains the same trap).
"""

import frappe
from frappe import _
from frappe.utils import cint

OTHER_REASON = "Other"
TEMPLATE_FIELD = "custom_return_reason_template"


def require_reason_template(doc, method=None):
	"""validate on Sales Invoice: a return must pick its Return Reason Template."""
	if not cint(doc.get("is_return")) or (doc.get(TEMPLATE_FIELD) or "").strip():
		return
	if _made_by_the_system(doc) or _approvers_turn(doc):
		return

	message = _("Pick a") + " " + frappe.bold(_("Return Reason Template")) + " " + _(
		"for this return. Every return names its reason from the standard list."
	)
	frappe.throw(message, title=_("Return Reason Required"))


def _flags(doc):
	return getattr(doc, "flags", None) or frappe._dict()


def _made_by_the_system(doc) -> bool:
	"""A return nobody is filling in by hand, so nobody is there to pick a reason."""
	if _flags(doc).get("ignore_mandatory"):
		# importers and erpnext's Bulk Transaction insert with ignore_mandatory on purpose
		return True
	flags = frappe.flags
	if flags.in_import or flags.in_migrate or flags.in_patch or flags.in_install:
		return True
	if cint(doc.get("is_consolidated")):
		# POS Invoice Merge Log's consolidated credit note
		return True
	return _issued_by_a_return_delivery_note(doc)


def _issued_by_a_return_delivery_note(doc) -> bool:
	"""erpnext makes and submits this credit note itself, inside the return Delivery Note's own
	on_submit ("Issue Credit Note"); refusing it would refuse the Delivery Note."""
	notes = {row.get("delivery_note") for row in (doc.get("items") or []) if row.get("delivery_note")}
	if not notes:
		return False
	return bool(
		frappe.db.exists(
			"Delivery Note", {"name": ["in", sorted(notes)], "is_return": 1, "issue_credit_note": 1}
		)
	)


def _approvers_turn(doc) -> bool:
	"""True when this save is not the requester's: an approver's PM action other than the one that
	sends the return in, or any other save of a return that is already waiting for approval."""
	from sf_trading.planned_payment import LIVE_APPROVAL_STATES

	state = (doc.get("workflow_state") or "").strip().lower()
	in_chain = cint(doc.get("docstatus")) == 0 and state in LIVE_APPROVAL_STATES
	getter = getattr(doc, "get_doc_before_save", None)
	before = getter() if callable(getter) else None
	was = (before.get("workflow_state") or "").strip().lower() if before else None
	entering = in_chain and was != state

	if _flags(doc).get("pm_workflow_action"):
		# Send for Approval is the requester's own action; Approve / Reject are the approver's
		return not entering
	# already waiting for approval and staying there: some other save, not the requester raising it
	return in_chain and not entering


def validate_return_reason(doc, method=None):
	"""validate on Sales Invoice: picking "Other" must leave more than just that label behind."""
	if not cint(doc.get("is_return")):
		return
	if doc.get("custom_return_reason_template") != OTHER_REASON:
		return

	text = (doc.get("custom_return_reason") or "").strip()
	if not text or text == OTHER_REASON:
		field_label = frappe.bold(_("Return/Debit Reason"))
		reason_label = frappe.bold(_(OTHER_REASON))
		message = _("Please describe the return in") + " " + field_label + " " + _(
			"when Return Reason Template is"
		) + " " + reason_label + "."
		frappe.throw(message, title=_("Return Reason Needs a Description"))
