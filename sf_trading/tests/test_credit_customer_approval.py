"""Tests for credit customer verification/approval gating Sales Invoice.

    bench --site <scratch-site> run-tests --module sf_trading.tests.test_credit_customer_approval
"""

from unittest.mock import MagicMock, patch

import frappe
from frappe.tests.utils import FrappeTestCase

from sf_trading import credit_customer_approval as cca


class StubDoc:
	def __init__(self, doctype, **fields):
		self.doctype = doctype
		self.name = fields.pop("name", None)
		self.__dict__.update(fields)

	def get(self, key, default=None):
		return self.__dict__.get(key, default)


def _stub_settings(rows=None):
	settings = MagicMock()
	settings.get.return_value = rows or []
	return settings


class TestDefaultNewCustomerStatus(FrappeTestCase):
	def test_new_customer_defaults_to_pending(self):
		doc = StubDoc("Customer", custom_approval_status=None)
		cca.default_new_customer_status(doc)
		self.assertEqual(doc.custom_approval_status, cca.PENDING)

	def test_does_not_override_an_explicit_status(self):
		doc = StubDoc("Customer", custom_approval_status=cca.APPROVED)
		cca.default_new_customer_status(doc)
		self.assertEqual(doc.custom_approval_status, cca.APPROVED)


class TestMayBypass(FrappeTestCase):
	def test_administrator_always_may(self):
		with patch.object(cca, "settings", return_value=_stub_settings()):
			self.assertTrue(cca.may_bypass(user="Administrator"))

	def test_no_roster_rows_refuses(self):
		with patch.object(cca, "settings", return_value=_stub_settings([])):
			self.assertFalse(cca.may_bypass(user="someone@example.com"))

	def test_named_user_row_allows(self):
		row = MagicMock(override_type="User", override="someone@example.com")
		with patch.object(cca, "settings", return_value=_stub_settings([row])):
			self.assertTrue(cca.may_bypass(user="someone@example.com"))

	def test_role_row_allows_a_holder(self):
		row = MagicMock(override_type="Role", override="Credit Approval Officer")
		with patch.object(cca, "settings", return_value=_stub_settings([row])), patch(
			"sf_trading.credit_customer_approval.frappe.get_roles",
			return_value=["Sales User", "Credit Approval Officer"],
		):
			self.assertTrue(cca.may_bypass(user="someone@example.com"))

	def test_role_row_refuses_a_non_holder(self):
		row = MagicMock(override_type="Role", override="Credit Approval Officer")
		with patch.object(cca, "settings", return_value=_stub_settings([row])), patch(
			"sf_trading.credit_customer_approval.frappe.get_roles", return_value=["Sales User"]
		):
			self.assertFalse(cca.may_bypass(user="someone@example.com"))


class TestValidateCustomerApprovedForInvoicing(FrappeTestCase):
	def test_return_is_exempt(self):
		doc = StubDoc("Sales Invoice", is_return=1, customer="CUST-0001")
		# would throw if it looked up the customer at all -- get_value is left unmocked so a
		# lookup would raise inside this test DB context; passing means it never tried
		cca.validate_customer_approved_for_invoicing(doc)

	def test_approved_customer_passes(self):
		doc = StubDoc("Sales Invoice", is_return=0, customer="CUST-0001")
		with patch(
			"sf_trading.credit_customer_approval.frappe.db.get_value", return_value=cca.APPROVED
		):
			cca.validate_customer_approved_for_invoicing(doc)

	def test_pending_customer_without_bypass_throws(self):
		doc = StubDoc("Sales Invoice", is_return=0, customer="CUST-0001")
		with patch(
			"sf_trading.credit_customer_approval.frappe.db.get_value", return_value=cca.PENDING
		), patch.object(cca, "may_bypass", return_value=False):
			with self.assertRaises(frappe.ValidationError):
				cca.validate_customer_approved_for_invoicing(doc)

	def test_pending_customer_with_bypass_passes(self):
		doc = StubDoc("Sales Invoice", is_return=0, customer="CUST-0001")
		with patch(
			"sf_trading.credit_customer_approval.frappe.db.get_value", return_value=cca.PENDING
		), patch.object(cca, "may_bypass", return_value=True):
			cca.validate_customer_approved_for_invoicing(doc)

	def test_rejected_customer_without_bypass_throws(self):
		doc = StubDoc("Sales Invoice", is_return=0, customer="CUST-0001")
		with patch(
			"sf_trading.credit_customer_approval.frappe.db.get_value", return_value=cca.REJECTED
		), patch.object(cca, "may_bypass", return_value=False):
			with self.assertRaises(frappe.ValidationError):
				cca.validate_customer_approved_for_invoicing(doc)
