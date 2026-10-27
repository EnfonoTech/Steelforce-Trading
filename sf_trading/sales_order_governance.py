# sf_trading/sales_order_governance.py
"""Sales Order governance: contact-completeness at billing time, cancellation control, and a cap
on how many orders one customer may have open at once.

Three separate client asks (GS Issues 1/20, 17, 18), grouped in one module because all three read
the same "is this order safe to submit/cancel" question at the same hook point.

Every refusal here lives in ``validate``/``before_cancel`` -- never in a client-side popup or a
hidden button -- because this account has already proven that a client-side gate alone is not a
gate: PM Workflow approval, the API, the SO -> SI mapper and amendments all submit or act
server-side with no browser in the loop, so anything enforced only in JS is bypassed by exactly the
paths that matter (see the account's own "client popup is not a gate" trap note; a 12.439 BHD
cash-refund slipped through this account's live prod the same way).
"""

from __future__ import annotations

import frappe
from frappe import _
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields
from frappe.utils import cint, cstr

from sf_trading.party_contact_cache import party_email_addresses, party_phone_numbers

#: Role allowed to cancel a submitted Sales Order. Reuses the same role name the account's
#: existing Payment Advice workflow already uses for "the person in charge of one branch" --
#: see sf_trading.api.payment_advice_workflow.ROLE_BRANCH_HEAD -- so a client asking "who is a
#: Branch Head" only has one role to look at, not two spellings of the same idea.
ROLE_BRANCH_HEAD = "Branch Head"

# The fixed cap of 2 open Sales Orders per customer (GS Issue 18) and its daily notification were removed 2026-10-15: Sales Order
# limits are now configured per Customer and per Salesperson -- see sf_trading/sales_order_limits.py.

#: Customer checkbox: a credit customer whose credit documents are still missing may buy for CASH.
#: Credit (and Cheque, which is post-dated credit here) stays refused until the documents are in.
CASH_OVERRIDE_FIELD = "custom_allow_cash_without_credit_documents"
CASH_MODE = "Cash"
#: who may switch it -- the same people who decide a customer's credit approval
CASH_OVERRIDE_ROLES = ("Credit Approval Officer", "Accounts Manager", "System Manager")


def ensure_custom_fields():
	"""after_migrate: create custom_cancellation_remark and the cash-sale override if missing.

	allow_on_submit -- both writers of the remark (cancel_sales_order_with_remark and the
	client-side before_cancel event) act on a Sales Order that is already submitted.
	"""
	create_custom_fields(
		{
			"Sales Order": [
				{
					"fieldname": "custom_cancellation_remark",
					"label": "Cancellation Remark",
					"fieldtype": "Small Text",
					"insert_after": "status",
					"allow_on_submit": 1,
					"read_only": 1,
					"no_copy": 1,
				}
			],
			"Customer": [
				{
					"fieldname": CASH_OVERRIDE_FIELD,
					"label": "Allow Cash Sales Without Credit Documents",
					"fieldtype": "Check",
					"default": "0",
					# stays in Credit Approval; the documents grid has its own section on Settings
					"insert_after": "custom_approval_status",
					"description": (
						"Lets this credit customer be invoiced in Cash mode while their credit "
						"documents (2 contact numbers, an email address, an attachment) are still "
						"missing. Credit and Cheque sales stay refused until they are complete. Only a "
						"Credit Approval Officer, Accounts Manager or System Manager can change this."
					),
				}
			],
		},
		ignore_validate=True,
		update=True,
	)


def cash_sale_overrides_credit_documents(doc) -> bool:
	"""A Cash-mode document for a customer whose cash-sale override is ticked."""
	if (doc.get("custom_payment_mode") or "").strip() != CASH_MODE:
		return False
	return bool(cint(frappe.db.get_value("Customer", doc.customer, CASH_OVERRIDE_FIELD)))


def validate_cash_override_change(doc, _method=None):
	"""Customer validate: only a credit approver may switch the cash-sale override, either way."""
	before = doc.get_doc_before_save()
	was = cint(before.get(CASH_OVERRIDE_FIELD)) if before else 0
	if cint(doc.get(CASH_OVERRIDE_FIELD)) == was:
		return
	if frappe.session.user == "Administrator" or set(frappe.get_roles()) & set(CASH_OVERRIDE_ROLES):
		return
	roles = " / ".join(_(role) for role in CASH_OVERRIDE_ROLES)
	frappe.throw(
		_("Only a {0} can change Allow Cash Sales Without Credit Documents.").format(roles),
		title=_("Not Permitted"),
	)


def missing_contact_phone(party_doctype: str, party_name: str) -> bool:
	"""True when the party has no linked Contact carrying a phone/mobile number.

	Delegates to party_contact_cache.party_phone_numbers -- the same Dynamic-Link-to-Contact-Phone
	walk that fills the cached list-view field for GS Issue 11 -- so the billing gate and the list
	view can never disagree about what "this party has a phone" means. This is also why the check
	cannot run inside Customer/Supplier's own ``validate``: see party_completeness.py's module
	docstring for why a fresh customer has no Contact yet by the time its own first save runs.
	"""
	if not party_name:
		return True
	return not party_phone_numbers(party_doctype, party_name)


def _bypassed(customer: str) -> bool:
	"""A customer an admin bypassed is let through the requirement checks (master_approval.py)."""
	from sf_trading.master_approval import is_master_bypassed

	return is_master_bypassed("Customer", customer)


def validate_customer_contact_at_transaction(doc, _method=None):
	"""Sales Invoice / Sales Order validate: block on a customer with no phone on file.

	Runs for a document against an EXISTING customer exactly as much as a brand new one -- there is
	no is_new()/is_new customer guard -- which is the "for existing records too" half of GS Issue 20.
	"""
	if not doc.get("customer") or _bypassed(doc.customer):
		return
	if missing_contact_phone("Customer", doc.customer):
		frappe.throw(
			_("Customer %s has no phone number on file. Add a Contact with a phone number before billing.")
			% (doc.customer_name or doc.customer),
			title=_("Customer Contact Incomplete"),
		)


def is_credit_customer(customer: str) -> bool:
	"""A Customer Credit Limit row with credit_limit > 0 is this account's own existing marker for
	"credit customer" -- customer_permission.py's auto_add_branch_on_credit_limit and
	validate_credit_branch_access already key off exactly this, so every caller asking "is this a
	credit customer" reuses the same signal rather than risking a second, possibly-disagreeing flag."""
	return bool(frappe.db.exists("Customer Credit Limit", {"parent": customer, "credit_limit": [">", 0]}))


def _payment_terms(customer: str):
	return frappe.db.get_value("Customer", customer, "payment_terms")


def missing_credit_customer_requirements(customer: str) -> list[str]:
	"""GS Issue 13: a credit customer needs 2 contact numbers, an email address, Payment Terms and at
	least one attachment.

	2026-10-11, client: Payment Terms are mandatory for every credit-limit customer, old or new --
	the same footing as the email address (party_documents asks them of a NEW master from its first
	save; this makes an existing credit customer carry them before it is billed).

	2026-09-27: the 2-contact-number requirement used to ALSO apply separately to any B2B (VAT-
	bearing) customer via missing_b2b_phone_requirements -- dropped, client call: a B2B customer
	with no credit standing no longer needs a 2nd number; this is now the ONLY 2-contact-number
	rule. The same call also made email mandatory for a credit customer specifically (not every
	customer) -- checked via party_email_addresses (the linked Contact/Address, same reasoning as
	party_phone_numbers: the party's own fetch_from `email_id` column can lag the real value).
	Attachment is checked via the generic File-attached-to mechanism api/customer_override.py
	already uses for the VAT-document rule -- not yet the classified document_type + expiry_date
	child table GS Issue 12 asks for, which needs its own new DocType and is still pending
	separately.
	"""
	if not is_credit_customer(customer):
		return []

	missing = []
	phone_count = len(party_phone_numbers("Customer", customer))
	if phone_count < 2:
		missing.append(_("at least 2 contact numbers (found %d)") % phone_count)
	if not party_email_addresses("Customer", customer):
		missing.append(_("an email address"))
	if not cstr(_payment_terms(customer)).strip():
		missing.append(_("Payment Terms"))
	if not frappe.db.exists("File", {"attached_to_doctype": "Customer", "attached_to_name": customer}):
		missing.append(_("at least one attachment"))
	return missing


def validate_credit_customer_requirements_at_transaction(doc, _method=None):
	"""Sales Invoice / Sales Order validate: a credit customer additionally needs 2 contacts +
	an attachment on file (GS Issue 13's field/validation half; the approval-workflow half -- who
	is "credit dept" vs "accounts" -- is still blocked on the client naming those roles)."""
	if not doc.get("customer") or _bypassed(doc.customer):
		return
	if cash_sale_overrides_credit_documents(doc):
		return
	missing = missing_credit_customer_requirements(doc.customer)
	if missing:
		frappe.throw(
			_("Credit customer %s is missing: %s") % (doc.customer_name or doc.customer, ", ".join(missing)),
			title=_("Credit Customer Requirements Incomplete"),
		)


#: Who approves -- and so may carry out -- a Sales Order cancellation (client tracker #23: "Sales
#: Manager should approve"). Everyone else asks: Actions > Request Cancellation, which the Sales
#: Manager approves from the Approvals page (sf_trading/approval_routing.py); approving runs this
#: same cancel as the Sales Manager.
CANCEL_APPROVER_ROLES = ("Sales Manager", "System Manager")


def before_cancel_require_remark_and_branch_head(doc, _method=None):
	"""Sales Order before_cancel: a remark is mandatory, and only a Sales Manager may cancel.

	(The name is kept from when the role was Branch Head; hooks and tests refer to it.)

	``before_cancel`` is the correct hook for both checks -- it runs BEFORE docstatus flips to 2,
	so throwing here genuinely leaves the order uncancelled. (``on_cancel`` is too late: docstatus
	is already 2 by the time it runs, which is the account's own well-documented cancel-ordering
	trap.) The remark itself is expected to already be on the in-memory document by this point --
	set by the cancellation request (approval_routing.capture_cancellation_reason), by
	``cancel_sales_order_with_remark`` below, or by the client-side ``before_cancel`` form event for
	a cancel started from the desk -- never asked for here, because a hook that runs after the
	cancel HTTP call has already begun cannot itself pop a dialog.
	"""
	if not cstr(doc.get("custom_cancellation_remark")).strip():
		frappe.throw(
			_("A remark is required to cancel a Sales Order."),
			title=_("Cancellation Remark Required"),
		)
	# the reason picked from the Order Cancellation Reason list, as on a sales return
	from sf_trading.order_cancellation import require_reason

	require_reason(doc)

	user_roles = set(frappe.get_roles(frappe.session.user))
	if not user_roles & set(CANCEL_APPROVER_ROLES):
		frappe.throw(
			_("Only a Sales Manager may cancel a Sales Order. Use Actions > Request Cancellation to ask for it."),
			title=_("Approval Required"),
		)


@frappe.whitelist()
def cancel_sales_order_with_remark(sales_order: str, remark: str = None, reason: str = None, details: str = None):
	"""Whitelisted entry point: stamp the reason (picked from Order Cancellation Reason) and the
	remark it makes, then cancel. `remark` alone is still accepted as the details of the reason.

	Permission is re-checked here explicitly (frappe.has_permission), not assumed from the button
	being visible -- a whitelisted method is a public HTTP endpoint the moment it exists, regardless
	of which desk screens choose to call it.
	"""
	if not frappe.has_permission("Sales Order", "cancel", doc=sales_order):
		frappe.throw(_("Not permitted to cancel this Sales Order."), frappe.PermissionError)

	from sf_trading.order_cancellation import REASON_FIELD, compose_remark

	remark = cstr(remark).strip()
	if reason:
		remark = compose_remark("Sales Order", reason, details or remark)
	if not remark:
		frappe.throw(_("A remark is required to cancel a Sales Order."))

	doc = frappe.get_doc("Sales Order", sales_order)
	if reason:
		doc.set(REASON_FIELD, cstr(reason).strip())
	# Set on the object, not via db_set -- db_set would bump `modified` in the database while
	# cancel() still validates the in-memory copy's own modified timestamp, which is exactly the
	# TimestampMismatchError trap the account's own coding standard calls out. Setting the field
	# directly and letting cancel() persist it keeps everything on one save.
	doc.custom_cancellation_remark = remark
	doc.cancel()
	return {"name": doc.name, "docstatus": doc.docstatus}
