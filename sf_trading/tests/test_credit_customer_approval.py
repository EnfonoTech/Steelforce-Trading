"""The bypass roster, and the earlier credit-customer approval answered by the Customer approval.

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
	def test_delegates_to_the_customer_approval(self):
		doc = StubDoc("Customer", custom_approval_status=None)
		with patch("sf_trading.master_approval.set_initial_status") as initial:
			cca.default_new_customer_status(doc)
		initial.assert_called_once()


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
	"""Answered by the Customer approval (master_approval.party_refusal)."""

	def test_return_is_exempt(self):
		doc = StubDoc("Sales Invoice", is_return=1, customer="CUST-0001")
		with patch("sf_trading.master_approval.party_refusal", side_effect=AssertionError("asked")):
			cca.validate_customer_approved_for_invoicing(doc)

	def test_approved_customer_passes(self):
		doc = StubDoc("Sales Invoice", is_return=0, customer="CUST-0001")
		with patch("sf_trading.master_approval.party_refusal", return_value=None):
			cca.validate_customer_approved_for_invoicing(doc)

	def test_unapproved_customer_throws(self):
		doc = StubDoc("Sales Invoice", is_return=0, customer="CUST-0001")
		with patch("sf_trading.master_approval.party_refusal", return_value="Customer CUST-0001 is not approved"):
			with self.assertRaises(frappe.ValidationError):
				cca.validate_customer_approved_for_invoicing(doc)
