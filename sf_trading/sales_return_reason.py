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
"""

import frappe
from frappe import _
from frappe.utils import cint

OTHER_REASON = "Other"


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
