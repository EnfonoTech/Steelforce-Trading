# sf_trading/credit_customer_approval.py
"""Credit customer verification, gating Sales Invoice on a Customer's approval status.

A new Customer starts Pending Verification and stays unavailable for Sales Invoice until
someone with the Credit Approval Officer role opens the record and sets it to Approved (or
Rejected) -- the field is visible to everyone (so any tester or salesperson can SEE why a
customer is blocked) but read-only except for a Credit Approval Officer / Accounts Manager /
System Manager, so this is the whole "workflow": one of those roles reviews, one field
decides. Existing customers are grandfathered in as Approved by
the Custom Field's own default ("Approved") -- fixture-sync backfills every row that has
none the moment the column is added, so only customers created from here on ever see
Pending Verification (default_new_customer_status below sets that explicitly on insert,
overriding the column default for a brand new row).

The bypass exists for the case a real customer needs invoicing today and verification
cannot finish in time -- deliberately narrow (a roster on SF Trading Settings, same shape as
sales_return.py's own override table) and always logged, because a bypass nobody can see
used is indistinguishable from the gate not existing.
"""

import frappe
from frappe import _

SETTINGS = "SF Trading Settings"
OVERRIDE_FIELD = "credit_customer_approval_overrides"

PENDING = "Pending Verification"
APPROVED = "Approved"
REJECTED = "Rejected"


def settings():
	return frappe.get_cached_doc(SETTINGS)


def may_bypass(user: str | None = None) -> bool:
	"""Whether this user is named in the bypass roster, by role or by name."""
	user = user or frappe.session.user
	if user == "Administrator":
		return True

	rows = settings().get(OVERRIDE_FIELD) or []
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


def default_new_customer_status(doc, method=None):
	"""before_insert on Customer: a brand new customer starts unverified.

	Only fires on insert, so it never touches an existing customer being re-saved --
	those were grandfathered in as Approved by the custom field's own default.
	"""
	if not doc.get("custom_approval_status"):
		doc.custom_approval_status = PENDING


def validate_customer_approved_for_invoicing(doc, method=None):
	"""before_submit on Sales Invoice: refuse an invoice for a customer not yet approved.

	before_submit, not validate -- a draft may still be saved while verification is in
	progress elsewhere; only SUBMIT (the actual billable moment) is refused, matching the
	same rationale sales_order_governance.py already uses for its own credit-customer checks.

	A return doesn't extend new credit, so it is exempt -- otherwise a customer stuck in
	Pending could never have a credit note raised against something already billed.
	"""
	if doc.get("is_return") or not doc.get("customer"):
		return

	status = frappe.db.get_value("Customer", doc.customer, "custom_approval_status")
	if not status or status == APPROVED:
		return

	if may_bypass():
		return

	message = _("%(customer)s is not yet approved for invoicing (status: %(status)s). Ask "
	            "someone authorised to bypass credit approval, or complete verification on "
	            "the Customer first.") % {"customer": frappe.bold(doc.customer), "status": _(status)}
	frappe.throw(message, title=_("Customer Not Approved"))


@frappe.whitelist()
def bypass_customer_approval(customer: str) -> None:
	"""Make a customer available for Sales Invoice without the normal approval step.

	Gated on the same roster the invoice-time check itself reads, so there is exactly one
	place that decides who may do this. Logged as a comment on the Customer -- a bypass with
	no trail is indistinguishable from the check just being broken.
	"""
	frappe.has_permission("Customer", "write", doc=customer, throw=True)
	if not may_bypass():
		frappe.throw(_("Not permitted to bypass credit customer approval"), frappe.PermissionError)

	frappe.db.set_value("Customer", customer, "custom_approval_status", APPROVED)
	comment_content = _("Credit approval bypassed by %(user)s") % {"user": frappe.session.user}
	frappe.get_doc(
		{
			"doctype": "Comment",
			"comment_type": "Info",
			"reference_doctype": "Customer",
			"reference_name": customer,
			"content": comment_content,
		}
	).insert(ignore_permissions=True)


@frappe.whitelist()
def get_approval_state(customer: str) -> dict:
	"""What the Customer form asks so it can offer (or hide) the Bypass button."""
	frappe.has_permission("Customer", "read", doc=customer, throw=True)
	status = frappe.db.get_value("Customer", customer, "custom_approval_status") or APPROVED
	return {
		"status": status,
		"approved": status == APPROVED,
		"can_bypass": may_bypass(),
	}
