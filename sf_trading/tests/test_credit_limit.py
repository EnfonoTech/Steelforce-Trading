"""Tests for the branch-wise Credit Limit sub-allocation enforcement (GS Issue 19).

    bench --site <scratch-site> run-tests --module sf_trading.tests.test_credit_limit
"""

from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase

from sf_trading import credit_limit as cl

BRANCH_SUB_LIMIT = "sf_trading.credit_limit.branch_sub_limit"
BRANCH_EXPOSURE = "sf_trading.credit_limit.branch_credit_exposure"


def _si(**fields):
	doc = frappe._dict(
		doctype="Sales Invoice",
		name="SI-0001",
		customer="CUST-0001",
		customer_name="Acme",
		branch="Branch A",
		outstanding_amount=0,
		custom_payment_mode="Credit",
	)
	doc.update(fields)
	return doc


def _so(**fields):
	doc = frappe._dict(
		doctype="Sales Order",
		name="SO-0001",
		customer="CUST-0001",
		customer_name="Acme",
		branch="Branch A",
		base_grand_total=0,
		per_billed=0,
		custom_payment_mode="Credit",
	)
	doc.update(fields)
	return doc


class TestCheckBranchCreditLimit(FrappeTestCase):
	def test_cash_is_exempt_before_any_lookup(self):
		doc = _si(custom_payment_mode="Cash", outstanding_amount=999999)
		with patch(BRANCH_SUB_LIMIT) as sub_limit:
			cl.check_branch_credit_limit(doc)
		sub_limit.assert_not_called()

	def test_no_branch_on_doc_is_skipped(self):
		doc = _si(branch=None)
		with patch(BRANCH_SUB_LIMIT) as sub_limit:
			cl.check_branch_credit_limit(doc)
		sub_limit.assert_not_called()

	def test_no_customer_on_doc_is_skipped(self):
		doc = _si(customer=None)
		with patch(BRANCH_SUB_LIMIT) as sub_limit:
			cl.check_branch_credit_limit(doc)
		sub_limit.assert_not_called()

	def test_branch_with_no_sub_limit_is_uncapped_here(self):
		doc = _si(outstanding_amount=999999)
		with patch(BRANCH_SUB_LIMIT, return_value=0):
			with patch(BRANCH_EXPOSURE) as exposure:
				cl.check_branch_credit_limit(doc)
		exposure.assert_not_called()

	def test_sales_invoice_within_limit_passes(self):
		doc = _si(outstanding_amount=3000)
		with patch(BRANCH_SUB_LIMIT, return_value=5000):
			with patch(BRANCH_EXPOSURE, return_value=1000):
				cl.check_branch_credit_limit(doc)  # 1000 existing + 3000 this doc = 4000 <= 5000

	def test_sales_invoice_exactly_at_limit_passes(self):
		doc = _si(outstanding_amount=4000)
		with patch(BRANCH_SUB_LIMIT, return_value=5000):
			with patch(BRANCH_EXPOSURE, return_value=1000):
				cl.check_branch_credit_limit(doc)  # 1000 + 4000 = 5000, boundary passes

	def test_sales_invoice_over_limit_is_refused(self):
		doc = _si(outstanding_amount=4001)
		with patch(BRANCH_SUB_LIMIT, return_value=5000):
			with patch(BRANCH_EXPOSURE, return_value=1000):
				with self.assertRaises(frappe.ValidationError):
					cl.check_branch_credit_limit(doc)  # 1000 + 4001 = 5001 > 5000

	def test_sales_order_uses_grand_total_and_per_billed(self):
		doc = _so(base_grand_total=1000, per_billed=50)  # unbilled half = 500
		with patch(BRANCH_SUB_LIMIT, return_value=1000):
			with patch(BRANCH_EXPOSURE, return_value=600) as exposure:
				with self.assertRaises(frappe.ValidationError):
					cl.check_branch_credit_limit(doc)  # 600 + 500 = 1100 > 1000
		exposure.assert_called_once_with("CUST-0001", "Branch A", exclude="SO-0001")

	def test_sales_order_fully_unbilled_within_limit_passes(self):
		doc = _so(base_grand_total=500, per_billed=0)
		with patch(BRANCH_SUB_LIMIT, return_value=1000):
			with patch(BRANCH_EXPOSURE, return_value=400):
				cl.check_branch_credit_limit(doc)  # 400 + 500 = 900 <= 1000

	def test_self_is_always_excluded_from_existing_exposure(self):
		doc = _si(name="SI-0042", outstanding_amount=100)
		with patch(BRANCH_SUB_LIMIT, return_value=5000):
			with patch(BRANCH_EXPOSURE, return_value=0) as exposure:
				cl.check_branch_credit_limit(doc)
		exposure.assert_called_once_with("CUST-0001", "Branch A", exclude="SI-0042")


class TestBranchSubLimit(FrappeTestCase):
	def test_no_customer_or_branch_returns_zero_without_a_query(self):
		self.assertEqual(cl.branch_sub_limit(None, "Branch A"), 0)
		self.assertEqual(cl.branch_sub_limit("CUST-0001", None), 0)
