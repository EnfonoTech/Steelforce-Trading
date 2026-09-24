"""
Sales Invoice overrides: remove empty item rows before validation (barcode scanner scan row).
"""

from __future__ import annotations

import frappe
from frappe import _
from frappe.utils import today


def before_validate(doc, _method=None):
	"""Remove item rows that have no item_code (leftover scan row from barcode). Runs before validation."""
	# Frappe v15 field_order Property Setter bug: is_pos can arrive as a list
	# instead of a scalar. Coerce it before ERPNext's validation sees it.
	if isinstance(doc.is_pos, list):
		doc.is_pos = 1 if doc.is_pos else 0

	if not doc.get("items"):
		frappe.throw(_("Please add at least one item before saving."))

	# Remove in reverse so indices stay valid
	to_remove = [row for row in doc.items if not (row.get("item_code") or "").strip()]
	for row in to_remove:
		doc.remove(row)
	for i, row in enumerate(doc.items, start=1):
		row.idx = i

	if not doc.items:
		frappe.throw(_("Please add at least one item before saving."))


def validate(doc, _method=None):
	"""Block new credit invoice if customer has overdue unsettled credit."""
	if doc.is_return:
		return
	if doc.custom_payment_mode != "Credit":
		return
	if not doc.customer:
		return

	inv = _get_overdue_invoice(doc.customer, doc.company, as_of_date=doc.posting_date)
	if inv:
		inv_link = frappe.utils.get_link_to_form("Sales Invoice", inv.name)
		frappe.throw(
			_(
				"Cannot create a new credit invoice for {0}. "
				"Invoice {1} dated {2} has an outstanding amount of {3} "
				"that is overdue. Please settle the outstanding balance first."
			).format(
				frappe.bold(doc.customer),
				inv_link,
				inv.posting_date,
				frappe.utils.fmt_money(inv.outstanding_amount, currency=doc.currency),
			),
			title=_("Overdue Invoice"),
		)


def _get_overdue_invoice(customer, company, as_of_date=None):
	"""Return the oldest overdue credit invoice for the customer, or None.

	Uses the Credit Days configured on the Customer Credit Limit row for the
	given company. Returns None if no credit days are set (validation disabled).

	as_of_date is the date "overdue" is measured against - the posting date of
	the invoice being created, not necessarily the real calendar date. This
	matters when an invoice is deliberately post-dated: the credit-days clock
	should run from the older invoice's posting date to the new invoice's
	posting date, not to whatever today happens to be.
	"""
	credit_days = frappe.db.get_value(
		"Customer Credit Limit",
		{"parent": customer, "company": company},
		"custom_credit_days",
	)
	if not credit_days:
		return None

	rows = frappe.db.sql(
		"""
		SELECT name, posting_date, outstanding_amount
		FROM `tabSales Invoice`
		WHERE customer = %s
		  AND company = %s
		  AND custom_payment_mode = 'Credit'
		  AND docstatus = 1
		  AND outstanding_amount > 0
		  AND DATEDIFF(%s, posting_date) > %s
		ORDER BY posting_date ASC
		LIMIT 1
		""",
		(customer, company, as_of_date or today(), frappe.utils.cint(credit_days)),
		as_dict=True,
	)
	return rows[0] if rows else None


CASH_MODE = "Cash"


def clear_driver_for_non_cash(doc, method=None):
	"""A Delivery Person belongs to a cash sale, so drop it when the mode is anything else.

	The field is only *hidden* when the mode is not Cash -- it carries
	`depends_on: eval:doc.custom_payment_mode=="Cash"` -- and a hidden field keeps whatever was
	put in it. So picking a delivery person, then switching the mode to Credit or Cheque, saved
	the invoice with a delivery person nobody could see: one submitted Credit invoice on this
	site carries one, and every report and check that reads the field then quietly disagrees with
	the form.

	Cleared at before_validate rather than on the form alone, because the form is not the only
	way in -- and so `validate_driver_payment`, which runs later at validate, is not still
	checking a driver this invoice no longer has.
	"""
	if doc.get("custom_payment_mode") == CASH_MODE:
		return
	if doc.get("custom_driver"):
		doc.custom_driver = None


def validate_driver_payment(doc, _method=None):
	"""Block a new Cash+Driver document if that driver has an overdue unsettled invoice.

	Hooked on Sales Order as well as Sales Invoice, so every field is read with `get`: an order has
	no is_return, and attribute access would raise on it rather than simply finding nothing.
	"""
	if doc.get("is_return"):
		return
	if doc.get("custom_payment_mode") == "Credit":
		return
	if not doc.get("custom_driver"):
		return

	# `posting_date` belongs to the invoice; an order dates itself with `transaction_date`. Read
	# bare, it raised AttributeError on every Cash order that named a delivery person and the
	# order could not be saved at all (production, 2026-08-31 onward). `exclude_name` is scoped to
	# the invoice for the same reason: the query reads `tabSales Invoice`, so passing an order's
	# name there excluded nothing.
	inv = _get_driver_overdue_invoice(
		doc.custom_driver,
		doc.name if doc.doctype == "Sales Invoice" else None,
		as_of_date=doc.get("posting_date") or doc.get("transaction_date"),
	)
	if inv:
		inv_link = frappe.utils.get_link_to_form("Sales Invoice", inv.name)
		payment_days = frappe.db.get_value("Driver", doc.custom_driver, "custom_payment_days") or 1
		driver_name = frappe.db.get_value("Driver", doc.custom_driver, "full_name") or doc.custom_driver
		frappe.throw(
			_(
				"Delivery Person {0} has an unsettled invoice {1} dated {2} with outstanding amount {3}. "
				"Payment was due within {4} day(s). Please collect and record the payment first."
			).format(
				frappe.bold(driver_name),
				inv_link,
				inv.posting_date,
				frappe.utils.fmt_money(inv.outstanding_amount, currency=doc.currency),
				payment_days,
			),
			title=_("Delivery Person Payment Overdue"),
		)


def _get_driver_overdue_invoice(driver, exclude_name=None, as_of_date=None):
	"""Return the oldest overdue uncleared Cash invoice for the driver, or None."""
	payment_days = frappe.utils.cint(
		frappe.db.get_value("Driver", driver, "custom_payment_days") or 1
	)

	conditions = """
		SELECT name, posting_date, outstanding_amount
		FROM `tabSales Invoice`
		WHERE custom_driver = %s
		  AND docstatus = 1
		  AND outstanding_amount > 0
		  AND DATEDIFF(%s, posting_date) > %s
	"""
	params = [driver, as_of_date or today(), payment_days]

	if exclude_name:
		conditions += " AND name != %s"
		params.append(exclude_name)

	conditions += " ORDER BY posting_date ASC LIMIT 1"

	rows = frappe.db.sql(conditions, params, as_dict=True)
	return rows[0] if rows else None


def _driver_uncollected_total(driver, exclude_name=None) -> float:
	"""Sum of outstanding_amount across every submitted, still-unpaid Cash invoice this driver is
	holding -- the running total validate_driver_cash_limit caps, distinct from the
	single-overdue-invoice check validate_driver_payment already does above."""
	conditions = """
		SELECT COALESCE(SUM(outstanding_amount), 0) AS total
		FROM `tabSales Invoice`
		WHERE custom_driver = %s
		  AND docstatus = 1
		  AND outstanding_amount > 0
	"""
	params = [driver]

	if exclude_name:
		conditions += " AND name != %s"
		params.append(exclude_name)

	rows = frappe.db.sql(conditions, params, as_dict=True)
	return frappe.utils.flt(rows[0].total) if rows else 0.0


def _driver_branch_sub_limit(driver, branch) -> float:
	"""This driver's own Branch Cash Limit row for `branch`, or 0 if that branch carries no
	sub-limit of its own -- uncapped at the branch level, still bound by the driver's overall
	Cash Collection Limit."""
	if not (driver and branch):
		return 0
	return frappe.utils.flt(
		frappe.db.get_value("Driver Branch Cash Limit", {"parent": driver, "branch": branch}, "cash_limit")
	)


def _driver_branch_uncollected_total(driver, branch, exclude_name=None) -> float:
	"""Sum of outstanding_amount across this driver's submitted, still-unpaid Cash invoices
	raised specifically at this branch -- the same shape as _driver_uncollected_total, narrowed
	by the `branch` accounting dimension."""
	conditions = """
		SELECT COALESCE(SUM(outstanding_amount), 0) AS total
		FROM `tabSales Invoice`
		WHERE custom_driver = %s
		  AND branch = %s
		  AND docstatus = 1
		  AND outstanding_amount > 0
	"""
	params = [driver, branch]

	if exclude_name:
		conditions += " AND name != %s"
		params.append(exclude_name)

	rows = frappe.db.sql(conditions, params, as_dict=True)
	return frappe.utils.flt(rows[0].total) if rows else 0.0


def _throw_driver_cash_limit_exceeded(doc, driver, existing, limit, branch=None):
	driver_name = frappe.db.get_value("Driver", driver, "full_name") or driver
	scope = (_("at Branch %s") % branch) if branch else _("overall")
	frappe.throw(
		_(
			"Delivery Person %s is already holding %s in uncollected cash %s, at or over their "
			"Cash Limit of %s. Please collect and record payment before handing over more."
		)
		% (
			frappe.bold(driver_name),
			frappe.utils.fmt_money(existing, currency=doc.get("currency")),
			scope,
			frappe.utils.fmt_money(limit, currency=doc.get("currency")),
		),
		title=_("Delivery Person Cash Limit Exceeded"),
	)


def validate_driver_cash_limit(doc, _method=None):
	"""Block a new Cash+Driver document once this driver's own running uncollected-cash total is
	already at or past their Cash Collection Limit (Driver.custom_cash_limit) -- 0 means uncapped,
	only the days rule (validate_driver_payment, above) applies (GS Issue 19: "amount limits ...
	delivery-person-wise (only days can be set today)"). On top of that overall check, also
	blocks once the driver's total AT THIS DOCUMENT'S OWN BRANCH is at or past that branch's own
	Cash Limit sub-allocation (Driver.custom_branch_cash_limits), when that branch carries one --
	same two-layer shape as sf_trading.credit_limit's company-wide + branch-wise Customer check.

	Reads only EXISTING submitted invoices, the same "is there already a problem" shape
	validate_driver_payment uses -- not this document's own prospective amount, which a Sales
	Order cannot supply (it has no outstanding_amount) and a brand-new Sales Invoice hasn't been
	billed against yet either. Hooked at the same two points as validate_driver_payment, for the
	same "an order has no is_return" reason.
	"""
	if doc.get("is_return"):
		return
	if doc.get("custom_payment_mode") == "Credit":
		return
	if not doc.get("custom_driver"):
		return

	driver = doc.custom_driver
	exclude_name = doc.name if doc.doctype == "Sales Invoice" else None

	overall_limit = frappe.utils.flt(frappe.db.get_value("Driver", driver, "custom_cash_limit"))
	if overall_limit:
		existing = _driver_uncollected_total(driver, exclude_name)
		if existing >= overall_limit:
			_throw_driver_cash_limit_exceeded(doc, driver, existing, overall_limit)

	branch = doc.get("branch")
	if branch:
		branch_limit = _driver_branch_sub_limit(driver, branch)
		if branch_limit:
			branch_existing = _driver_branch_uncollected_total(driver, branch, exclude_name)
			if branch_existing >= branch_limit:
				_throw_driver_cash_limit_exceeded(doc, driver, branch_existing, branch_limit, branch=branch)


def _sum_positive_driver_branch_cash_limits(doc) -> float:
	return sum(
		frappe.utils.flt(row.get("cash_limit"))
		for row in (doc.custom_branch_cash_limits or [])
		if frappe.utils.flt(row.get("cash_limit")) > 0
	)


def validate_driver_branch_cash_limit_allocation(doc, _method=None):
	"""Driver validate: the branches that DO carry their own Cash Limit sub-allocation may not
	collectively promise more than this driver's own overall Cash Collection Limit -- same shape
	as sf_trading.customer_permission.validate_branch_credit_limit_allocation."""
	allocated = _sum_positive_driver_branch_cash_limits(doc)
	if allocated <= 0:
		return

	total = frappe.utils.flt(doc.get("custom_cash_limit"))
	if allocated > total:
		frappe.throw(
			_(
				"Branch Cash Limits add up to %s, more than this delivery person's own overall "
				"Cash Collection Limit of %s. Lower a branch's Cash Limit, or raise the Cash "
				"Collection Limit above."
			)
			% (frappe.utils.fmt_money(allocated), frappe.utils.fmt_money(total)),
			title=_("Branch Cash Limits Exceed Total"),
		)


@frappe.whitelist()
def check_driver_payment_overdue(driver):
	"""Client-side check: return the oldest overdue driver invoice dict or None."""
	if not driver:
		return None
	return _get_driver_overdue_invoice(driver)


@frappe.whitelist()
def check_customer_credit_overdue(customer, company):
	"""Client-side check: return the oldest overdue invoice dict or None."""
	return _get_overdue_invoice(customer, company)


@frappe.whitelist()
def get_credit_limit_status(customer, company):
	"""Client-side check: customer's credit limit and current outstanding for a company.
	Used by the real-time credit-limit popup while items are being added on Sales Invoice."""
	if not customer or not company:
		return {"credit_limit": 0, "outstanding": 0, "currency": None}

	from erpnext.selling.doctype.customer.customer import get_credit_limit, get_customer_outstanding
	from frappe.utils import flt

	credit_limit = flt(get_credit_limit(customer, company))
	outstanding = flt(get_customer_outstanding(customer, company)) if credit_limit else 0
	return {
		"credit_limit": credit_limit,
		"outstanding": outstanding,
		"currency": frappe.get_cached_value("Company", company, "default_currency"),
	}
