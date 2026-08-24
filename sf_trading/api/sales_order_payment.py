# sf_trading/api/sales_order_payment.py
"""Collect money against a Sales Order, the same way the Sales Invoice popup does.

The invoice popup (api/sales_invoice_payment.py) exists because a cashier should not have
to build a Payment Entry by hand for a sale they have just rung up. An order taken with a
deposit has exactly the same problem, so this is the order-side twin: the same branch
allow-list of modes of payment, the same one-Payment-Entry-per-mode result, the same
cheque handling.

Two things are deliberately different, because an order is not an invoice:

* There is no Loyalty (write-off) field. A write-off closes an outstanding invoice by
  booking the shortfall to the company's Write Off Account; an order has no receivable to
  close, so there is nothing to write off and offering the field would only invite a
  posting nobody can explain later.

* "Amount to Pay" is the order's own balance -- `grand_total` less `advance_paid` -- not an
  invoice's `outstanding_amount`. ERPNext keeps `advance_paid` in the party account's
  currency and refreshes it from the Advance Payment Ledger Entry table on every payment
  submit, which is why the balance is re-read from the order between each mode rather than
  worked out once up front.

Everything the resulting Payment Entry is (an advance against the order, party account,
account currencies, exchange rate) comes from ERPNext's own `get_payment_entry`, so the
entries this creates are indistinguishable from ones made through the desk.
"""

import json

import frappe
from frappe import _
from frappe.utils import cint, flt, nowdate

from sf_trading.api.sales_invoice_payment import (
	branch_has_pdc_modes,
	get_payment_modes_with_account,
)


def _currency_precision(currency: str) -> int:
	"""Decimals the currency is actually kept in (BHD is three, not two)."""
	number_format = frappe.db.get_value("Currency", currency, "number_format") or "#,###.##"
	return len(number_format.split(".")[-1]) if "." in number_format else 0


def _order_balance(so) -> float:
	"""What is still uncollected on the order.

	`advance_paid` is denominated in the party account's currency while `grand_total` is in
	the order's own currency. They only differ on a foreign-currency order, and this site
	sells in company currency, so they are compared directly and the balance is clamped at
	zero rather than pretending to a precision the field cannot carry.
	"""
	total = flt(so.get("rounded_total") or so.get("grand_total"))
	return max(0.0, flt(total - flt(so.get("advance_paid")), _currency_precision(so.currency)))


@frappe.whitelist()
def get_sales_order_payment_state(sales_order: str) -> dict:
	"""Everything the Receive Payment dialog needs, in one round trip.

	The modes come back split the same way the invoice popup splits them -- cheque modes
	(the branch's `for_pdc` rows) apart from the rest -- so the dialog can ask for a cheque
	number only when a cheque amount is actually entered.
	"""
	frappe.has_permission("Sales Order", "read", doc=sales_order, throw=True)

	so = frappe.get_doc("Sales Order", sales_order)
	branch = so.get("branch") or ""

	return {
		"sales_order": so.name,
		"company": so.company,
		"currency": so.currency,
		"precision": _currency_precision(so.currency),
		"grand_total": flt(so.get("rounded_total") or so.get("grand_total")),
		"advance_paid": flt(so.get("advance_paid")),
		"balance": _order_balance(so),
		"per_billed": flt(so.get("per_billed")),
		"modes": get_payment_modes_with_account(so.company, is_return=0, is_pdc=0, branch=branch),
		"pdc_modes": (
			get_payment_modes_with_account(so.company, is_return=0, is_pdc=1, branch=branch)
			if branch_has_pdc_modes(branch)
			else []
		),
	}


@frappe.whitelist()
def create_payments_for_sales_order(
	sales_order: str,
	payments: str | list,
	cheque_date: str = None,
	cheque_no: str = None,
	posting_date: str = None,
):
	"""One submitted Payment Entry per mode of payment, as an advance against the order.

	Args:
		sales_order: submitted Sales Order name
		payments: JSON list (or list) of {"mode_of_payment": str, "amount": float}
		cheque_date / cheque_no: the cheque's own date and number. The posting date stays
			today's -- the cheque date rides on `reference_date`, which is what the PDC
			Report and the cheque reminder read.
		posting_date: overrides today, for a collection being recorded after the fact.

	Returns:
		List of created Payment Entry names.
	"""
	if not sales_order:
		frappe.throw(_("Sales Order is required"))

	frappe.has_permission("Sales Order", "read", doc=sales_order, throw=True)
	frappe.has_permission("Payment Entry", "create", throw=True)

	so = frappe.get_doc("Sales Order", sales_order)
	if so.docstatus != 1:
		frappe.throw(_("Sales Order {0} must be submitted before collecting payment.").format(so.name))

	# The form hides the button on a closed order; this is the same answer for anything reaching
	# the endpoint directly. A closed order has been abandoned and one on hold is disputed --
	# taking money against either is a decision somebody has to make deliberately, by reopening it.
	if so.status in ("Closed", "On Hold"):
		frappe.throw(
			_("Sales Order {0} is {1}. Reopen it before collecting a payment against it.").format(
				so.name, _(so.status)
			)
		)

	if isinstance(payments, str):
		try:
			payments = json.loads(payments)
		except Exception:
			frappe.throw(_("Invalid payments payload"))

	if not isinstance(payments, (list, tuple)) or not payments:
		frappe.throw(_("No payment rows were provided."))

	valid_rows = []
	for row in payments:
		mode_of_payment = (row or {}).get("mode_of_payment")
		amount = flt((row or {}).get("amount"))
		if not mode_of_payment or amount <= 0:
			continue
		valid_rows.append({"mode_of_payment": mode_of_payment, "amount": amount})

	if not valid_rows:
		frappe.throw(_("No valid payment rows found (non-zero amounts with mode of payment)."))

	precision = _currency_precision(so.currency)
	asked = flt(sum(row["amount"] for row in valid_rows), precision)
	balance_now = _order_balance(so)
	if asked - balance_now > 0.0001:
		frappe.throw(
			_("Total payment {0} is more than the balance of {1} on order {2}.").format(
				asked, balance_now, so.name
			)
		)

	from erpnext.accounts.doctype.payment_entry.payment_entry import get_payment_entry
	from erpnext.accounts.doctype.sales_invoice.sales_invoice import get_bank_cash_account

	cheque_modes = _cheque_modes(so)
	created = []
	for row in valid_rows:
		# Re-read the order: ERPNext refreshes `advance_paid` from the advance ledger when a
		# payment is submitted, so the balance the next mode may take is only known now.
		so.reload()
		balance = _order_balance(so)
		amount = flt(row["amount"], precision)
		if amount <= 0 or balance <= 0:
			continue

		pe = get_payment_entry("Sales Order", so.name)
		pe.mode_of_payment = row["mode_of_payment"]

		bank_account = (get_bank_cash_account(row["mode_of_payment"], so.company) or {}).get("account")
		if not bank_account:
			frappe.throw(
				_("Set a default account for mode of payment {0} on company {1}.").format(
					row["mode_of_payment"], so.company
				)
			)
		pe.paid_to = bank_account
		account = frappe.get_cached_value(
			"Account", bank_account, ["account_currency", "account_type"], as_dict=True
		)
		if account:
			pe.paid_to_account_currency = account.account_currency
			pe.paid_to_account_type = account.account_type

		# Never allocate past the order's own balance: `get_payment_entry` fills the row with
		# the whole outstanding, and ERPNext refuses a Payment Entry allocating more than the
		# order still owes. `outstanding_amount` on the row is left exactly as ERPNext wrote
		# it -- rewriting it trips "has already been partly paid" on submit.
		if not pe.references:
			frappe.throw(_("Order {0} has nothing left to collect against.").format(so.name))

		reference = pe.references[0]
		allocated = min(amount, balance, flt(reference.outstanding_amount))
		reference.allocated_amount = allocated
		pe.paid_amount = allocated
		pe.received_amount = allocated

		pe.posting_date = posting_date or nowdate()
		pe.reference_no = cheque_no or so.name
		if cheque_date and row["mode_of_payment"] in cheque_modes:
			pe.reference_date = cheque_date
		else:
			pe.reference_date = pe.posting_date

		pe.insert()

		# Same reasoning as the invoice popup: an entry the cashier never sees cannot be
		# walked through an approval chain, and the collection has already happened.
		pe.flags.ignore_workflow = True
		pe.flags.ignore_validate = True
		pe.submit()
		created.append(pe.name)

	return created


def _cheque_modes(so) -> set:
	"""The branch's cheque (`for_pdc`) modes, so only those carry the cheque date."""
	branch = so.get("branch") or ""
	if not branch or not cint(branch_has_pdc_modes(branch)):
		return set()
	return set(get_payment_modes_with_account(so.company, is_return=0, is_pdc=1, branch=branch))
