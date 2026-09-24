"""Tests for the Branch Credit Limit sub-allocation validation (GS Issue 19).

    bench --site <scratch-site> run-tests --module sf_trading.tests.test_customer_permission
"""

import frappe
from frappe.tests.utils import FrappeTestCase

from sf_trading import customer_permission as cp


def _doc(credit_limits=None, branch_access=None):
	return frappe._dict(
		credit_limits=[frappe._dict(r) for r in (credit_limits or [])],
		custom_branch_access=[frappe._dict(r) for r in (branch_access or [])],
	)


class TestValidateBranchCreditLimitAllocation(FrappeTestCase):
	def test_no_branch_rows_at_all_is_fine(self):
		doc = _doc(credit_limits=[{"credit_limit": 5000}])
		cp.validate_branch_credit_limit_allocation(doc)

	def test_branch_rows_with_no_sub_limit_are_fine(self):
		doc = _doc(credit_limits=[{"credit_limit": 5000}], branch_access=[{"branch": "Branch A"}])
		cp.validate_branch_credit_limit_allocation(doc)

	def test_zero_branch_limits_are_excluded_from_the_sum(self):
		doc = _doc(
			credit_limits=[{"credit_limit": 5000}],
			branch_access=[
				{"branch": "Branch A", "credit_limit": 0},
				{"branch": "Branch B", "credit_limit": 0},
			],
		)
		cp.validate_branch_credit_limit_allocation(doc)

	def test_allocation_within_total_passes(self):
		doc = _doc(
			credit_limits=[{"credit_limit": 10000}],
			branch_access=[
				{"branch": "Branch A", "credit_limit": 4000},
				{"branch": "Branch B", "credit_limit": 6000},
			],
		)
		cp.validate_branch_credit_limit_allocation(doc)

	def test_allocation_exactly_at_total_passes(self):
		doc = _doc(
			credit_limits=[{"credit_limit": 10000}],
			branch_access=[
				{"branch": "Branch A", "credit_limit": 5000},
				{"branch": "Branch B", "credit_limit": 5000},
			],
		)
		cp.validate_branch_credit_limit_allocation(doc)

	def test_allocation_over_total_is_refused(self):
		"""GS Issue 19's own real example: 5,000 at one branch + 5,000 at another, but the
		customer's own company-wide Credit Limit is only 8,000."""
		doc = _doc(
			credit_limits=[{"credit_limit": 8000}],
			branch_access=[
				{"branch": "Branch A", "credit_limit": 5000},
				{"branch": "Branch B", "credit_limit": 5000},
			],
		)
		with self.assertRaises(frappe.ValidationError):
			cp.validate_branch_credit_limit_allocation(doc)

	def test_allocation_with_no_company_credit_limit_at_all_is_refused(self):
		doc = _doc(credit_limits=[], branch_access=[{"branch": "Branch A", "credit_limit": 1000}])
		with self.assertRaises(frappe.ValidationError):
			cp.validate_branch_credit_limit_allocation(doc)
