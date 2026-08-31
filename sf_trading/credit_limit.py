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

# the modes on this site are Cash / Credit / Cheque
SETTLED_AT_THE_COUNTER = ("Cash",)


def paid_at_the_counter(doc) -> bool:
	"""True when this document's money is in hand, so no credit is being extended."""
	return (doc.get("custom_payment_mode") or "").strip() in SETTLED_AT_THE_COUNTER


def skip_credit_limit(doc) -> bool:
	"""Whether ERPNext's credit-limit check should be skipped for this document."""
	return paid_at_the_counter(doc)
