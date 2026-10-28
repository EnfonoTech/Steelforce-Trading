# sf_trading/bonus_eligibility.py
"""Bonus Eligibility: what each branch sold that earns a bonus, against its target, and the bonus it earns.

The client's sheet, line for line (amounts are company currency and net of VAT -- `base_net_total` -- as the
branch targets are):

  Bonus Eligibility Calculation                      Revenue Allowed For Bonus
  1. Cash sales                                      1. Cash sales
  2. Credit sales                                    2. Less: Cash sales return
     Less: credit sales not collected in N days     3. Less: Outdoor sales cash
  3. Less: Intercompany cash sales                   4. Add: cash collected on credit sales within N days
  4. Less: Intercompany credit sales                 5. Total revenue allowed for bonus (1 - 2 - 3 + 4)
  5. Less: Cash sales return
  6. Less: Credit sales return
  7. Net sales
  8. Less: Outdoor sales (one line per outdoor sales person, then the total)
  9. Eligible sales (7 - 8)

  Target achieved = eligible sales / the branch's Sales Target for the period (sales_target.py, prorated by
  the days of each month the period covers). 90% and above earns 0.25%, 80% to below 90% earns 0.15%, below
  80% nothing; the bonus is that share of the revenue allowed for bonus, 70% of it to the branch and 30% to
  operations.

How a sale is read:

* **Cash or credit** -- the invoice's Payment Mode (`custom_payment_mode`, the marker every rule here already
  uses): Credit is a credit sale; Cash and Cheque are cash. An invoice with none (the July import) is credit
  when its customer is a credit customer (a credit limit above zero, sales_order_governance) and cash
  otherwise. A return is read by its own mode, or failing that the invoice it returns.
* **Intercompany** -- the customer is an Internal Customer, or the invoice names an Inter Company Branch.
* **Outdoor** -- the invoice's Sales Person is one of the outdoor sales persons picked in the report.
* **Collected within N days** (the report's Collected Within (Days), 60 by default) -- payments
  and journals the Payment Ledger allocates to the credit invoice, dated no later than N days after it (and not
  after today), taken net of VAT in the invoice's own net / gross proportion and never above its net amount.

Outdoor sales are what the outdoor sales persons earned in the same terms as the branch -- cash sales plus
credit collected within N days, less their returns -- so a credit sale that was never collected is not taken
out twice. Intercompany sales are taken out in full (lines 3 and 4), so their collection is not counted again.
"""

from __future__ import annotations

import frappe
from frappe import _
from frappe.utils import add_days, cint, flt, getdate, nowdate

from sf_trading.sales_target import fiscal_year_bounds, month_coverage, targets

CREDIT = "Credit"
DEFAULT_COLLECTION_DAYS = 60
#: (achieved % at or above, bonus %), highest first
BONUS_SLABS = ((90, 0.25), (80, 0.15))
BRANCH_SHARE, OPERATIONS_SHARE = 70, 30


def bonus_percent(achieved: float) -> float:
	for floor, percent in BONUS_SLABS:
		if achieved >= floor:
			return percent
	return 0.0


def _invoices(company, from_date, to_date, branches):
	conditions = ["si.docstatus = 1", "si.company = %(company)s", "si.posting_date between %(from_date)s and %(to_date)s"]
	values = {"company": company, "from_date": from_date, "to_date": to_date}
	if branches:
		conditions.append("si.branch in %(branches)s")
		values["branches"] = tuple(branches)
	return frappe.db.sql(
		f"""
		select si.name, si.branch, si.posting_date, si.is_return, si.return_against, si.customer,
			si.custom_payment_mode as mode, si.custom_sales_person as sales_person,
			si.inter_company_branch, si.base_net_total as net, si.base_grand_total as gross,
			ifnull(c.is_internal_customer, 0) as internal_customer
		from `tabSales Invoice` si
		left join `tabCustomer` c on c.name = si.customer
		where {" and ".join(conditions)}
		""",
		values,
		as_dict=True,
	)


def _credit_customers(customers) -> set:
	if not customers:
		return set()
	return set(
		frappe.get_all(
			"Customer Credit Limit",
			filters={"parent": ["in", list(customers)], "parenttype": "Customer", "credit_limit": [">", 0]},
			pluck="parent",
		)
	)


def _return_modes(invoices) -> dict:
	originals = {inv.return_against for inv in invoices if inv.is_return and not inv.mode and inv.return_against}
	if not originals:
		return {}
	return dict(frappe.get_all("Sales Invoice", filters={"name": ["in", list(originals)]}, fields=["name", "custom_payment_mode"], as_list=True))


def _collected(invoices, days) -> dict:
	"""{invoice: amount collected within `days` of it, gross} from the Payment Ledger."""
	if not invoices:
		return {}
	today = getdate(nowdate())
	by_name = {inv.name: inv for inv in invoices}
	rows = frappe.db.sql(
		"""
		select against_voucher_no as invoice, posting_date, amount
		from `tabPayment Ledger Entry`
		where against_voucher_type = 'Sales Invoice' and against_voucher_no in %(names)s
			and voucher_no != against_voucher_no and voucher_type in ('Payment Entry', 'Journal Entry')
			and delinked = 0 and docstatus = 1
		""",
		{"names": tuple(by_name)},
		as_dict=True,
	)
	out = {}
	for row in rows:
		inv = by_name[row.invoice]
		deadline = min(add_days(inv.posting_date, days), today)
		if getdate(row.posting_date) <= deadline:
			# a receipt reduces the receivable: its ledger amount is negative
			out[row.invoice] = out.get(row.invoice, 0.0) - flt(row.amount)
	return out


def calculate(company: str, from_date, to_date, branches=None, outdoor=None, days=DEFAULT_COLLECTION_DAYS) -> dict:
	"""{branch: the sheet's lines} for the period, every branch with a sale or a target in it. `outdoor` is the
	sales persons whose sales are outdoor sales; `days` how soon a credit sale must be collected to count."""
	conf = frappe._dict(outdoor=set(outdoor or ()), days=DEFAULT_COLLECTION_DAYS if days in (None, "") else cint(days))
	invoices = _invoices(company, from_date, to_date, branches)
	credit_customers = _credit_customers({inv.customer for inv in invoices if not inv.mode})
	return_modes = _return_modes(invoices)

	def is_credit(inv):
		mode = inv.mode or (return_modes.get(inv.return_against) if inv.is_return else None)
		if mode:
			return mode == CREDIT
		return inv.customer in credit_customers

	credit_sales = [inv for inv in invoices if not inv.is_return and is_credit(inv)]
	collected = _collected(credit_sales, conf.days)
	# what was returned against each credit invoice of the period: the returned part is no longer owed, so it is
	# not "not collected" as well as "returned" (a fully returned, unpaid invoice would take its net out twice)
	credit_returned = {}
	for inv in invoices:
		if inv.is_return and inv.return_against and is_credit(inv):
			credit_returned[inv.return_against] = credit_returned.get(inv.return_against, 0.0) - flt(inv.net)

	def collected_net(inv):
		gross = flt(inv.gross)
		share = flt(inv.net) / gross if gross else 1
		return min(flt(collected.get(inv.name)) * share, flt(inv.net))

	branches_out = {}

	def line(branch):
		if branch not in branches_out:
			branches_out[branch] = frappe._dict(
				branch=branch, cash_sales=0.0, credit_sales=0.0, credit_not_collected=0.0, ic_cash=0.0, ic_credit=0.0,
				cash_returns=0.0, credit_returns=0.0, ic_returns=0.0, outdoor={}, outdoor_cash=0.0, credit_collected=0.0,
			)
		return branches_out[branch]

	for inv in invoices:
		b = line(inv.branch or _("Not Set"))
		credit = is_credit(inv)
		net = flt(inv.net)
		intercompany = bool(inv.internal_customer or inv.inter_company_branch)
		outdoor = inv.sales_person in conf.outdoor
		if inv.is_return:
			# a return carries its totals negative
			if credit:
				b.credit_returns += -net
			else:
				b.cash_returns += -net
			if intercompany:
				# its sale left Net Sales in full (lines 3 and 4), so the return has nothing left to take out
				b.ic_returns += -net
			if outdoor and not intercompany:
				b.outdoor[inv.sales_person] = b.outdoor.get(inv.sales_person, 0.0) + net
				if not credit:
					b.outdoor_cash += net
			continue
		if credit:
			b.credit_sales += net
			if intercompany:
				b.ic_credit += net
				continue
			got = collected_net(inv)
			b.credit_not_collected += max(net - got - credit_returned.get(inv.name, 0.0), 0.0)
			b.credit_collected += got
			if outdoor:
				b.outdoor[inv.sales_person] = b.outdoor.get(inv.sales_person, 0.0) + got
		else:
			b.cash_sales += net
			if intercompany:
				b.ic_cash += net
				continue
			if outdoor:
				b.outdoor[inv.sales_person] = b.outdoor.get(inv.sales_person, 0.0) + net
				b.outdoor_cash += net

	# the branch targets for the period, each month prorated by the days of it the period covers -- of every
	# fiscal year the period touches, so a period over a year end is not judged against one year's targets
	period_start, period_end = getdate(from_date), getdate(to_date)
	branch_targets = {}
	for fiscal_year in frappe.get_all(
		"Fiscal Year",
		filters={"disabled": 0, "year_start_date": ["<=", period_end], "year_end_date": [">=", period_start]},
		pluck="name",
	):
		start, end = fiscal_year_bounds(fiscal_year)
		coverage = month_coverage(fiscal_year, max(period_start, start), min(period_end, end))
		for (branch, month), amount in targets(company, fiscal_year, "Branch").items():
			if (branches and branch not in branches) or not coverage.get(month):
				continue
			branch_targets[branch] = branch_targets.get(branch, 0.0) + amount * coverage[month]
			line(branch)

	precision = cint(frappe.get_system_settings("currency_precision")) or 3
	for b in branches_out.values():
		b.net_sales = (
			b.cash_sales + b.credit_sales - b.credit_not_collected - b.ic_cash - b.ic_credit
			- b.cash_returns - b.credit_returns + b.ic_returns
		)
		b.outdoor_total = sum(b.outdoor.values())
		b.eligible_sales = b.net_sales - b.outdoor_total
		b.revenue_allowed = b.cash_sales - b.cash_returns - b.outdoor_cash + b.credit_collected
		b.target = branch_targets.get(b.branch, 0.0)
		b.achieved = b.eligible_sales / b.target * 100 if b.target else 0.0
		# 89.99999999999999 is the 90% the sheet shows: the slab is read on the percentage as it is displayed
		b.bonus_percent = bonus_percent(flt(b.achieved, 6)) if b.target else 0.0
		# the bonus and its two shares in the currency's precision, so the shares add up to the bonus as printed
		b.bonus = flt(b.revenue_allowed * b.bonus_percent / 100, precision)
		b.branch_share = flt(b.bonus * BRANCH_SHARE / 100, precision)
		b.operations_share = flt(b.bonus - b.branch_share, precision)
		b.collection_days = conf.days
	return branches_out
