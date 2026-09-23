"""Tests for the bulk Item-Default company assignment (GS Issue 31).

    bench --site <scratch-site> run-tests --module sf_trading.tests.test_bulk_item_company_assign
"""

from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase

from sf_trading.api import bulk_item_company_assign as bica

GET_ALL = "sf_trading.api.bulk_item_company_assign.frappe.get_all"
ENQUEUE = "sf_trading.api.bulk_item_company_assign.frappe.enqueue"
HAS_PERMISSION = "sf_trading.api.bulk_item_company_assign.frappe.has_permission"
COMPANY_EXISTS = "sf_trading.api.bulk_item_company_assign.frappe.db.exists"


class TestItemsMissingCompany(FrappeTestCase):
	def test_empty_input_needs_no_query(self):
		with patch(GET_ALL) as get_all:
			self.assertEqual(bica.items_missing_company([], "Steel Force Trading WLL"), [])
		get_all.assert_not_called()

	def test_items_already_assigned_are_excluded(self):
		with patch(GET_ALL, return_value=["ITEM-0001"]):
			missing = bica.items_missing_company(["ITEM-0001", "ITEM-0002"], "Steel Force Trading WLL")
		self.assertEqual(missing, ["ITEM-0002"])


class TestBulkAssignCompany(FrappeTestCase):
	def test_refuses_with_no_items(self):
		with patch(HAS_PERMISSION, return_value=True):
			with self.assertRaises(frappe.ValidationError):
				bica.bulk_assign_company([], "Steel Force Trading WLL")

	def test_refuses_an_unknown_company(self):
		with patch(HAS_PERMISSION, return_value=True):
			with patch(COMPANY_EXISTS, return_value=False):
				with self.assertRaises(frappe.ValidationError):
					bica.bulk_assign_company(["ITEM-0001"], "Not A Real Company")

	def test_a_valid_request_is_queued_not_run_inline(self):
		"""16,000 items in one HTTP request would time out -- this must enqueue, never loop
		inline (see the account's own hard rule on anything past ~30 seconds)."""
		with patch(HAS_PERMISSION, return_value=True):
			with patch(COMPANY_EXISTS, return_value=True):
				with patch(ENQUEUE, return_value=frappe._dict(id="job-1")) as enqueue:
					result = bica.bulk_assign_company(["ITEM-0001", "ITEM-0002"], "Steel Force Trading WLL")
		enqueue.assert_called_once()
		_, kwargs = enqueue.call_args
		self.assertEqual(kwargs["queue"], "long")
		self.assertTrue(result["queued"])
		self.assertEqual(result["item_count"], 2)
