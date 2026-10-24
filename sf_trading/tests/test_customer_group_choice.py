"""The Create New Customer dialog's Customer Group (api/customer.py _resolve_customer_group).

    bench --site <scratch-site> run-tests --module sf_trading.tests.test_customer_group_choice
"""

import frappe
from frappe.tests.utils import FrappeTestCase

from sf_trading.api.customer import _resolve_customer_group


class TestCustomerGroupChoice(FrappeTestCase):
	def test_a_leaf_group_is_used_as_asked(self):
		leaf = frappe.db.get_value("Customer Group", {"is_group": 0}, "name")
		self.assertEqual(_resolve_customer_group(leaf), leaf)

	def test_a_group_node_is_refused_before_anything_is_created(self):
		"""The site default is the root; core would refuse it only after the address work started."""
		group = frappe.db.get_value("Customer Group", {"is_group": 1}, "name")
		with self.assertRaises(frappe.ValidationError):
			_resolve_customer_group(group)

	def test_an_unknown_group_is_refused(self):
		with self.assertRaises(frappe.ValidationError):
			_resolve_customer_group("No Such Customer Group")

	def test_no_group_asked_for_falls_to_the_site_default_leaf(self):
		"""An old dialog still cached in a browser sends no group: it must not start failing."""
		group = _resolve_customer_group(None)
		self.assertFalse(frappe.db.get_value("Customer Group", group, "is_group"))
