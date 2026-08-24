# sf_trading/sales_return.py
"""What Steel Force allows a sales return to be, and when it needs approving.

Both halves used to live in permission_manager, on PM Settings -- an app shared by every client
running it, where a switch added for this client ships to all of them. They live here now, on
**SF Trading Settings**, and permission_manager keeps only the machinery: the PM Workflow engine,
the states, the approval chain. The policy is this app's.

Two independent controls, both off until switched on:

**The window** -- a return may only be raised within N days, counted either from the invoice being
returned (the usual retail rule) or from the return's own posting date. Roles or users named in
the settings may raise one anyway, and so may Administrator. Enforced in three places, because a
control that only refuses at the last one is a control people work around:

  1. `make_sales_return` -- our stand-in for erpnext's endpoint. Refuses before the credit note is
     built, which is the one point no stale browser can get past.
  2. `validate_return_window` on Sales Invoice validate -- the save itself, draft included.
  3. the form (public/js/sales_return_window.js) -- declines to paint Return / Credit Note and
     says why, so nobody is offered what will be refused.

**The approval** -- a return above a threshold may only be submitted through the PM Workflow on
Sales Invoice. permission_manager asks this module whether a given document is governed, through
its `pm_workflow_applicability` hook: a workflow is defined per doctype, and without an answer
from here a Sales Invoice workflow would govern every invoice on the site and take Submit off
ordinary sales. Returning "not governed" settles the whole surface at once -- the form restores
its native Submit and the submit guard has no state to refuse.
"""

import frappe
from frappe import _
from frappe.utils import cint, date_diff, flt, fmt_money, getdate, nowdate

SETTINGS = "SF Trading Settings"
RETURN_DOCTYPE = "Sales Invoice"
OVERRIDE_FIELD = "sales_return_overrides"
FROM_INVOICE = "Original Invoice Date"
FROM_POSTING = "Return Posting Date"


def settings():
	return frappe.get_cached_doc(SETTINGS)


def _setting(fieldname, default=None):
	"""Read one switch, tolerating a bench that has the code but not the doctype yet."""
	try:
		return settings().get(fieldname)
	except frappe.DoesNotExistError:
		return default


# ─── The window ───────────────────────────────────────────────────────────────

def window_enabled() -> bool:
	return bool(cint(_setting("restrict_sales_return")))


def allowed_days() -> int:
	return cint(_setting("sales_return_days"))


def counted_from() -> str:
	return _setting("sales_return_days_from") or FROM_INVOICE


def may_override(user: str | None = None) -> bool:
	"""Whether this user is named in the settings, by role or by name."""
	user = user or frappe.session.user
	if user == "Administrator":
		return True

	rows = _setting(OVERRIDE_FIELD) or []
	if not rows:
		return False

	user_roles = set(frappe.get_roles(user))
	for row in rows:
		if not row.override:
			continue
		if row.override_type == "User" and row.override == user:
			return True
		if row.override_type == "Role" and row.override in user_roles:
			return True
	return False


def _return_date(doc):
	return getdate(doc.get("posting_date") or doc.get("transaction_date") or nowdate())


def _original_date(doc):
	"""The date of the document being returned, or nothing when it names none."""
	against = doc.get("return_against")
	if not against:
		return None
	date = frappe.db.get_value(doc.doctype, against, "posting_date") or frappe.db.get_value(
		doc.doctype, against, "transaction_date"
	)
	return getdate(date) if date else None


def age_in_days(doc) -> int | None:
	"""How old this return is, by whichever basis the settings name.

	None means there is nothing to measure -- a return naming no original document while the
	invoice-date basis is in force. That is a deliberate credit note, not a late return.
	"""
	if counted_from() == FROM_POSTING:
		return date_diff(getdate(nowdate()), _return_date(doc))

	original = _original_date(doc)
	if not original:
		return None
	return date_diff(_return_date(doc), original)


def _basis_phrase() -> str:
	return (
		_("the invoice being returned")
		if counted_from() == FROM_INVOICE
		else _("the return's own posting date")
	)


def validate_return_window(doc, method=None):
	"""validate on Sales Invoice: refuse a return raised outside the window."""
	if not cint(doc.get("is_return")):
		return
	if doc.docstatus != 0:
		return
	if not window_enabled():
		return

	days = allowed_days()
	age = age_in_days(doc)
	if age is None or age <= days:
		return

	if may_override():
		frappe.msgprint(
			_("This return is {0} day(s) old and the window is {1} day(s). Allowed because you may "
			  "override the sales return window.").format(age, days),
			title=_("Return Window Overridden"),
			indicator="orange",
		)
		return

	frappe.throw(
		_("A {0} may only be raised within {1} day(s) of {2}. This one is {3} day(s) old.").format(
			_(doc.doctype), days, _basis_phrase(), age
		)
		+ "<br><br>"
		+ _("Ask someone authorised to override the sales return window."),
		title=_("Return Window Has Passed"),
	)


def window_state(doctype: str, docname: str) -> dict:
	"""Where a return raised today against this document would stand."""
	probe = frappe._dict(
		doctype=doctype, docstatus=0, is_return=1, return_against=docname, posting_date=nowdate()
	)
	age = age_in_days(probe)
	days = allowed_days()
	can_override = may_override()
	return {
		"enabled": True,
		"days": days,
		"age": age,
		"can_override": can_override,
		"past_window": bool(age is not None and age > days),
		"blocked": bool(age is not None and age > days and not can_override),
		"basis": counted_from(),
	}


@frappe.whitelist()
def check_source_return_window(doctype: str, docname: str) -> dict:
	"""Would a return raised today against this document be refused? Asked by the invoice form."""
	frappe.has_permission(doctype, "read", doc=docname, throw=True)

	if not window_enabled():
		return {"enabled": False, "blocked": False}
	return window_state(doctype, docname)


@frappe.whitelist()
def make_sales_return(source_name, target_doc=None):
	"""ERPNext's Return / Credit Note action, refused before it builds anything.

	Registered through `override_whitelisted_methods`, so it stands in for erpnext's own endpoint.
	This is the check that cannot be missed or go stale: the form script only helps once the
	browser has it, and the toolbar is re-painted on every render.
	"""
	guard_return_creation(RETURN_DOCTYPE, source_name)

	from erpnext.accounts.doctype.sales_invoice.sales_invoice import (
		make_sales_return as erpnext_make_sales_return,
	)

	return erpnext_make_sales_return(source_name, target_doc)


def guard_return_creation(doctype: str, source_name: str):
	"""Refuse to start a return on a document whose window has closed."""
	if not source_name or not window_enabled():
		return

	state = window_state(doctype, source_name)
	if not state["past_window"]:
		return

	if state["can_override"]:
		frappe.msgprint(
			_("This invoice is {0} day(s) old and the return window is {1} day(s). Allowed because "
			  "you may override it.").format(state["age"], state["days"]),
			title=_("Return Window Overridden"),
			indicator="orange",
		)
		return

	frappe.throw(
		_("{0} {1} is {2} day(s) old. A return may only be raised within {3} day(s) of it.").format(
			_(doctype), frappe.bold(source_name), state["age"], state["days"]
		)
		+ "<br><br>"
		+ _("Ask someone authorised to override the sales return window."),
		title=_("Return Window Has Passed"),
	)


# ─── The approval ─────────────────────────────────────────────────────────────

def approval_enabled() -> bool:
	return bool(cint(_setting("si_return_approval_enabled")))


def threshold() -> float:
	return flt(_setting("si_return_approval_threshold"))


def restricts_by_amount() -> bool:
	return bool(cint(_setting("si_return_amount_restriction")))


def workflow_is_mandatory() -> bool:
	return bool(cint(_setting("si_return_requires_workflow")))


def is_sales_return(doc) -> bool:
	"""A Sales Invoice that is a return. `doc` may be a document or a plain dict."""
	if not doc:
		return False
	doctype = doc.get("doctype") if hasattr(doc, "get") else None
	return doctype == RETURN_DOCTYPE and bool(cint(doc.get("is_return")))


def return_amount(doc) -> float:
	"""The return's value in company currency, positive -- a credit note carries it negative."""
	return abs(
		flt(doc.get("base_rounded_total") or doc.get("base_grand_total") or doc.get("grand_total"))
	)


def needs_approval(doc) -> bool:
	"""Whether this document may only be submitted through the approval chain.

	The cheap test first: permission_manager asks this on the submit of every document of every
	doctype, and all but a handful are not sales returns at all.
	"""
	if not is_sales_return(doc) or not approval_enabled():
		return False
	if not restricts_by_amount():
		return True
	return return_amount(doc) - threshold() > 0.0001


def _no_workflow_message(doc) -> str:
	currency = frappe.get_cached_value("Company", doc.get("company"), "default_currency")
	return _(
		"{0} is a return of {1}, which needs approval, but no active PM Workflow covers {2}."
	).format(
		frappe.bold(doc.get("name")),
		frappe.bold(fmt_money(return_amount(doc), currency=currency)),
		_(RETURN_DOCTYPE),
	)


def workflow_applicability(doctype: str, doc=None) -> dict | None:
	"""Answer permission_manager's `pm_workflow_applicability` hook.

	None means "no opinion" -- every other doctype on the site, and Sales Invoice while the
	feature is off, so the engine behaves exactly as it did.
	"""
	if doctype != RETURN_DOCTYPE or not approval_enabled():
		return None

	# The doctype is routed in part: Sales Invoice carries a workflow meant for returns above the
	# threshold, so it must stay off the boot-time list of wholly-governed doctypes or every
	# ordinary sale loses its Submit button for a round trip.
	verdict = {"conditional": True}

	if doc is None:
		# a question about the doctype, not a document; the authority is the check that runs with
		# the document in hand
		return verdict

	governed = needs_approval(doc)
	verdict.update(
		{
			"applies": governed,
			"guard_submit": governed,
			"require_workflow": governed and workflow_is_mandatory(),
		}
	)
	if governed:
		verdict["message"] = _no_workflow_message(doc)
	return verdict


@frappe.whitelist()
def get_return_approval_settings() -> dict:
	"""The approval rule itself, for the form to apply to a document it is still holding.

	`get_return_approval_state` needs a saved document; the payment popup has to decide before
	there is one -- a return typed on a new invoice never reaches the server until it is saved,
	and by then the popup has already offered Save & Submit. So the rule comes down instead and
	the form applies it to the totals in front of it.
	"""
	if not approval_enabled():
		return {"enabled": False}

	return {
		"enabled": True,
		"by_amount": restricts_by_amount(),
		"threshold": threshold(),
	}


@frappe.whitelist()
def get_return_approval_state(sales_invoice: str) -> dict:
	"""What the Sales Invoice form asks so it can say why Submit is not on offer."""
	frappe.has_permission(RETURN_DOCTYPE, "read", doc=sales_invoice, throw=True)

	doc = frappe.get_doc(RETURN_DOCTYPE, sales_invoice)
	if not approval_enabled() or not is_sales_return(doc):
		return {"enabled": approval_enabled(), "needs_approval": False}

	return {
		"enabled": True,
		"needs_approval": needs_approval(doc),
		"amount": return_amount(doc),
		"threshold": threshold() if restricts_by_amount() else None,
		"currency": frappe.get_cached_value("Company", doc.company, "default_currency"),
	}
