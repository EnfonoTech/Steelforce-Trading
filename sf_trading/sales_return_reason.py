# sf_trading/sales_return_reason.py
"""Why a Sales Return is being made, standardised -- a dropdown backed by a master.

sales_return.py already owns the return WINDOW (how many days late a return may be raised) and the
return APPROVAL (PM Workflow above a threshold). Both are about whether a return may go through at
all. This is a narrower, unrelated concern: once a return is allowed, what it says about itself.

Before this, "why" lived in core's own free-text Remarks field, spelled however the person raising
the credit note felt like typing it that day -- "damaged", "Damaged goods", "DAMAGE" all meant the
same thing to nobody but a human reading one invoice at a time. Sales Return Reason (see
sf_trading/sf_trading/doctype/sales_return_reason) gives the same nine causes a fixed name; the
Sales Invoice field `custom_return_reason` (a Link to it, visible only on a return) is what gets
picked, and its value is copied into Remarks so a report reading Remarks still finds a real answer
in it and every print format that already prints Remarks keeps working unchanged.

"Other" is the one reason that says nothing on its own, so it is the one case Remarks may not be
left to whatever the last screen happened to hold. The form enforces that live
(public/js/sales_return_reason.js, on the field's own change event); this is the same rule checked
again server-side, because a client-only rule is a rule a stale draft or a direct API call can
skip -- sales_return.py's own docstring makes the same point about the window check, for the same
reason.
"""

import frappe
from frappe import _
from frappe.utils import cint

OTHER_REASON = "Other"


def validate_return_reason(doc, method=None):
	"""validate on Sales Invoice: "Other" must explain itself in Remarks."""
	if not cint(doc.get("is_return")):
		return
	if doc.get("custom_return_reason") != OTHER_REASON:
		return
	if not (doc.get("remarks") or "").strip():
		frappe.throw(
			_("Please describe the return in Remarks when Return Reason is {0}.").format(
				frappe.bold(_(OTHER_REASON))
			),
			title=_("Return Reason Needs a Remark"),
		)
