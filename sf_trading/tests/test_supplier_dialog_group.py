"""Tests for the Supplier Group asked by the Create New Supplier dialog (api/supplier.py).

    bench --site <scratch-site> run-tests --module sf_trading.tests.test_supplier_dialog_group
"""

from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase

from sf_trading.api import supplier as api

DB_VALUE = "sf_trading.api.supplier.frappe.db.get_value"
DEFAULT = "sf_trading.api.supplier._get_default_supplier_group"


class TestResolveSupplierGroup(FrappeTestCase):
	def test_no_group_asked_falls_to_the_site_default(self):
		with patch(DEFAULT, return_value="Local"):
			self.assertEqual(api._resolve_supplier_group(None), "Local")
			self.assertEqual(api._resolve_supplier_group(""), "Local")

	def test_a_leaf_group_is_taken_as_it_is(self):
		with patch(DB_VALUE, return_value=0):
			self.assertEqual(api._resolve_supplier_group("Govt"), "Govt")

	def test_a_group_node_is_refused(self):
		with patch(DB_VALUE, return_value=1), self.assertRaises(frappe.ValidationError):
			api._resolve_supplier_group("All Supplier Groups")

	def test_an_unknown_group_is_refused(self):
		with patch(DB_VALUE, return_value=None), self.assertRaises(frappe.ValidationError):
			api._resolve_supplier_group("Nope")
