"""Tests for the "N similar records found" search (GS Issue 15).

    bench --site <scratch-site> run-tests --module sf_trading.tests.test_duplicate_check
"""

from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase

from sf_trading.api import duplicate_check as dc

HAS_PERMISSION = "sf_trading.api.duplicate_check.frappe.has_permission"
GET_LIST = "sf_trading.api.duplicate_check.frappe.get_list"
ENABLED = "sf_trading.api.duplicate_check.enabled"


class TestSimilarRecords(FrappeTestCase):
	def setUp(self):
		# the SF Trading Settings switch is on for every test but the one about the switch itself
		self._enabled = patch(ENABLED, return_value=True)
		self._enabled.start()
		self.addCleanup(self._enabled.stop)

	def test_switch_off_returns_nothing_without_a_query(self):
		self._enabled.stop()
		with patch(ENABLED, return_value=False), patch(GET_LIST) as get_list:
			result = dc.similar_records("Customer", "Al Test")
		self._enabled.start()
		self.assertEqual(result, [])
		get_list.assert_not_called()

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

	def test_a_real_search_matches_every_word_in_any_order(self):
		with patch(HAS_PERMISSION, return_value=True):
			with patch(GET_LIST, return_value=[{"name": "CUST-0001", "label": "Al Test Trading"}]) as get_list:
				result = dc.similar_records("Customer", "Test Al")
		self.assertEqual(len(result), 1)
		_, kwargs = get_list.call_args
		self.assertEqual(kwargs["limit_page_length"], 8)
		self.assertEqual(
			kwargs["filters"], [["customer_name", "like", "%Test%"], ["customer_name", "like", "%Al%"]]
		)

	def test_a_master_with_an_enabled_flag_answers_disabled_like_the_rest(self):
		with patch(HAS_PERMISSION, return_value=True):
			rows = [frappe._dict(name="Kg", label="Kg", enabled=0), frappe._dict(name="Kgs", label="Kgs", enabled=1)]
			with patch(GET_LIST, return_value=rows) as get_list:
				result = dc.similar_records("UOM", "kgs")
		_, kwargs = get_list.call_args
		self.assertEqual(kwargs["fields"], ["name", "uom_name as label", "enabled"])
		self.assertEqual([r.disabled for r in result], [True, False])
		self.assertNotIn("enabled", result[0])

	def test_an_employee_is_searched_by_full_name_and_left_counts_as_disabled(self):
		with patch(HAS_PERMISSION, return_value=True):
			rows = [frappe._dict(name="HR-EMP-1", label="Ali Hassan", status="Left")]
			with patch(GET_LIST, return_value=rows) as get_list:
				result = dc.similar_records("Employee", "ali hassan")
		_, kwargs = get_list.call_args
		self.assertEqual(kwargs["filters"], [["employee_name", "like", "%ali%"], ["employee_name", "like", "%hassan%"]])
		self.assertTrue(result[0].disabled)
		self.assertNotIn("status", result[0])
