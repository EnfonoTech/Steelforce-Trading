"""Tests for the Delivery Person cash-collection amount cap (GS Issue 19).

    bench --site <scratch-site> run-tests --module sf_trading.tests.test_sales_invoice_override
"""

from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase

from sf_trading.api import sales_invoice_override as sio

GET_VALUE = "sf_trading.api.sales_invoice_override.frappe.db.get_value"
UNCOLLECTED_TOTAL = "sf_trading.api.sales_invoice_override._driver_uncollected_total"
BRANCH_SUB_LIMIT = "sf_trading.api.sales_invoice_override._driver_branch_sub_limit"
BRANCH_UNCOLLECTED_TOTAL = "sf_trading.api.sales_invoice_override._driver_branch_uncollected_total"


class StubDoc:
	def __init__(self, doctype, **fields):
		self.doctype = doctype
		self.name = fields.pop("name", None)
		self.__dict__.update(fields)

	def get(self, key, default=None):
		return self.__dict__.get(key, default)


def sales_invoice(**overrides):
	fields = {
		"name": "ACC-SINV-2026-00099",
		"customer": "CUST-0001",
		"custom_driver": "DRV-0001",
		"custom_payment_mode": "Cash",
		"currency": "BHD",
		"is_return": 0,
	}
	fields.update(overrides)
	return StubDoc("Sales Invoice", **fields)


def sales_order(**overrides):
	fields = {
		"name": "SAL-ORD-2026-00099",
		"customer": "CUST-0001",
		"custom_driver": "DRV-0001",
		"custom_payment_mode": "Cash",
		"currency": "BHD",
	}
	fields.update(overrides)
	return StubDoc("Sales Order", **fields)


class TestValidateDriverCashLimit(FrappeTestCase):
	def test_a_return_is_never_checked(self):
		with patch(GET_VALUE) as get_value:
			sio.validate_driver_cash_limit(sales_invoice(is_return=1))
		get_value.assert_not_called()

	def test_a_credit_document_is_never_checked(self):
		with patch(GET_VALUE) as get_value:
			sio.validate_driver_cash_limit(sales_invoice(custom_payment_mode="Credit"))
		get_value.assert_not_called()

	def test_a_document_with_no_driver_is_never_checked(self):
		with patch(GET_VALUE) as get_value:
			sio.validate_driver_cash_limit(sales_invoice(custom_driver=None))
		get_value.assert_not_called()

	def test_a_driver_with_no_cash_limit_is_uncapped(self):
		with patch(GET_VALUE, return_value=0):
			with patch(UNCOLLECTED_TOTAL) as total:
				sio.validate_driver_cash_limit(sales_invoice())
		total.assert_not_called()

	def test_below_the_limit_passes(self):
		with patch(GET_VALUE, return_value=500):
			with patch(UNCOLLECTED_TOTAL, return_value=300):
				sio.validate_driver_cash_limit(sales_invoice())  # must not raise

	def test_exactly_at_the_limit_is_refused(self):
		"""Same >= shape as the account's own PENDING_SO_CAP -- "already at or over" blocks
		piling on more, it does not wait for the limit to be strictly exceeded."""
		with patch(GET_VALUE, return_value=500):
			with patch(UNCOLLECTED_TOTAL, return_value=500):
				with self.assertRaises(frappe.ValidationError):
					sio.validate_driver_cash_limit(sales_invoice())

	def test_over_the_limit_is_refused(self):
		with patch(GET_VALUE, return_value=500):
			with patch(UNCOLLECTED_TOTAL, return_value=800):
				with self.assertRaises(frappe.ValidationError):
					sio.validate_driver_cash_limit(sales_invoice())

	def test_sales_invoice_excludes_itself_from_the_existing_total(self):
		with patch(GET_VALUE, return_value=500):
			with patch(UNCOLLECTED_TOTAL, return_value=100) as total:
				sio.validate_driver_cash_limit(sales_invoice(name="ACC-SINV-2026-00042"))
		total.assert_called_once_with("DRV-0001", "ACC-SINV-2026-00042")

	def test_sales_order_excludes_nothing_since_it_has_no_invoice_footprint(self):
		with patch(GET_VALUE, return_value=500):
			with patch(UNCOLLECTED_TOTAL, return_value=100) as total:
				sio.validate_driver_cash_limit(sales_order())
		total.assert_called_once_with("DRV-0001", None)


class TestValidateDriverCashLimitBranchScope(FrappeTestCase):
	"""The overall check above and this branch-scoped one are independent layers -- a document
	can pass one and fail the other, same two-layer shape as the Customer branch credit limit."""

	def test_no_branch_on_the_document_skips_the_branch_check_entirely(self):
		with patch(GET_VALUE, return_value=0):  # no overall limit either
			with patch(BRANCH_SUB_LIMIT) as sub_limit:
				sio.validate_driver_cash_limit(sales_invoice(branch=None))
		sub_limit.assert_not_called()

	def test_branch_with_no_sub_limit_is_uncapped_there(self):
		with patch(GET_VALUE, return_value=0):  # no overall limit
			with patch(BRANCH_SUB_LIMIT, return_value=0):
				with patch(BRANCH_UNCOLLECTED_TOTAL) as total:
					sio.validate_driver_cash_limit(sales_invoice(branch="Branch A"))
		total.assert_not_called()

	def test_branch_at_or_over_its_own_sub_limit_is_refused_even_with_no_overall_limit(self):
		with patch(GET_VALUE, return_value=0):  # no overall limit set at all
			with patch(BRANCH_SUB_LIMIT, return_value=200):
				with patch(BRANCH_UNCOLLECTED_TOTAL, return_value=200):
					with self.assertRaises(frappe.ValidationError):
						sio.validate_driver_cash_limit(sales_invoice(branch="Branch A"))

	def test_branch_within_its_sub_limit_passes_even_when_overall_is_also_fine(self):
		with patch(GET_VALUE, return_value=1000):
			with patch(UNCOLLECTED_TOTAL, return_value=100):
				with patch(BRANCH_SUB_LIMIT, return_value=200):
					with patch(BRANCH_UNCOLLECTED_TOTAL, return_value=50):
						sio.validate_driver_cash_limit(sales_invoice(branch="Branch A"))  # must not raise

	def test_branch_scoped_query_excludes_this_document_and_narrows_by_branch(self):
		with patch(GET_VALUE, return_value=0):
			with patch(BRANCH_SUB_LIMIT, return_value=200):
				with patch(BRANCH_UNCOLLECTED_TOTAL, return_value=0) as total:
					sio.validate_driver_cash_limit(sales_invoice(branch="Branch A", name="ACC-SINV-2026-00077"))
		total.assert_called_once_with("DRV-0001", "Branch A", "ACC-SINV-2026-00077")


class TestDriverUncollectedTotal(FrappeTestCase):
	def test_sums_outstanding_amount_for_the_driver(self):
		with patch("sf_trading.api.sales_invoice_override.frappe.db.sql", return_value=[{"total": 1234.5}]) as sql:
			total = sio._driver_uncollected_total("DRV-0001")
		self.assertEqual(total, 1234.5)
		args, _kwargs = sql.call_args
		self.assertEqual(args[1], ["DRV-0001"])

	def test_exclude_name_is_appended_as_a_query_param(self):
		with patch("sf_trading.api.sales_invoice_override.frappe.db.sql", return_value=[{"total": 0}]) as sql:
			sio._driver_uncollected_total("DRV-0001", exclude_name="ACC-SINV-2026-00042")
		args, _kwargs = sql.call_args
		self.assertEqual(args[1], ["DRV-0001", "ACC-SINV-2026-00042"])


class TestDriverBranchSubLimit(FrappeTestCase):
	def test_no_driver_or_branch_returns_zero_without_a_query(self):
		self.assertEqual(sio._driver_branch_sub_limit(None, "Branch A"), 0)
		self.assertEqual(sio._driver_branch_sub_limit("DRV-0001", None), 0)


class TestDriverBranchUncollectedTotal(FrappeTestCase):
	def test_sums_outstanding_amount_for_the_driver_at_that_branch(self):
		with patch("sf_trading.api.sales_invoice_override.frappe.db.sql", return_value=[{"total": 250.0}]) as sql:
			total = sio._driver_branch_uncollected_total("DRV-0001", "Branch A")
		self.assertEqual(total, 250.0)
		args, _kwargs = sql.call_args
		self.assertEqual(args[1], ["DRV-0001", "Branch A"])


class DriverStub:
	def __init__(self, **fields):
		self.doctype = "Driver"
		self.__dict__.update(fields)

	def get(self, key, default=None):
		return self.__dict__.get(key, default)


class TestValidateDriverBranchCashLimitAllocation(FrappeTestCase):
	"""Same shape as TestValidateBranchCreditLimitAllocation for Customer."""

	def test_no_branch_rows_at_all_is_fine(self):
		doc = DriverStub(custom_cash_limit=500)
		sio.validate_driver_branch_cash_limit_allocation(doc)

	def test_zero_branch_limits_are_excluded_from_the_sum(self):
		doc = DriverStub(
			custom_cash_limit=500,
			custom_branch_cash_limits=[{"branch": "Branch A", "cash_limit": 0}],
		)
		sio.validate_driver_branch_cash_limit_allocation(doc)

	def test_allocation_within_the_overall_limit_passes(self):
		doc = DriverStub(
			custom_cash_limit=1000,
			custom_branch_cash_limits=[
				{"branch": "Branch A", "cash_limit": 400},
				{"branch": "Branch B", "cash_limit": 600},
			],
		)
		sio.validate_driver_branch_cash_limit_allocation(doc)

	def test_allocation_over_the_overall_limit_is_refused(self):
		doc = DriverStub(
			custom_cash_limit=800,
			custom_branch_cash_limits=[
				{"branch": "Branch A", "cash_limit": 500},
				{"branch": "Branch B", "cash_limit": 500},
			],
		)
		with self.assertRaises(frappe.ValidationError):
			sio.validate_driver_branch_cash_limit_allocation(doc)

	def test_allocation_with_no_overall_limit_at_all_is_refused(self):
		doc = DriverStub(
			custom_cash_limit=0,
			custom_branch_cash_limits=[{"branch": "Branch A", "cash_limit": 100}],
		)
		with self.assertRaises(frappe.ValidationError):
			sio.validate_driver_branch_cash_limit_allocation(doc)
