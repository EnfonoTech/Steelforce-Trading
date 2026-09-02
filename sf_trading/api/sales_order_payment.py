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
	company = frappe.get_cached_value(
		"Company", so.company, ["write_off_account", "custom_max_payment_write_off"], as_dict=True
	) or frappe._dict()

	return {
		"sales_order": so.name,
		"company": so.company,
		"currency": so.currency,
		"precision": _currency_precision(so.currency),
		"grand_total": flt(so.get("rounded_total") or so.get("grand_total")),
		"advance_paid": flt(so.get("advance_paid")),
		"balance": _order_balance(so),
		"per_billed": flt(so.get("per_billed")),
		# what the Loyalty field may do here, read once with everything else. Same two Company
		# settings the invoice popup reads, under the same names.
		"write_off_account": company.get("write_off_account"),
		"max_write_off": flt(company.get("custom_max_payment_write_off")),
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
	write_off_amount: float | str = 0,
):
	"""One submitted Payment Entry per mode of payment, as an advance against the order.

	Args:
		sales_order: submitted Sales Order name
		payments: JSON list (or list) of {"mode_of_payment": str, "amount": float}
		cheque_date / cheque_no: the cheque's own date and number. The posting date stays
			today's -- the cheque date rides on `reference_date`, which is what the PDC
			Report and the cheque reminder read.
		posting_date: overrides today, for a collection being recorded after the fact.
		write_off_amount: the "Loyalty" the counter is giving up, booked as a deduction on the
			LAST Payment Entry against the Company's Write Off Account -- the same mechanism,
			the same Company settings and the same cap as
			`sales_invoice_payment.create_pos_payments_for_invoice`. On this company the
			account is "Loyalty Rewards" and the cap is 0.400, which is what this is for:
			closing a fils-level shortfall so the order reads fully advanced.

			It may only CLOSE an order. Cash plus loyalty must equal the whole remaining
			balance, exactly as the invoice popup demands of an invoice, because forgiving
			part of an order nobody is settling would drop the balance with no bill behind it.

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

	write_off_amount = flt(write_off_amount, precision)
	write_off_account = None
	write_off_cost_center = None
	if write_off_amount < 0:
		frappe.throw(_("Loyalty amount cannot be negative."))
	if write_off_amount > 0:
		company_defaults = frappe.get_cached_value(
			"Company",
			so.company,
			["write_off_account", "custom_max_payment_write_off", "cost_center", "default_currency"],
			as_dict=True,
		) or frappe._dict()
		company_currency = company_defaults.get("default_currency")

		# A deduction row is ALWAYS in company currency -- `Payment Entry Deduction.amount`
		# carries options "Company:company:default_currency" -- while the allocation is in the
		# party account's. On a foreign-currency order `paid_amount = allocated - write_off`
		# cannot satisfy the base-currency identity ERPNext checks, and the entry dies inside
		# submit() on "Difference Amount must be zero" (payment_entry.py:119) -- by which time
		# an earlier mode of the same collection has already posted. Refused up front instead.
		if company_currency and so.currency != company_currency:
			frappe.throw(
				_("Loyalty is only available on an order in {0}. Order {1} is in {2}.").format(
					company_currency, so.name, so.currency
				)
			)

		# Loyalty is for the last fils of an order nobody has billed yet. Once billing has
		# started the receivable exists, and that is where the invoice popup's own Loyalty
		# belongs -- forgiving fils here would leave an advance only a future invoice can use.
		if flt(so.get("per_billed")) > 0.01:
			frappe.throw(
				_("Order {0} is already {1}% billed. Take the loyalty on the invoice instead.").format(
					so.name, flt(so.per_billed, 2)
				)
			)

		write_off_account = company_defaults.get("write_off_account")
		if not write_off_account:
			frappe.throw(_("Set 'Write Off Account' on company {0}.").format(so.company))

		# ERPNext builds the deduction's GL row inside submit() and throws
		# "Currency for {0} must be {1}" there. Same check, before anything is created.
		account_currency = frappe.get_cached_value("Account", write_off_account, "account_currency")
		if company_currency and account_currency and account_currency != company_currency:
			frappe.throw(
				_("Write Off Account {0} must be in {1} to be used as Loyalty.").format(
					write_off_account, company_currency
				)
			)

		max_write_off = flt(company_defaults.get("custom_max_payment_write_off"))
		if not max_write_off:
			frappe.throw(
				_("Set 'Max Payment Write Off' on company {0} to allow write off in payments.").format(
					so.company
				)
			)
		if write_off_amount - max_write_off > 0.0001:
			frappe.throw(
				_("Loyalty amount {0} exceeds the company limit of {1}.").format(
					write_off_amount, max_write_off
				)
			)

		write_off_cost_center = so.get("cost_center") or company_defaults.get("cost_center")
		if not write_off_cost_center:
			frappe.throw(_("Set a default Cost Center on company {0}.").format(so.company))

		# Loyalty closes an order or it does nothing. Anything less would forgive part of an
		# order nobody is settling, and the balance would fall with no invoice behind it.
		if abs(flt(asked + write_off_amount, precision) - balance_now) > 0.0001:
			frappe.throw(
				_("Loyalty may only close an order: {0} plus loyalty of {1} must equal the "
				  "balance of {2} on order {3}.").format(
					asked, write_off_amount, balance_now, so.name
				)
			)

	if flt(asked + write_off_amount, precision) - balance_now > 0.0001:
		frappe.throw(
			_("Total payment {0} is more than the balance of {1} on order {2}.").format(
				flt(asked + write_off_amount, precision), balance_now, so.name
			)
		)

	from erpnext.accounts.doctype.payment_entry.payment_entry import get_payment_entry
	from erpnext.accounts.doctype.sales_invoice.sales_invoice import get_bank_cash_account

	cheque_modes = _cheque_modes(so)
	created = []
	last_index = len(valid_rows) - 1
	for index, row in enumerate(valid_rows):
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

		# the whole write-off rides on the last mode, as it does on the invoice side: split
		# across modes it would need a deduction row per entry for no gain
		row_write_off = write_off_amount if index == last_index else 0.0

		reference = pe.references[0]
		# the allocation covers the cash AND the loyalty, which is what closes the order; the
		# deduction row below is what funds the difference
		allocated = min(amount + row_write_off, balance, flt(reference.outstanding_amount))
		reference.allocated_amount = allocated
		# paid_amount is the cash that actually moved. ERPNext's own identity is
		# paid_amount = allocated - deductions, which is why the deduction row is appended
		# whenever one is carried here.
		pe.paid_amount = flt(allocated - row_write_off, precision)
		pe.received_amount = pe.paid_amount

		if row_write_off:
			pe.append(
				"deductions",
				{
					"account": write_off_account,
					"cost_center": write_off_cost_center,
					"amount": row_write_off,
					"description": _("Loyalty"),
				},
			)

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
