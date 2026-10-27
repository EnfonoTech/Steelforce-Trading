# sf_trading/credit_customer_approval.py
"""The bypass roster of the master approval, and what is left of the earlier credit-customer approval.

The earlier approval -- a new Customer started Pending Verification and its Sales Invoice could not be
submitted until a Credit Approval Officer set it to Approved -- is now the Customer half of the master
approval (master_approval.py): a PM Workflow on the same `custom_approval_status`, held on the Sales
Order, Delivery Note and Sales Invoice, with requirements checked before it is approved. Its Pending
Verification customers carry over as Pending Approval, and the Credit Approval Officer is one of the
Customer approvers.

What stays here is the roster on SF Trading Settings (Allowed to Bypass Master Approval) -- roles or
users who may bypass, alongside System Manager -- which other gates of this app read the same way
(purchase_receipt_gate.py). The old entry points answer through the master approval, so a stale browser
or an outside caller gets the same answer as the form.
"""

import frappe
from frappe import _

SETTINGS = "SF Trading Settings"
OVERRIDE_FIELD = "credit_customer_approval_overrides"

PENDING = "Pending Approval"
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
	"""A new customer starts as a Draft of the Customer approval (master_approval.set_initial_status)."""
	from sf_trading.master_approval import set_initial_status

	set_initial_status(doc, method)


def validate_customer_approved_for_invoicing(doc, method=None):
	"""A sale to a customer the Customer approval has not approved is refused (returns are not)."""
	if doc.get("is_return") or not doc.get("customer"):
		return
	from sf_trading.master_approval import party_refusal

	message = party_refusal("Customer", doc.customer)
	if message:
		frappe.throw(message, title=_("Customer Not Approved"))


@frappe.whitelist()
def bypass_customer_approval(customer: str) -> None:
	"""The old Bypass Approval button: the master approval's bypass, with the button as the reason."""
	from sf_trading.master_approval import bypass_approval

	bypass_approval("Customer", customer, _("Bypassed from the credit approval button"))


@frappe.whitelist()
def get_approval_state(customer: str) -> dict:
	"""The old form call, answered by the master approval."""
	from sf_trading.master_approval import get_approval_state as master_state

	state = master_state("Customer", customer)
	approved = not state.get("enabled") or not state.get("covered") or state.get("approved")
	return {"status": state.get("status") or APPROVED, "approved": bool(approved),
		"can_bypass": bool(state.get("can_bypass"))}
