"""Tests for the Branch Credit Limit sub-allocation validation (GS Issue 19).

    bench --site <scratch-site> run-tests --module sf_trading.tests.test_customer_permission
"""

from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase

from sf_trading import customer_permission as cp

DB_SQL = "sf_trading.customer_permission.frappe.db.sql"


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


class TestCreditLimitGatesStatusOnlyExemption(FrappeTestCase):
	"""2026-09-26, same live bug as party_completeness's own status-only exemption (313
	Contracting): these two credit-limit gates can equally block an unrelated freeze/disable
	toggle on a customer whose credit-limit setup already violates one of them. ignore_validate on
	the initial insert -- the violation being tested for is exactly what THIS SAME insert would
	otherwise refuse; the fixture needs an already-existing violating record, the same way a
	migrated-data customer would arrive at this state without ever passing through this rule."""

	def _make_customer_with_credit_no_branch_access(self, name):
		leaf_group = frappe.db.get_value("Customer Group", {"is_group": 0}, "name")
		customer = frappe.get_doc(
			{
				"doctype": "Customer",
				"customer_name": name,
				"customer_type": "Company",
				"customer_group": leaf_group,
				"mobile_no": "33445566",
				"credit_limits": [{"credit_limit": 5000}],
			}
		)
		customer.flags.ignore_validate = True
		customer.insert(ignore_permissions=True)
		# Reset immediately -- it must not leak into the caller's own later .save() and silently
		# skip the very validation that save is meant to exercise.
		customer.flags.ignore_validate = False
		return customer

	def test_blocks_a_normal_edit_with_credit_set_and_no_branch_access(self):
		customer = self._make_customer_with_credit_no_branch_access("Test Credit No Branch Access")
		customer.reload()
		customer.website = "https://example.com"
		with self.assertRaises(frappe.ValidationError):
			customer.save(ignore_permissions=True)

	def test_allows_freezing_with_credit_set_and_no_branch_access(self):
		customer = self._make_customer_with_credit_no_branch_access("Test Credit No Branch Access Freeze")
		customer.reload()
		customer.is_frozen = 1
		customer.save(ignore_permissions=True)  # must not raise
		self.assertEqual(frappe.db.get_value("Customer", customer.name, "is_frozen"), 1)


class TestCustomerQueryCreditBranch(FrappeTestCase):
	"""A fresh Sales Invoice starts with Branch blank -- confirmed live, this used to zero out
	every credit customer from the search with no explanation. It must now widen instead."""

	def test_blank_branch_does_not_hard_zero_the_query(self):
		with patch(DB_SQL, return_value=[]) as sql:
			cp.customer_query_credit_branch("Customer", "acme", "name", 0, 20, {"company": "Steel Force Trading WLL"})
		query = sql.call_args[0][0]
		self.assertNotIn("1=0", query)

	def test_blank_branch_still_scopes_to_company_and_credit_customers(self):
		with patch(DB_SQL, return_value=[]) as sql:
			cp.customer_query_credit_branch("Customer", "acme", "name", 0, 20, {"company": "Steel Force Trading WLL"})
		query = sql.call_args[0][0]
		self.assertIn("Customer Credit Limit", query)
		self.assertIn("custom_company", query)

	def test_a_real_branch_narrows_to_that_branch(self):
		with patch(DB_SQL, return_value=[]) as sql:
			cp.customer_query_credit_branch("Customer", "acme", "name", 0, 20, {"branch": "Branch A"})
		query = sql.call_args[0][0]
		params = sql.call_args[0][1]
		self.assertIn("Customer Branch Access", query)
		self.assertEqual(params["branch"], "Branch A")
