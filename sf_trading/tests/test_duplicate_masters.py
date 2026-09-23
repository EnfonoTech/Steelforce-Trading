"""Tests for the duplicate-customer-by-CR finder (GS Issue 4's read-only half).

    bench --site <scratch-site> run-tests --module sf_trading.tests.test_duplicate_masters
"""

from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase

from sf_trading import duplicate_masters as dm
from sf_trading.party_completeness import CR_FIELD

GET_ALL = "sf_trading.duplicate_masters.frappe.get_all"


def _row(name, cr, customer_name=None, disabled=0):
	# frappe.get_all returns frappe._dict rows (attribute access) -- match that shape, not a
	# plain dict, since duplicate_customers_by_cr reads row.cr_number etc.
	return frappe._dict(
		{
			"name": name,
			"customer_name": customer_name or name,
			"cr_number": cr,
			CR_FIELD: cr,
			"disabled": disabled,
		}
	)


class TestDuplicateCustomersByCr(FrappeTestCase):
	def test_no_customers_returns_no_groups(self):
		with patch(GET_ALL, return_value=[]):
			self.assertEqual(dm.duplicate_customers_by_cr(), [])

	def test_a_cr_held_by_one_customer_is_not_a_duplicate(self):
		rows = [_row("CUST-0001", "CR-111")]
		with patch(GET_ALL, return_value=rows):
			self.assertEqual(dm.duplicate_customers_by_cr(), [])

	def test_a_cr_shared_by_two_customers_is_reported(self):
		rows = [_row("CUST-0001", "CR-111"), _row("CUST-0002", "CR-111")]
		with patch(GET_ALL, return_value=rows):
			groups = dm.duplicate_customers_by_cr()
		self.assertEqual(len(groups), 1)
		self.assertEqual(groups[0]["cr_number"], "CR-111")
		self.assertEqual(groups[0]["count"], 2)
		self.assertIn("CUST-0001", groups[0]["customers"])
		self.assertIn("CUST-0002", groups[0]["customers"])
		self.assertTrue(groups[0]["any_active"])

	def test_a_duplicate_where_every_side_is_disabled_is_flagged_not_active(self):
		rows = [_row("CUST-0001", "CR-111", disabled=1), _row("CUST-0002", "CR-111", disabled=1)]
		with patch(GET_ALL, return_value=rows):
			groups = dm.duplicate_customers_by_cr()
		self.assertFalse(groups[0]["any_active"])

	def test_blank_cr_is_never_grouped(self):
		rows = [_row("CUST-0001", ""), _row("CUST-0002", None)]
		with patch(GET_ALL, return_value=rows):
			self.assertEqual(dm.duplicate_customers_by_cr(), [])
