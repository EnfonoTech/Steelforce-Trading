# sf_trading/credit_limit.py
"""When ERPNext's credit-limit check applies at this company, in one place.

Core measures a customer's exposure as the receivable in the GL **plus every unbilled Sales
Order** (`base_grand_total x (100 - per_billed) / 100`) plus unbilled Delivery Notes. An order is
money not yet invoiced and not yet collected, so core counts it whatever the counter intends to do
about payment -- it has never heard of `custom_payment_mode`.

That is why a CASH order tripped the limit on production while a cash invoice for the same
customer sails through: the bypass had been written into `CustomSalesInvoice.check_credit_limit`
and Sales Order carried no override at all. Now both read this function.

Cash is exempt because the money is in the drawer before the document is submitted.

**Cheque is NOT exempt, deliberately.** On this site Cheque means a post-dated cheque -- the PDC
account, the maturity date in `reference_date`, the whole PDC report exists for it. Until it
clears, that is credit the customer is holding, which is exactly what a credit limit is for.
"""

import frappe
from frappe import _
from frappe.utils import flt, fmt_money

# the modes on this site are Cash / Credit / Cheque
SETTLED_AT_THE_COUNTER = ("Cash",)


def paid_at_the_counter(doc) -> bool:
	"""True when this document's money is in hand, so no credit is being extended."""
	return (doc.get("custom_payment_mode") or "").strip() in SETTLED_AT_THE_COUNTER


def skip_credit_limit(doc) -> bool:
	"""Whether ERPNext's credit-limit check should be skipped for this document."""
	return paid_at_the_counter(doc)


# ── Branch-wise sub-allocation (GS Issue 19) ──────────────────────────────────────────────────
#
# The company-wide check above (core's own check_credit_limit, via skip_credit_limit) already
# nets a customer's exposure across every branch -- one receivable ledger per company, not per
# branch (see party_accounts.py). That already catches "5,000 at Branch A + 5,000 at Branch B"
# in aggregate, PROVIDED a real Customer Credit Limit > 0 is on file; a 0/unset limit is core's
# own "unlimited" (erpnext/selling/doctype/customer/customer.py: `if not credit_limit: return`),
# which is what actually let that case through.
#
# What core has no concept of is a client wanting to sub-allocate that one company-wide number
# across branches -- e.g. Branch A may extend at most 5,000 of the customer's 8,000 total. That
# is what this section adds: an OPTIONAL per-branch credit_limit on the customer's own Branch
# Access row (sf_trading/doctype/customer_branch_access), on top of, never instead of, the
# company-wide check. See customer_permission.validate_branch_credit_limit_allocation for the
# setup-time rule that keeps every branch's sub-limit from adding up to more than the total.


def branch_sub_limit(customer: str, branch: str) -> float:
	"""This customer's own Branch Access row's credit_limit for `branch`, or 0 if that branch
	carries no sub-limit of its own -- uncapped at the branch level, still bound only by the
	company-wide check above."""
	if not (customer and branch):
		return 0
	return flt(
		frappe.db.get_value("Customer Branch Access", {"parent": customer, "branch": branch}, "credit_limit")
	)


def branch_credit_exposure(customer: str, branch: str, exclude: str | None = None) -> float:
	"""Every OTHER submitted Sales Invoice's outstanding_amount, plus every OTHER open Sales
	Order's unbilled amount, for this customer at this branch -- the same two-part shape core uses
	company-wide (this module's own docstring), including core's own `status != 'Closed'` guard
	(erpnext/selling/doctype/customer/customer.py), narrowed to one branch via the `branch`
	accounting dimension both doctypes already carry.

	`exclude` is always applied -- an empty string never matches a real document name -- so the
	document being checked is never double-counted regardless of its own docstatus at the moment
	of the call. The caller adds that document's own current amount back on top explicitly.
	"""
	outstanding_si = frappe.db.sql(
		"""
		SELECT COALESCE(SUM(outstanding_amount), 0)
		FROM `tabSales Invoice`
		WHERE customer = %(customer)s AND branch = %(branch)s AND docstatus = 1
		AND name != %(exclude)s
		""",
		{"customer": customer, "branch": branch, "exclude": exclude or ""},
	)[0][0]

	unbilled_so = frappe.db.sql(
		"""
		SELECT COALESCE(SUM(base_grand_total * (100 - per_billed) / 100), 0)
		FROM `tabSales Order`
		WHERE customer = %(customer)s AND branch = %(branch)s AND docstatus = 1
		AND per_billed < 100 AND status != 'Closed'
		AND name != %(exclude)s
		""",
		{"customer": customer, "branch": branch, "exclude": exclude or ""},
	)[0][0]

	return flt(outstanding_si) + flt(unbilled_so)


def check_branch_credit_limit(doc) -> None:
	"""Sales Order / Sales Invoice check_credit_limit: refuse a document that would push THIS
	branch's own exposure for this customer past that branch's own Credit Limit sub-allocation --
	only when that branch actually carries a non-zero sub-limit (branch_sub_limit). A Cash
	document is exempt, same as the company-wide check; a blank branch or blank sub-limit means
	nothing new to enforce here."""
	if skip_credit_limit(doc):
		return

	branch = doc.get("branch")
	customer = doc.get("customer")
	if not (branch and customer):
		return

	limit = branch_sub_limit(customer, branch)
	if not limit:
		return

	existing = branch_credit_exposure(customer, branch, exclude=doc.name)
	if doc.doctype == "Sales Invoice":
		this_doc = flt(doc.get("outstanding_amount"))
	else:
		this_doc = flt(doc.get("base_grand_total")) * (100 - flt(doc.get("per_billed") or 0)) / 100
	projected = existing + this_doc

	if projected > limit:
		frappe.throw(
			_("%s's exposure at Branch %s would reach %s, over that branch's own Credit Limit of "
				"%s. Existing exposure at this branch (other documents) is %s.")
			% (
				doc.get("customer_name") or customer,
				branch,
				fmt_money(projected),
				fmt_money(limit),
				fmt_money(existing),
			),
			title=_("Branch Credit Limit Exceeded"),
		)
