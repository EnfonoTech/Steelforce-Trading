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
from frappe.utils import cstr

from sf_trading.party_contact_cache import party_phone_numbers

#: Role allowed to cancel a submitted Sales Order. Reuses the same role name the account's
#: existing Payment Advice workflow already uses for "the person in charge of one branch" --
#: see sf_trading.api.payment_advice_workflow.ROLE_BRANCH_HEAD -- so a client asking "who is a
#: Branch Head" only has one role to look at, not two spellings of the same idea.
ROLE_BRANCH_HEAD = "Branch Head"

#: How many open Sales Orders one customer may hold before new ones are blocked (GS Issue 18).
#: "Open" here means submitted and not yet fully delivered+billed. Scoped per-customer: the call's
#: own wording ("if more than two are pending") did not name salesman or branch, and per-customer is
#: the reading that matches Issue 19's own real example (one customer, two branches, both missed).
#: Revisit if the client means a different grouping.
PENDING_SO_CAP = 2

_OPEN_STATUSES = ("To Deliver and Bill", "To Bill", "To Deliver")


def ensure_custom_fields():
	"""after_migrate: create custom_cancellation_remark if it is not already there.

	allow_on_submit -- both writers of this field (cancel_sales_order_with_remark and the
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
			]
		},
		ignore_validate=True,
		update=True,
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


def validate_customer_contact_at_transaction(doc, _method=None):
	"""Sales Invoice / Sales Order validate: block on a customer with no phone on file.

	Runs for a document against an EXISTING customer exactly as much as a brand new one -- there is
	no is_new()/is_new customer guard -- which is the "for existing records too" half of GS Issue 20.
	"""
	if not doc.get("customer"):
		return
	if missing_contact_phone("Customer", doc.customer):
		frappe.throw(
			_("Customer %s has no phone number on file. Add a Contact with a phone number before billing.")
			% (doc.customer_name or doc.customer),
			title=_("Customer Contact Incomplete"),
		)


def missing_credit_customer_requirements(customer: str) -> list[str]:
	"""GS Issue 13: a credit customer needs 2 contact numbers and at least one attachment.

	"Credit customer" is not a new checkbox -- this account's own customer_permission.py already
	treats a Customer Credit Limit row with credit_limit > 0 as exactly that marker (it is what
	auto_add_branch_on_credit_limit and validate_credit_branch_access key off), so this reuses the
	same signal rather than adding a second, possibly-disagreeing flag. Attachment is checked via
	the generic File-attached-to mechanism api/customer_override.py already uses for the VAT-document
	rule -- not yet the classified document_type + expiry_date child table GS Issue 12 asks for,
	which needs its own new DocType and is still pending separately.
	"""
	is_credit_customer = frappe.db.exists(
		"Customer Credit Limit", {"parent": customer, "credit_limit": [">", 0]}
	)
	if not is_credit_customer:
		return []

	missing = []
	phone_count = len(party_phone_numbers("Customer", customer))
	if phone_count < 2:
		missing.append(_("at least 2 contact numbers (found %d)") % phone_count)
	if not frappe.db.exists("File", {"attached_to_doctype": "Customer", "attached_to_name": customer}):
		missing.append(_("at least one attachment"))
	return missing


def validate_credit_customer_requirements_at_transaction(doc, _method=None):
	"""Sales Invoice / Sales Order validate: a credit customer additionally needs 2 contacts +
	an attachment on file (GS Issue 13's field/validation half; the approval-workflow half -- who
	is "credit dept" vs "accounts" -- is still blocked on the client naming those roles)."""
	if not doc.get("customer"):
		return
	missing = missing_credit_customer_requirements(doc.customer)
	if missing:
		frappe.throw(
			_("Credit customer %s is missing: %s") % (doc.customer_name or doc.customer, ", ".join(missing)),
			title=_("Credit Customer Requirements Incomplete"),
		)


def before_cancel_require_remark_and_branch_head(doc, _method=None):
	"""Sales Order before_cancel: a remark is mandatory, and only a Branch Head may cancel.

	``before_cancel`` is the correct hook for both checks -- it runs BEFORE docstatus flips to 2,
	so throwing here genuinely leaves the order uncancelled. (``on_cancel`` is too late: docstatus
	is already 2 by the time it runs, which is the account's own well-documented cancel-ordering
	trap.) The remark itself is expected to already be on the in-memory document by this point --
	set by ``cancel_sales_order_with_remark`` below, or by the client-side ``before_cancel`` form
	event for a cancel started from the desk -- never asked for here, because a hook that runs after
	the cancel HTTP call has already begun cannot itself pop a dialog.
	"""
	if not cstr(doc.get("custom_cancellation_remark")).strip():
		frappe.throw(
			_("A remark is required to cancel a Sales Order."),
			title=_("Cancellation Remark Required"),
		)

	user_roles = frappe.get_roles(frappe.session.user)
	if ROLE_BRANCH_HEAD not in user_roles and "System Manager" not in user_roles:
		frappe.throw(
			_("Only a %s may cancel a Sales Order.") % ROLE_BRANCH_HEAD,
			title=_("Not Permitted"),
		)


@frappe.whitelist()
def cancel_sales_order_with_remark(sales_order: str, remark: str):
	"""Whitelisted entry point for the desk dialog: stamp the remark, then cancel.

	Permission is re-checked here explicitly (frappe.has_permission), not assumed from the button
	being visible -- a whitelisted method is a public HTTP endpoint the moment it exists, regardless
	of which desk screens choose to call it.
	"""
	if not frappe.has_permission("Sales Order", "cancel", doc=sales_order):
		frappe.throw(_("Not permitted to cancel this Sales Order."), frappe.PermissionError)

	remark = cstr(remark).strip()
	if not remark:
		frappe.throw(_("A remark is required to cancel a Sales Order."))

	doc = frappe.get_doc("Sales Order", sales_order)
	# Set on the object, not via db_set -- db_set would bump `modified` in the database while
	# cancel() still validates the in-memory copy's own modified timestamp, which is exactly the
	# TimestampMismatchError trap the account's own coding standard calls out. Setting the field
	# directly and letting cancel() persist it keeps everything on one save.
	doc.custom_cancellation_remark = remark
	doc.cancel()
	return {"name": doc.name, "docstatus": doc.docstatus}


def count_open_sales_orders(customer: str, exclude: str | None = None) -> int:
	filters = {
		"customer": customer,
		"docstatus": 1,
		"status": ["in", _OPEN_STATUSES],
	}
	if exclude:
		filters["name"] = ["!=", exclude]
	return frappe.db.count("Sales Order", filters)


def before_submit_cap_pending_orders(doc, _method=None):
	"""Sales Order before_submit: refuse a new order once the customer already has
	PENDING_SO_CAP or more open (GS Issue 18)."""
	if not doc.get("customer"):
		return

	existing = count_open_sales_orders(doc.customer, exclude=doc.name)
	if existing >= PENDING_SO_CAP:
		frappe.throw(
			_(
				"Customer %s already has %d open Sales Orders (limit %d). "
				"Clear the existing ones before submitting another."
			)
			% (doc.customer_name or doc.customer, existing, PENDING_SO_CAP),
			title=_("Too Many Open Sales Orders"),
		)


def notify_customers_over_pending_cap():
	"""Daily scheduled job: tell Sales Manager who is currently sitting at/over the cap.

	A notify-only companion to the hard block above -- so the situation is visible before it
	becomes a refused submission, not only after.
	"""
	rows = frappe.db.sql(
		"""
		SELECT customer, customer_name, COUNT(*) AS open_orders
		FROM `tabSales Order`
		WHERE docstatus = 1 AND status IN %(statuses)s
		GROUP BY customer
		HAVING COUNT(*) >= %(cap)s
		""",
		{"statuses": _OPEN_STATUSES, "cap": PENDING_SO_CAP},
		as_dict=True,
	)
	if not rows:
		return

	lines = "".join(
		"<li>%s (%s): %d open orders</li>" % (r.customer_name or r.customer, r.customer, r.open_orders)
		for r in rows
	)
	for user in frappe.get_all(
		"Has Role", filters={"role": "Sales Manager", "parenttype": "User"}, pluck="parent"
	):
		frappe.sendmail(
			recipients=[user],
			subject=_("Customers at or over the open Sales Order limit"),
			message="<ul>%s</ul>" % lines,
			now=False,
		)
