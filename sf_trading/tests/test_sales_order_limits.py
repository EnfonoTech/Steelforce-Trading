# sf_trading/tests/test_sales_order_limits.py
"""Sales Order limits by Customer and by Salesperson (sf_trading/sales_order_limits.py).

The three reads (the party's figures, its oldest overdue Cash invoice, its uncollected cash) are patched, so nothing is created:

    bench --site <site> run-tests --module sf_trading.tests.test_sales_order_limits
"""

from datetime import date
from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase

from sf_trading import sales_order_limits as sol

LIMITS = "sf_trading.sales_order_limits.get_limits"
OVERDUE = "sf_trading.sales_order_limits.overdue_invoice"
UNCOLLECTED = "sf_trading.sales_order_limits.uncollected_cash"


def limits(days=0, cash=0.0, **branch):
	return frappe._dict(days=days, cash=cash, branch=dict(branch))


def order(**over):
	values = dict(
		doctype="Sales Order",
		name="SAL-ORD-1",
		custom_payment_mode="Cash",
		customer="CUST-1",
		custom_sales_person="Akhil",
		company="Steel Force Trading WLL",
		branch="SFSB",
		currency="BHD",
		transaction_date=date(2026, 10, 15),
	)
	values.update(over)
	return frappe._dict(values)


def figures(customer=None, salesperson=None):
	"""get_limits side effect: the figures of the Customer and of the Sales Person."""

	def get(party_type, party):
		return (customer if party_type == "Customer" else salesperson) or limits()

	return get


class TestSalesOrderLimits(FrappeTestCase):
	def run_check(self, doc, customer=None, salesperson=None, overdue=None, held=0.0):
		"""Returns (refused, overdue mock, uncollected mock)."""
		with patch(LIMITS, side_effect=figures(customer, salesperson)), patch(OVERDUE, return_value=overdue) as o, patch(
			UNCOLLECTED, side_effect=held if callable(held) else (lambda *a, **k: held)
		) as u:
			try:
				sol.validate_sales_order_limits(doc)
				return False, o, u
			except frappe.ValidationError:
				return True, o, u

	def test_a_credit_order_is_not_checked_at_all(self):
		refused, o, u = self.run_check(order(custom_payment_mode="Credit"), customer=limits(days=1, cash=1), held=999)
		self.assertFalse(refused)
		o.assert_not_called()
		u.assert_not_called()

	def test_any_other_mode_is_checked_like_the_driver(self):
		refused, _, _ = self.run_check(order(custom_payment_mode="Cheque"), customer=limits(cash=100), held=100)
		self.assertTrue(refused)

	def test_no_party_means_nothing_to_check(self):
		refused, o, u = self.run_check(order(customer=None, custom_sales_person=None), customer=limits(cash=1), held=999)
		self.assertFalse(refused)
		o.assert_not_called()
		u.assert_not_called()

	def test_a_party_with_no_figure_costs_no_query(self):
		refused, o, u = self.run_check(order(), held=999)
		self.assertFalse(refused)
		o.assert_not_called()
		u.assert_not_called()

	def test_an_overdue_cash_invoice_refuses_the_order(self):
		invoice = frappe._dict(name="SINV-9", posting_date=date(2026, 9, 1), outstanding_amount=12.5)
		refused, o, _ = self.run_check(order(), customer=limits(days=7), overdue=invoice)
		self.assertTrue(refused)
		o.assert_called_once_with("Customer", "CUST-1", "Steel Force Trading WLL", date(2026, 10, 15), 7)

	def test_no_overdue_invoice_passes_the_day_limit(self):
		refused, _, u = self.run_check(order(), customer=limits(days=7), overdue=None)
		self.assertFalse(refused)
		u.assert_not_called()

	def test_cash_limit_is_at_or_over(self):
		self.assertTrue(self.run_check(order(), customer=limits(cash=100), held=100)[0])
		self.assertTrue(self.run_check(order(), customer=limits(cash=100), held=140)[0])
		self.assertFalse(self.run_check(order(), customer=limits(cash=100), held=99.999)[0])

	def test_the_salesperson_has_a_limit_of_his_own(self):
		def held(party_type, party, company, branch=None):
			return 500.0 if party_type == "Sales Person" else 0.0

		refused, _, _ = self.run_check(order(), customer=limits(cash=100), salesperson=limits(cash=400), held=held)
		self.assertTrue(refused)

	def test_either_party_refuses_and_neither_set_does_not(self):
		def held(party_type, party, company, branch=None):
			return 500.0 if party_type == "Customer" else 0.0

		self.assertTrue(self.run_check(order(), customer=limits(cash=400), salesperson=limits(cash=400), held=held)[0])
		self.assertFalse(self.run_check(order(), customer=limits(cash=900), salesperson=limits(cash=900), held=held)[0])

	def test_a_branch_cap_refuses_even_with_no_overall_limit(self):
		def held(party_type, party, company, branch=None):
			return 80.0 if branch == "SFSB" else 1.0

		refused, _, u = self.run_check(order(), customer=limits(SFSB=75.0), held=held)
		self.assertTrue(refused)
		u.assert_called_with("Customer", "CUST-1", "Steel Force Trading WLL", "SFSB")

	def test_a_branch_without_a_cap_is_uncapped_there(self):
		refused, _, _ = self.run_check(order(branch="SFSS"), customer=limits(SFSB=75.0), held=999.0)
		self.assertFalse(refused)

	def test_an_order_with_no_branch_skips_the_branch_check(self):
		refused, _, u = self.run_check(order(branch=None), customer=limits(SFSB=75.0), held=999.0)
		self.assertFalse(refused)
		u.assert_not_called()

	def test_a_branch_within_its_cap_passes_when_the_overall_is_fine_too(self):
		refused, _, _ = self.run_check(order(), customer=limits(cash=500, SFSB=75.0), held=10.0)
		self.assertFalse(refused)


class AllocationStub(frappe._dict):
	pass


def party(cash_limit, *rows):
	return frappe._dict(
		custom_cash_limit=cash_limit,
		custom_branch_cash_limits=[frappe._dict(idx=i, branch=b, cash_limit=c) for i, (b, c) in enumerate(rows, start=1)],
	)


class TestBranchAllocation(FrappeTestCase):
	def refused(self, doc):
		try:
			sol.validate_branch_cash_limit_allocation(doc)
			return False
		except frappe.ValidationError:
			return True

	def test_no_rows_is_fine(self):
		self.assertFalse(self.refused(party(0)))

	def test_a_branch_listed_twice_is_refused(self):
		self.assertTrue(self.refused(party(500, ("SFSB", 100), ("SFSB", 50))))

	def test_zero_branch_caps_are_not_counted(self):
		self.assertFalse(self.refused(party(0, ("SFSB", 0), ("SFSS", 0))))

	def test_caps_within_the_overall_limit_pass(self):
		self.assertFalse(self.refused(party(500, ("SFSB", 300), ("SFSS", 200))))

	def test_caps_over_the_overall_limit_are_refused(self):
		self.assertTrue(self.refused(party(500, ("SFSB", 300), ("SFSS", 201))))

	def test_a_branch_cap_with_no_overall_limit_is_refused(self):
		self.assertTrue(self.refused(party(0, ("SFSB", 50))))


class TestGetLimits(FrappeTestCase):
	def test_all_zero_and_no_read_while_the_fields_do_not_exist_yet(self):
		class NoFields:
			def has_field(self, name):
				return False

		with patch.object(sol.frappe, "get_meta", return_value=NoFields()), patch.object(sol.frappe.db, "get_value") as read:
			got = sol.get_limits("Customer", "CUST-1")
		self.assertEqual((got.days, got.cash, got.branch), (0, 0.0, {}))
		read.assert_not_called()
