import json

import frappe
from frappe import _
from frappe.utils import cint, flt


@frappe.whitelist()
def get_available_credit(customer: str, company: str) -> float:
	"""
	Return the available credit for a customer:
	  credit_limit (from Customer Credit Limit child table)
	  minus sum of outstanding_amount on submitted Sales Invoices
	  where custom_payment_mode = 'Credit'.
	"""
	if not customer or not company:
		return 0.0

	# Credit limit defined on Customer for this company
	row = frappe.db.get_value(
		"Customer Credit Limit",
		{"parent": customer, "company": company},
		"credit_limit",
	)
	if row is None:
		# Fallback: first credit limit regardless of company
		row = frappe.db.get_value(
			"Customer Credit Limit",
			{"parent": customer},
			"credit_limit",
		)
	credit_limit = flt(row)
	if not credit_limit:
		return 0.0

	used = frappe.db.sql(
		"""
		SELECT IFNULL(SUM(grand_total), 0)
		FROM `tabSales Invoice`
		WHERE customer = %s
		  AND company = %s
		  AND custom_payment_mode = 'Credit'
		  AND docstatus = 1
		""",
		(customer, company),
	)[0][0]

	return max(0.0, flt(credit_limit) - flt(used))


@frappe.whitelist()
def get_payment_modes_with_account(company: str, is_return: int | str = 0, is_pdc: int | str = 0, branch: str = None):
	"""
	Return Mode of Payment names that are enabled and have a default Cash/Bank
	account for the given company. Use this to avoid "Please set default Cash or
	Bank account" or disabled-mode errors when showing the payment popup.

	The list is built from all enabled modes with a default account, then
	restricted to the branch allowlist. POS Profile is not consulted.

	Args:
		company: Company name
		is_return: 1 when the invoice is a return (filters to for_return modes)
		is_pdc: 1 when collecting PDC modes (filters to for_pdc modes)
		branch: Branch to restrict Cash/Bank modes to (from the document)

	Returns:
		List of mode names that are enabled and have default account for company.
	"""
	if not company:
		return []

	# Modes that have a default account for this company
	has_account = frappe.db.sql(
		"""
		SELECT DISTINCT parent
		FROM `tabMode of Payment Account`
		WHERE company = %s AND default_account IS NOT NULL AND default_account != ''
		""",
		(company,),
		as_list=True,
	)
	modes_with_account = {r[0] for r in has_account}

	enabled = frappe.get_all(
		"Mode of Payment",
		filters={"enabled": 1},
		pluck="name",
		ignore_permissions=True,
	)

	# Intersection: enabled and has default account for company
	valid = [m for m in enabled if m in modes_with_account]

	# PDC filter: applied globally regardless of user/branch.
	# for_pdc MoPs only appear in PDC mode; they are excluded from all other popups.
	all_pdc_mops = set(
		frappe.get_all(
			"Branch Configuration Mode of Payment",
			filters={"for_pdc": 1},
			pluck="mode_of_payment",
			ignore_permissions=True,
		)
	)
	if frappe.utils.cint(is_pdc):
		if all_pdc_mops:
			valid = [m for m in valid if m in all_pdc_mops]
	else:
		if all_pdc_mops:
			valid = [m for m in valid if m not in all_pdc_mops]

	valid = _restrict_to_branch_allowlist(valid, company, is_return=frappe.utils.cint(is_return), branch=branch)
	return valid


@frappe.whitelist()
def branch_has_pdc_modes(branch: str) -> bool:
	"""Return True if the branch has at least one 'For PDC' mode of payment configured."""
	if not branch:
		return False
	return bool(
		frappe.db.exists(
			"Branch Configuration Mode of Payment",
			{"parent": branch, "parenttype": "Branch Configuration", "for_pdc": 1},
		)
	)


def _restrict_to_branch_allowlist(modes: list, company: str, is_return: int = 0, branch: str = None) -> list:
	"""
	Return only the MOPs that are configured in the document branch's
	Branch Configuration Mode of Payment table.

	- No branch set → return empty list (show nothing).
	- Branch has no MOPs configured → return empty list (show nothing).
	- No role bypasses — rule applies to everyone.
	"""
	if not modes:
		return []

	if not branch:
		return []

	filters = {"parent": branch, "parenttype": "Branch Configuration"}
	if is_return:
		filters["for_return"] = 1

	configured = frappe.get_all(
		"Branch Configuration Mode of Payment",
		filters=filters,
		pluck="mode_of_payment",
		ignore_permissions=True,
	)

	if not configured:
		return []

	allowed = set(configured)
	return [m for m in modes if m in allowed]


@frappe.whitelist()
def get_accounts_for_modes(company: str, modes: str | list):
	"""Return {mode_of_payment: account} for each mode in the list.

	Uses the same logic as Sales Invoice.set_account_for_mode_of_payment but
	doesn't require write permission on the document.
	"""
	if not company:
		return {}
	if isinstance(modes, str):
		try:
			modes = json.loads(modes) if modes else []
		except Exception:
			modes = []
	if not modes:
		return {}

	from erpnext.accounts.doctype.sales_invoice.sales_invoice import get_bank_cash_account

	result = {}
	for mode in modes:
		if mode:
			result[mode] = (get_bank_cash_account(mode, company) or {}).get("account") or ""
	return result


@frappe.whitelist()
def loyalty_already_given(sales_invoice) -> dict:
	"""Loyalty already taken against the Sales Orders this invoice bills.

	The company cap is enforced per PAYMENT, so without this one sale could take it twice --
	once when the order was collected and again when the invoice was, each check knowing
	nothing of the other. The order-side collection is a Payment Entry that references the
	Sales Order and carries the loyalty as a deduction row, so the amount is readable; no new
	field is needed to remember it.

	Only orders NAMED BY THIS INVOICE's item rows can be found this way. An invoice raised
	fresh instead of from the order names none, and nothing links the two -- see the module
	docstring of `sales_order_payment` for what that leaves open.
	"""
	invoice = (
		sales_invoice
		if not isinstance(sales_invoice, str)
		else frappe.get_doc("Sales Invoice", sales_invoice)
	)

	orders = sorted({row.sales_order for row in (invoice.get("items") or []) if row.get("sales_order")})
	if not orders:
		return {"amount": 0.0, "orders": []}

	# Read through the ADVANCE LEDGER, not through the Payment Entry's reference row. When the
	# invoice is submitted ERPNext applies the advance and REWRITES that row from the Sales
	# Order to the Sales Invoice (`update_reference_in_payment_entry` in accounts/utils.py), so
	# a query keyed on the order finds the loyalty while the invoice is a draft and loses it the
	# moment it is submitted -- measured on UAT: 0.300 before submit, 0.000 after.
	# `Advance Payment Ledger Entry` keeps naming the order, which is what makes this stable.
	rows = frappe.db.sql(
		"""
		SELECT DISTINCT ded.name AS deduction, ded.amount, aple.against_voucher_no AS sales_order
		FROM `tabAdvance Payment Ledger Entry` aple
		INNER JOIN `tabPayment Entry` pe ON pe.name = aple.voucher_no AND pe.docstatus = 1
		INNER JOIN `tabPayment Entry Deduction` ded ON ded.parent = pe.name
		WHERE aple.against_voucher_type = 'Sales Order'
			AND aple.against_voucher_no IN %(orders)s
			AND aple.delinked = 0
		""",
		{"orders": tuple(orders)},
		as_dict=True,
	)

	# one deduction can be reached through several ledger rows for the same order (a Submit and
	# an Allocate event), so each is counted once
	seen = {}
	orders_seen = set()
	for row in rows:
		seen[row.deduction] = flt(row.amount)
		orders_seen.add(row.sales_order)

	return {"amount": flt(sum(seen.values()), 3), "orders": sorted(orders_seen)}


def unapplied_order_advance(customer: str, company: str) -> dict:
	"""Advances collected against a Sales Order that no invoice has consumed yet.

	This is the case `loyalty_already_given` cannot see. An invoice raised FRESH instead of
	from the order names no order on its item rows, so nothing links the two documents --
	and on this site that is the normal habit: 3,462 of 3,469 invoices since August carry
	`allocate_advances_automatically = 0`, which sf_trading itself sets when no item names an
	order, so the advance is never applied and the invoice opens at its full value. The
	customer is then asked to pay a second time, and the loyalty could be given a second time
	with it.

	"Not consumed yet" is read as an order-sourced payment with no Sales Invoice reference row.
	That is measured, not assumed: while the advance is outstanding the payment's only
	reference is the Sales Order; when an invoice consumes it, ERPNext REPLACES that row with
	a Sales Invoice one. The advance ledger keeps naming the order either way, which is what
	identifies the payment as order-sourced in the first place.
	"""
	if not (customer and company):
		return {"advance": 0.0, "loyalty": 0.0, "orders": []}

	rows = frappe.db.sql(
		"""
		SELECT pe.name,
			(SELECT COALESCE(SUM(ref.allocated_amount), 0) FROM `tabPayment Entry Reference` ref
			 WHERE ref.parent = pe.name) AS allocated,
			(SELECT COALESCE(SUM(ded.amount), 0) FROM `tabPayment Entry Deduction` ded
			 WHERE ded.parent = pe.name) AS loyalty,
			(SELECT GROUP_CONCAT(DISTINCT aple.against_voucher_no)
			 FROM `tabAdvance Payment Ledger Entry` aple
			 WHERE aple.voucher_no = pe.name AND aple.against_voucher_type = 'Sales Order'
			   AND aple.delinked = 0) AS orders
		FROM `tabPayment Entry` pe
		WHERE pe.docstatus = 1
			AND pe.party_type = 'Customer'
			AND pe.party = %(customer)s
			AND pe.company = %(company)s
			AND EXISTS (
				SELECT 1 FROM `tabAdvance Payment Ledger Entry` aple
				WHERE aple.voucher_no = pe.name AND aple.against_voucher_type = 'Sales Order'
					AND aple.delinked = 0
			)
			AND NOT EXISTS (
				SELECT 1 FROM `tabPayment Entry Reference` ref
				WHERE ref.parent = pe.name AND ref.reference_doctype = 'Sales Invoice'
			)
		""",
		{"customer": customer, "company": company},
		as_dict=True,
	)

	orders = set()
	advance = loyalty = 0.0
	for row in rows:
		advance += flt(row.allocated)
		loyalty += flt(row.loyalty)
		orders.update((row.orders or "").split(","))

	return {
		"advance": flt(advance, 3),
		"loyalty": flt(loyalty, 3),
		"orders": sorted(o for o in orders if o),
	}


@frappe.whitelist()
def get_loyalty_state(sales_invoice: str) -> dict:
	"""What the invoice payment popup may offer for Loyalty, in one call.

	Exposed so the dialog can leave the field out entirely when the order already carried the
	loyalty for this sale, rather than offering something the server is going to refuse.
	"""
	frappe.has_permission("Sales Invoice", "read", doc=sales_invoice, throw=True)

	si = frappe.get_doc("Sales Invoice", sales_invoice)
	company = frappe.get_cached_value(
		"Company", si.company, ["write_off_account", "custom_max_payment_write_off"], as_dict=True
	) or frappe._dict()
	already = loyalty_already_given(si)
	unapplied = unapplied_order_advance(si.customer, si.company)

	return {
		"write_off_account": company.get("write_off_account"),
		"max_write_off": flt(company.get("custom_max_payment_write_off")),
		"already_given": already["amount"],
		"already_given_on": already["orders"],
		# the customer is holding money for an order nobody has billed. Reported even when no
		# loyalty rode on it, because the cashier is about to ask for the whole invoice again.
		"unapplied_advance": unapplied["advance"],
		"unapplied_loyalty": unapplied["loyalty"],
		"unapplied_orders": unapplied["orders"],
		"allowed": (
			not cint(si.is_return)
			and already["amount"] <= 0.0001
			and unapplied["loyalty"] <= 0.0001
		),
	}


def create_pos_payments_for_invoice(
	sales_invoice: str,
	payments: str | list,
	cheque_date: str = None,
	cheque_no: str = None,
	posting_date: str = None,
	write_off_amount: float | str = 0,
):
	"""
	Create Payment Entry records for a submitted POS Sales Invoice, one per mode of payment.

	Args:
		sales_invoice: Sales Invoice name
		payments: JSON list or Python list of dicts:
			[{ "mode_of_payment": "Cash", "amount": 100.0 }, ...]
		posting_date: Payment Entry posting and reference date; defaults to the
			invoice's posting date (submit-time collection). The Receive Payment
			flow passes the actual payment date.
		write_off_amount: Small unpaid balance to book as a deduction on the
			last Payment Entry (same mechanism as the PE form's "Write Off
			Difference Amount" button). Uses the Company's Write Off Account,
			capped by the Company's Max Payment Write Off, cost center from the
			invoice (fallback: company default).

	Returns:
		List of created Payment Entry names.
	"""

	if not sales_invoice:
		frappe.throw(_("Sales Invoice is required"))

	si = frappe.get_doc("Sales Invoice", sales_invoice)

	if si.docstatus != 1:
		frappe.throw(
			_("Sales Invoice {0} must be submitted before creating payments.").format(
				si.name
			)
		)

	# Parse payments argument
	if isinstance(payments, str):
		try:
			payments = json.loads(payments)
		except Exception:
			frappe.throw(_("Invalid payments payload"))

	if not isinstance(payments, (list, tuple)) or not payments:
		frappe.throw(_("No payment rows were provided."))

	# Filter and validate rows (Cash and non-Cash; for Bank we set reference_no/reference_date)
	valid_rows = []
	for row in payments:
		mode_of_payment = (row or {}).get("mode_of_payment")
		amount = frappe.utils.flt((row or {}).get("amount"))
		if not mode_of_payment or amount <= 0:
			continue
		valid_rows.append({"mode_of_payment": mode_of_payment, "amount": amount})

	if not valid_rows:
		frappe.throw(_("No valid payment rows found (non-zero amounts with mode of payment)."))

	# Write-off: validate limit and resolve account/cost center up front,
	# before any Payment Entry is created.
	write_off_amount = flt(write_off_amount)
	if write_off_amount < 0:
		frappe.throw(_("Write off amount cannot be negative."))
	write_off_account = None
	write_off_cost_center = None
	if write_off_amount > 0:
		if si.is_return:
			frappe.throw(_("Write off is not allowed on return invoices."))
		company_defaults = frappe.db.get_value(
			"Company",
			si.company,
			["write_off_account", "custom_max_payment_write_off", "cost_center"],
			as_dict=True,
		)
		max_write_off = flt(company_defaults.custom_max_payment_write_off)
		if not max_write_off:
			frappe.throw(
				_("Set 'Max Payment Write Off' on company {0} to allow write off in payments.").format(si.company)
			)
		if write_off_amount - max_write_off > 0.0001:
			frappe.throw(
				_("Write off amount {0} exceeds the company limit of {1}.").format(
					write_off_amount, max_write_off
				)
			)
		write_off_account = company_defaults.write_off_account
		if not write_off_account:
			frappe.throw(_("Set 'Write Off Account' on company {0}.").format(si.company))
		write_off_cost_center = si.get("cost_center") or company_defaults.cost_center
		if not write_off_cost_center:
			frappe.throw(
				_("Set a Cost Center on the invoice or a default Cost Center on company {0}.").format(si.company)
			)

		# The cap is per payment, so one sale could otherwise take it twice: once when the
		# order was collected, once here. Loyalty already given against an order this invoice
		# bills is loyalty for THIS sale, and it is not given again.
		already = loyalty_already_given(si)
		if already["amount"] > 0.0001:
			frappe.throw(
				_("Loyalty of {0} was already given on {1}. It cannot be given again on this "
				  "invoice.").format(already["amount"], ", ".join(already["orders"]))
			)

		# The invoice may name no order at all and still be the second half of the same sale.
		# An order advance nobody has consumed, carrying loyalty, is that case.
		unapplied = unapplied_order_advance(si.customer, si.company)
		if unapplied["loyalty"] > 0.0001:
			frappe.throw(
				_("{0} is holding an unapplied advance of {1} from {2}, and loyalty of {3} was "
				  "already given on it. Apply that advance to this invoice — or raise the "
				  "invoice from the order — before giving loyalty again.").format(
					si.customer, unapplied["advance"], ", ".join(unapplied["orders"]),
					unapplied["loyalty"],
				)
			)

	created = []

	from erpnext.accounts.doctype.payment_entry.payment_entry import get_payment_entry
	from erpnext.accounts.doctype.sales_invoice.sales_invoice import get_bank_cash_account

	number_format = frappe.db.get_value("Currency", si.currency, "number_format") or "#,###.##"
	currency_precision = len(number_format.split(".")[-1]) if "." in number_format else 0

	for idx, row in enumerate(valid_rows):
		# Reload invoice each time so outstanding is up to date after previous payments
		si.reload()
		outstanding = frappe.utils.flt(si.outstanding_amount, currency_precision)
		amount = frappe.utils.flt(row["amount"], currency_precision)

		if amount <= 0:
			continue

		# The write-off rides on the last Payment Entry: it allocates
		# payment + write-off against the invoice, balanced by a deduction row.
		row_write_off = write_off_amount if idx == len(valid_rows) - 1 else 0

		if amount + row_write_off - abs(outstanding) > 0.0001:
			frappe.throw(
				_(
					"Payment amount {0} plus write off is greater than outstanding amount {1} for invoice {2}."
				).format(amount + row_write_off, outstanding, si.name)
			)

		pe = get_payment_entry("Sales Invoice", si.name)

		# Set the specific mode of payment and amount
		pe.mode_of_payment = row["mode_of_payment"]

		bank_cash = get_bank_cash_account(row["mode_of_payment"], si.company)
		bank_account = bank_cash.get("account")

		# For return SIs, payment_type is "Pay": cash/bank goes on paid_from.
		# For normal SIs, payment_type is "Receive": cash/bank goes on paid_to.
		if pe.payment_type == "Pay":
			pe.paid_from = bank_account
			if bank_account:
				acc = frappe.get_cached_value(
					"Account", bank_account, ["account_currency", "account_type"], as_dict=True
				)
				if acc:
					pe.paid_from_account_currency = acc.account_currency
		else:
			pe.paid_to = bank_account
			if bank_account:
				acc = frappe.get_cached_value(
					"Account", bank_account, ["account_currency", "account_type"], as_dict=True
				)
				if acc:
					pe.paid_to_account_currency = acc.account_currency
					pe.paid_to_account_type = acc.account_type

		# Clamp to the reference row's actual outstanding to avoid sub-cent validation
		# failures (e.g. user pays 1.11 but DB outstanding is 1.109 after rounding).
		if pe.references:
			ref = pe.references[0]
			ref_outstanding = frappe.utils.flt(ref.outstanding_amount)
			effective_amount = min(amount, abs(ref_outstanding))
			pe.paid_amount = effective_amount
			pe.received_amount = effective_amount
			allocated = min(effective_amount + row_write_off, abs(ref_outstanding))
			ref.allocated_amount = -allocated if pe.payment_type == "Pay" else allocated
		else:
			if row_write_off:
				frappe.throw(
					_("Cannot apply write off: no outstanding reference found for invoice {0}.").format(si.name)
				)
			pe.paid_amount = amount
			pe.received_amount = amount

		if row_write_off:
			pe.append(
				"deductions",
				{
					"account": write_off_account,
					"cost_center": write_off_cost_center,
					"amount": row_write_off,
				},
			)

		# PDC: posting date = invoice date; reference date = future cheque date
		if cheque_date:
			pe.posting_date = si.posting_date
			pe.reference_no = cheque_no or si.name
			pe.reference_date = cheque_date
		else:
			pe.posting_date = posting_date or si.posting_date
			pe.reference_no = si.name
			pe.reference_date = posting_date or si.posting_date

		pe.insert()

		# Bypass workflow state transition validation for auto-created PEs.
		# ignore_workflow skips the "Draft -> Pending" transition check.
		# ignore_validate skips before_submit hooks (e.g. attachment requirements).
		pe.flags.ignore_workflow = True
		pe.flags.ignore_validate = True

		pe.submit()
		created.append(pe.name)

	return created

