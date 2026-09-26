"""Tests for the Customer VAT-document attachment override (api/customer_override.py).

    bench --site <scratch-site> run-tests --module sf_trading.tests.test_customer_override
"""

import frappe
from frappe.tests.utils import FrappeTestCase


class TestCustomerOverrideVatAttachment(FrappeTestCase):
	def _make_vat_customer(self, name):
		# CR filled in too, so party_completeness.validate_company_fields (a separate hook in the
		# same Customer validate chain) never fires here -- this suite is only about
		# customer_override's own attachment rule. customer_group looked up rather than hardcoded --
		# core's own validate_customer_group refuses a GROUP-type node, and the leaf name varies
		# by site. mobile_no filled in too -- mandatory on this site's Customer (a Property
		# Setter, not present on every bench), and this suite has no phone cache to fill it from.
		leaf_group = frappe.db.get_value("Customer Group", {"is_group": 0}, "name")
		return frappe.get_doc(
			{
				"doctype": "Customer",
				"customer_name": name,
				"customer_type": "Company",
				"customer_group": leaf_group,
				"mobile_no": "33445566",
				"custom_vat_registration_number": "200013075500002",
				"custom_commercial_registration_number": "CR-99999",
			}
		).insert(ignore_permissions=True)

	def test_blocks_a_normal_edit_with_no_attachment(self):
		customer = self._make_vat_customer("Test VAT Customer No Attachment")
		customer.reload()
		customer.website = "https://example.com"
		with self.assertRaises(frappe.ValidationError):
			customer.save(ignore_permissions=True)

	def test_allows_freezing_with_no_attachment(self):
		"""The reported bug: toggling Is Frozen on a VAT-registered customer with no attachment
		yet must not be blocked -- freezing is often exactly the action taken BECAUSE the document
		is still missing."""
		customer = self._make_vat_customer("Test VAT Customer Freeze")
		customer.reload()
		customer.is_frozen = 1
		customer.save(ignore_permissions=True)  # must not raise
		self.assertEqual(frappe.db.get_value("Customer", customer.name, "is_frozen"), 1)

	def test_allows_disabling_with_no_attachment(self):
		customer = self._make_vat_customer("Test VAT Customer Disable")
		customer.reload()
		customer.disabled = 1
		customer.save(ignore_permissions=True)  # must not raise
		self.assertEqual(frappe.db.get_value("Customer", customer.name, "disabled"), 1)

	def test_still_blocks_other_field_edit_alongside_freeze(self):
		"""Freezing does not blanket-exempt the whole save -- only is_frozen/disabled changes are
		exempt. Changing something else in the SAME save is still blocked."""
		customer = self._make_vat_customer("Test VAT Customer Freeze Plus Edit")
		customer.reload()
		customer.is_frozen = 1
		customer.website = "https://example.com"
		with self.assertRaises(frappe.ValidationError):
			customer.save(ignore_permissions=True)

	def test_no_vat_number_is_never_checked(self):
		leaf_group = frappe.db.get_value("Customer Group", {"is_group": 0}, "name")
		customer = frappe.get_doc(
			{
				"doctype": "Customer",
				"customer_name": "Test No VAT Customer",
				"customer_type": "Individual",
				"customer_group": leaf_group,
				"mobile_no": "33445566",
			}
		).insert(ignore_permissions=True)
		customer.reload()
		customer.website = "https://example.com"
		customer.save(ignore_permissions=True)  # must not raise -- no VAT number at all
