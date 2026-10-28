"""Tests for the Bonus Eligibility sheet (bonus_eligibility.calculate): the lines that must not take a sale out twice.

    bench --site <scratch-site> run-tests --module sf_trading.tests.test_bonus_eligibility

Every database read is replaced, so the sheet's arithmetic is all that runs.
"""

from datetime import date
from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase

from sf_trading import bonus_eligibility as be

MONTHS = ("January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December")
BOUNDS = {"FY26": (date(2026, 1, 1), date(2026, 12, 31)), "FY25": (date(2025, 1, 1), date(2025, 12, 31))}


def invoice(name, ret=0, against=None, mode="Cash", net=1000.0, internal=0, branch="B1"):
	return frappe._dict(
		name=name, branch=branch, posting_date=date(2026, 3, 10), is_return=ret, return_against=against, customer="C1",
		mode=mode, sales_person=None, inter_company_branch=None, net=net, gross=net, internal_customer=internal,
	)


def sheet(invoices, collected=None, years=("FY26",), target_by_year=None):
	target_by_year = target_by_year or {"FY26": {("B1", "March"): 1000.0}}
	settings = {"currency_precision": 3, "rounding_method": "Banker's Rounding", "float_precision": 3}
	with patch.object(be, "_invoices", return_value=invoices), \
		patch.object(be, "_credit_customers", return_value=set()), \
		patch.object(be, "_return_modes", return_value={}), \
		patch.object(be, "_collected", return_value=collected or {}), \
		patch.object(be.frappe, "get_all", return_value=list(years)), \
		patch.object(be, "fiscal_year_bounds", side_effect=lambda fy: BOUNDS[fy]), \
		patch.object(be, "month_coverage", side_effect=lambda fy, start, end: {"March": 1} if fy == "FY26" else {"December": 1}), \
		patch.object(be, "targets", side_effect=lambda company, fy, dimension, **kw: target_by_year.get(fy, {})), \
		patch("frappe.get_system_settings", side_effect=lambda key, *a: settings.get(key)):
		return be.calculate("Co", "2026-03-01", "2026-03-31")["B1"]


class TestBonusEligibility(FrappeTestCase):
	def test_an_intercompany_return_is_not_taken_out_again(self):
		# the sale left Net Sales in full as an intercompany sale, so its return has nothing left to take out
		line = sheet([invoice("S1", internal=1), invoice("R1", ret=1, against="S1", net=-200.0, internal=1)])
		self.assertAlmostEqual(line.net_sales, 0.0)

	def test_a_fully_returned_credit_invoice_is_not_deducted_twice(self):
		line = sheet([invoice("S2", mode="Credit"), invoice("R2", ret=1, against="S2", mode="Credit", net=-1000.0)])
		self.assertAlmostEqual(line.credit_not_collected, 0.0)
		self.assertAlmostEqual(line.net_sales, 0.0)

	def test_a_part_returned_credit_invoice_counts_what_was_collected(self):
		# 1000 invoiced, 500 collected, 300 returned: 200 is still owed, and the sheet keeps the 500 collected
		line = sheet([invoice("S3", mode="Credit"), invoice("R3", ret=1, against="S3", mode="Credit", net=-300.0)], collected={"S3": 500.0})
		self.assertAlmostEqual(line.net_sales, 500.0)

	def test_an_uncollected_credit_invoice_is_still_not_a_sale(self):
		self.assertAlmostEqual(sheet([invoice("S4", mode="Credit")]).net_sales, 0.0)

	def test_a_cash_sale_is_a_sale(self):
		line = sheet([invoice("S5")])
		self.assertAlmostEqual(line.net_sales, 1000.0)
		self.assertAlmostEqual(line.revenue_allowed, 1000.0)

	def test_the_shares_add_up_to_the_bonus(self):
		line = sheet([invoice("S6", net=900.0)])
		self.assertEqual(line.bonus_percent, 0.25)
		self.assertAlmostEqual(line.branch_share + line.operations_share, line.bonus, places=6)

	def test_a_period_over_a_year_end_reads_both_years_targets(self):
		line = sheet(
			[invoice("S7")], years=("FY25", "FY26"),
			target_by_year={"FY25": {("B1", "December"): 600.0}, "FY26": {("B1", "March"): 400.0}},
		)
		self.assertAlmostEqual(line.target, 1000.0)
