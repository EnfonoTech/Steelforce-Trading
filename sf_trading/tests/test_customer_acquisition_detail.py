# sf_trading/tests/test_customer_acquisition_detail.py
"""User-permission narrowing of the Customer Acquisition and Loyalty Detail report.

The report reads invoices through frappe.qb, which applies no permission filtering, so the report
narrows the shown rows itself. These cover that narrowing on plain values -- no documents needed.
New-versus-repeat is decided company-wide before any narrowing, which the report's own loop
guarantees by construction (see get_invoices).
"""

import frappe
from frappe.tests.utils import FrappeTestCase

from sf_trading.sf_trading.report.customer_acquisition_and_loyalty_detail import (
	customer_acquisition_and_loyalty_detail as report,
)


def invoice(**values):
	row = {"name": "SINV-1", "branch": "SFSB", "cost_center": "Main - SFB", "customer": "CUST-1"}
	row.update(values)
	return frappe._dict(row)


class TestCustomerAcquisitionDetailPermissions(FrappeTestCase):
	def test_no_restriction_shows_everything(self):
		self.assertTrue(report.is_permitted(invoice(), {}, {}))

	def test_restricted_branch_hides_another_branch(self):
		scope = {"Branch": {"SFSS"}}
		self.assertFalse(report.is_permitted(invoice(branch="SFSB"), scope, {}))
		self.assertTrue(report.is_permitted(invoice(branch="SFSS"), scope, {}))

	def test_a_blank_value_passes_like_frappe_does_under_a_user_permission(self):
		scope = {"Branch": {"SFSS"}, "Cost Center": {"Other - SFB"}}
		self.assertTrue(report.is_permitted(invoice(branch=None, cost_center=""), scope, {}))

	def test_a_hit_on_the_item_rows_counts(self):
		# branch is often on the item rows only; one permitted item row keeps the invoice visible
		scope = {"Branch": {"SFSS"}}
		item_values = {("SINV-1", "branch"): {"SFSS"}}
		self.assertTrue(report.is_permitted(invoice(branch="SFSB"), scope, item_values))

	def test_restricted_cost_center(self):
		scope = {"Cost Center": {"Main - SFB"}}
		self.assertTrue(report.is_permitted(invoice(cost_center="Main - SFB"), scope, {}))
		self.assertFalse(report.is_permitted(invoice(cost_center="Other - SFB"), scope, {}))

	def test_restricted_customer(self):
		scope = {"Customer": {"CUST-2"}}
		self.assertFalse(report.is_permitted(invoice(customer="CUST-1"), scope, {}))
		self.assertTrue(report.is_permitted(invoice(customer="CUST-2"), scope, {}))

	def test_every_restriction_must_hold(self):
		scope = {"Branch": {"SFSB"}, "Customer": {"CUST-2"}}
		self.assertFalse(report.is_permitted(invoice(branch="SFSB", customer="CUST-1"), scope, {}))
		self.assertTrue(report.is_permitted(invoice(branch="SFSB", customer="CUST-2"), scope, {}))

	def test_administrator_has_no_scope(self):
		self.assertEqual(report.get_permitted_scope("Administrator"), {})
