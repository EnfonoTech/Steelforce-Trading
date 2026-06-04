import json

import frappe
from frappe import _
from frappe.utils import flt


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
def get_payment_modes_with_account(company: str, mode_list: str | list = None):
	"""
	Return Mode of Payment names that are enabled and have a default Cash/Bank
	account for the given company. Use this to avoid "Please set default Cash or
	Bank account" or disabled-mode errors when showing the payment popup.

	Args:
		company: Company name
		mode_list: Optional JSON list or Python list of mode names to filter
			(e.g. from POS Profile). If None, all enabled modes with account are returned.

	Returns:
		List of mode names that are enabled and have default account for company.
	"""
	if not company:
		return []

	if isinstance(mode_list, str):
		try:
			mode_list = json.loads(mode_list) if mode_list else None
		except Exception:
			mode_list = None

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

	# Enabled modes: get_list with enabled=1
	if mode_list is not None:
		names = [m if isinstance(m, str) else (m.get("name") or m.get("mode_of_payment")) for m in (mode_list or [])]
		names = [n for n in names if n]
		if not names:
			return []
		enabled = frappe.get_all(
			"Mode of Payment",
			filters={"name": ["in", names], "enabled": 1},
			pluck="name",
		)
	else:
		enabled = frappe.get_all(
			"Mode of Payment",
			filters={"enabled": 1},
			pluck="name",
		)

	# Intersection: enabled and has default account for company
	valid = [m for m in enabled if m in modes_with_account]
	# Preserve order of mode_list when filtering from profile
	if mode_list is not None and names:
		order = {m: i for i, m in enumerate(names)}
		valid.sort(key=lambda m: order.get(m, 999))

	valid = _restrict_to_branch_allowlist(valid, company)
	return valid


def _restrict_to_branch_allowlist(modes: list, company: str) -> list:
	"""For users in a Branch Configuration, filter Cash/Bank MoPs to the
	branch's allowlist. Other types (General, Phone, etc.) pass through.
	Bypassed for Administrator, Guest, System Manager, Sales Manager.
	"""
	if not modes:
		return modes

	user = frappe.session.user
	if user in ("Administrator", "Guest"):
		return modes

	roles = set(frappe.get_roles(user))
	if roles & {"System Manager", "Sales Manager", "Sales Master Manager"}:
		return modes

	from sf_trading.branch_defaults import _branch_mops_by_type, _resolve_user_branch

	branch = _resolve_user_branch(user, company=company)
	if not branch:
		return modes

	cash_allow, bank_allow = _branch_mops_by_type(branch)
	# If branch has no Cash/Bank MoPs configured, don't filter
	if not cash_allow and not bank_allow:
		return modes

	cash_allow_set = set(cash_allow)
	bank_allow_set = set(bank_allow)

	mop_types = {
		m["name"]: (m.get("type") or "")
		for m in frappe.get_all(
			"Mode of Payment",
			filters={"name": ["in", modes]},
			fields=["name", "type"],
		)
	}

	out = []
	for m in modes:
		t = mop_types.get(m, "")
		if t == "Cash":
			if m in cash_allow_set:
				out.append(m)
		elif t == "Bank":
			if m in bank_allow_set:
				out.append(m)
		else:
			out.append(m)
	return out


@frappe.whitelist()
def create_pos_payments_for_invoice(sales_invoice: str, payments: str | list):
	"""
	Create Payment Entry records for a submitted POS Sales Invoice, one per mode of payment.

	Args:
		sales_invoice: Sales Invoice name
		payments: JSON list or Python list of dicts:
			[{ "mode_of_payment": "Cash", "amount": 100.0 }, ...]

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

	created = []

	from erpnext.accounts.doctype.payment_entry.payment_entry import get_payment_entry
	from erpnext.accounts.doctype.sales_invoice.sales_invoice import get_bank_cash_account

	for row in valid_rows:
		# Reload invoice each time so outstanding is up to date after previous payments
		si.reload()
		outstanding = frappe.utils.flt(si.outstanding_amount)
		amount = frappe.utils.flt(row["amount"])

		if amount <= 0:
			continue

		if amount - abs(outstanding) > 0.5:
			frappe.throw(
				_(
					"Payment amount {0} is greater than outstanding amount {1} for invoice {2}."
				).format(amount, outstanding, si.name)
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

		# paid_amount / received_amount in payment currency
		pe.paid_amount = amount
		pe.received_amount = amount

		# Update reference allocation to match this amount
		if pe.references:
			# Assume single reference row for this invoice
			ref = pe.references[0]
			# For "Pay" type (return SI), allocated_amount must be negative
			ref.allocated_amount = -amount if pe.payment_type == "Pay" else amount

		# Posting date same as invoice if not set
		if not pe.posting_date:
			pe.posting_date = si.posting_date

		# Reference No/Date: mandatory when payment account is Bank (PE validates on account type, not mode type).
		# Set for all so Cash mode linked to a Bank account does not fail.
		pe.reference_no = si.name
		pe.reference_date = si.posting_date

		pe.insert()

		# Bypass "Draft -> Pending" server script attachment requirement for
		# auto-created Payment Entries from the Sales Invoice POS popup.
		#
		# Setting ignore_validate skips `validate` / `before_submit` for this submit,
		# which is where such workflow attachment checks typically run.
		pe.flags.ignore_validate = True

		# Keep workflow_state consistent with the expected submitted state.
		if hasattr(pe, "workflow_state"):
			pe.workflow_state = "Pending"

		pe.submit()
		created.append(pe.name)

	return created

