"""Tests for the "N similar records found" search (GS Issue 15).

    bench --site <scratch-site> run-tests --module sf_trading.tests.test_duplicate_check
"""

from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase

from sf_trading.api import duplicate_check as dc

HAS_PERMISSION = "sf_trading.api.duplicate_check.frappe.has_permission"
GET_LIST = "sf_trading.api.duplicate_check.frappe.get_list"


class TestSimilarRecords(FrappeTestCase):
	def test_unconfigured_doctype_refuses(self):
		with self.assertRaises(frappe.ValidationError):
			dc.similar_records("Sales Invoice", "test")

	def test_short_input_returns_nothing_without_a_query(self):
		with patch(GET_LIST) as get_list:
			result = dc.similar_records("Customer", "ab")
		self.assertEqual(result, [])
		get_list.assert_not_called()

	def test_no_read_permission_returns_nothing(self):
		with patch(HAS_PERMISSION, return_value=False):
			with patch(GET_LIST) as get_list:
				result = dc.similar_records("Customer", "Al Test")
		self.assertEqual(result, [])
		get_list.assert_not_called()

	def test_a_real_search_uses_a_capped_prefix_query(self):
		with patch(HAS_PERMISSION, return_value=True):
			with patch(GET_LIST, return_value=[{"name": "CUST-0001", "label": "Al Test Trading"}]) as get_list:
				result = dc.similar_records("Customer", "Al Test")
		self.assertEqual(len(result), 1)
		_, kwargs = get_list.call_args
		self.assertEqual(kwargs["limit_page_length"], 5)
		self.assertEqual(kwargs["filters"]["customer_name"], ["like", "Al Test%"])
