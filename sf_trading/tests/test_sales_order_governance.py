"""Tests for GS Issues 17/18/20 -- cancellation control and the open-order cap.

    bench --site <scratch-site> run-tests --module sf_trading.tests.test_sales_order_governance
"""

from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase

from sf_trading import sales_order_governance as gov

MISSING_PHONE = "sf_trading.sales_order_governance.missing_contact_phone"
COUNT_OPEN = "sf_trading.sales_order_governance.count_open_sales_orders"


class StubDoc:
	def __init__(self, doctype, **fields):
		self.doctype = doctype
		self.__dict__.update(fields)
		self.name = fields.get("name")

	def get(self, key, default=None):
		return self.__dict__.get(key, default)


def sales_order(**overrides):
	fields = {
		"name": "SAL-ORD-2026-00099",
		"customer": "CUST-2026-00042",
		"customer_name": "Al Test Trading W.L.L.",
	}
	fields.update(overrides)
	return StubDoc("Sales Order", **fields)


class TestContactCompletenessAtTransaction(FrappeTestCase):
	def test_blocks_when_customer_has_no_phone(self):
		with patch(MISSING_PHONE, return_value=True):
			with self.assertRaises(frappe.ValidationError):
				gov.validate_customer_contact_at_transaction(sales_order())

	def test_passes_when_customer_has_a_phone(self):
		with patch(MISSING_PHONE, return_value=False):
			gov.validate_customer_contact_at_transaction(sales_order())  # must not raise

	def test_a_document_with_no_customer_is_not_checked(self):
		with patch(MISSING_PHONE) as lookup:
			gov.validate_customer_contact_at_transaction(sales_order(customer=None))
		lookup.assert_not_called()


class TestPendingOrderCap(FrappeTestCase):
	def test_blocks_the_third_open_order(self):
		with patch(COUNT_OPEN, return_value=gov.PENDING_SO_CAP):
			with self.assertRaises(frappe.ValidationError):
				gov.before_submit_cap_pending_orders(sales_order())

	def test_a_customer_below_the_cap_is_not_blocked(self):
		with patch(COUNT_OPEN, return_value=gov.PENDING_SO_CAP - 1):
			gov.before_submit_cap_pending_orders(sales_order())  # must not raise

	def test_excludes_the_document_itself_from_its_own_count(self):
		with patch(COUNT_OPEN, return_value=0) as counter:
			gov.before_submit_cap_pending_orders(sales_order())
		counter.assert_called_once_with("CUST-2026-00042", exclude="SAL-ORD-2026-00099")


class TestCancellationControl(FrappeTestCase):
	def test_blocks_a_cancel_with_no_remark(self):
		doc = sales_order(custom_cancellation_remark="")
		with self.assertRaises(frappe.ValidationError):
			gov.before_cancel_require_remark_and_branch_head(doc)

	def test_blocks_a_remarked_cancel_from_a_user_without_the_role(self):
		doc = sales_order(custom_cancellation_remark="Customer changed their mind")
		with patch("frappe.get_roles", return_value=["Sales User"]):
			with self.assertRaises(frappe.ValidationError):
				gov.before_cancel_require_remark_and_branch_head(doc)

	def test_a_branch_head_with_a_remark_may_cancel(self):
		doc = sales_order(custom_cancellation_remark="Customer changed their mind")
		with patch("frappe.get_roles", return_value=[gov.ROLE_BRANCH_HEAD]):
			gov.before_cancel_require_remark_and_branch_head(doc)  # must not raise

	def test_a_system_manager_may_also_cancel(self):
		"""So Administrator/support can always unblock a mistake, without needing Branch Head."""
		doc = sales_order(custom_cancellation_remark="testing")
		with patch("frappe.get_roles", return_value=["System Manager"]):
			gov.before_cancel_require_remark_and_branch_head(doc)  # must not raise


class TestCreditCustomerRequirements(FrappeTestCase):
	CREDIT_LIMIT_EXISTS = "sf_trading.sales_order_governance.frappe.db.exists"
	PHONE_NUMBERS = "sf_trading.sales_order_governance.party_phone_numbers"

	def test_a_non_credit_customer_is_never_checked(self):
		"""No Customer Credit Limit row > 0 -- this rule does not apply at all."""
		with patch(self.CREDIT_LIMIT_EXISTS, return_value=False) as exists:
			with patch(self.PHONE_NUMBERS) as phones:
				missing = gov.missing_credit_customer_requirements("CUST-0001")
		self.assertEqual(missing, [])
		phones.assert_not_called()
		exists.assert_called_once()

	def test_a_credit_customer_with_one_phone_and_no_attachment_lists_both(self):
		def fake_exists(doctype, filters):
			if doctype == "Customer Credit Limit":
				return True
			return False  # no File row

		with patch(self.CREDIT_LIMIT_EXISTS, side_effect=fake_exists):
			with patch(self.PHONE_NUMBERS, return_value=["33445566"]):
				missing = gov.missing_credit_customer_requirements("CUST-0001")
		self.assertEqual(len(missing), 2)

	def test_a_fully_compliant_credit_customer_passes(self):
		with patch(self.CREDIT_LIMIT_EXISTS, return_value=True):
			with patch(self.PHONE_NUMBERS, return_value=["33445566", "17001122"]):
				missing = gov.missing_credit_customer_requirements("CUST-0001")
		self.assertEqual(missing, [])

	def test_validate_throws_naming_what_is_missing(self):
		doc = sales_order(customer_name="Al Test Trading W.L.L.")
		with patch.object(gov, "missing_credit_customer_requirements", return_value=["at least 2 contact numbers (found 1)"]):
			with self.assertRaises(frappe.ValidationError):
				gov.validate_credit_customer_requirements_at_transaction(doc)


class TestB2BPhoneRequirements(FrappeTestCase):
	"""missing_b2b_phone_requirements delegates its "is this B2B" question to
	party_completeness.is_b2b_customer -- these tests only need to prove the delegation and the
	phone-count logic on top of it. is_b2b_customer's own customer_type/VAT widening logic is
	tested directly in test_party_completeness.py."""

	IS_B2B = "sf_trading.sales_order_governance.is_b2b_customer"
	PHONE_NUMBERS = "sf_trading.sales_order_governance.party_phone_numbers"

	def test_a_non_b2b_customer_is_never_checked(self):
		with patch(self.IS_B2B, return_value=False):
			with patch(self.PHONE_NUMBERS) as phones:
				missing = gov.missing_b2b_phone_requirements("CUST-0001")
		self.assertEqual(missing, [])
		phones.assert_not_called()

	def test_a_b2b_customer_with_one_phone_is_incomplete(self):
		with patch(self.IS_B2B, return_value=True):
			with patch(self.PHONE_NUMBERS, return_value=["33445566"]):
				missing = gov.missing_b2b_phone_requirements("CUST-0001")
		self.assertEqual(len(missing), 1)

	def test_a_b2b_customer_with_two_phones_passes(self):
		with patch(self.IS_B2B, return_value=True):
			with patch(self.PHONE_NUMBERS, return_value=["33445566", "17001122"]):
				missing = gov.missing_b2b_phone_requirements("CUST-0001")
		self.assertEqual(missing, [])

	def test_validate_throws_naming_what_is_missing(self):
		doc = sales_order(customer_name="Al Test Trading W.L.L.")
		with patch.object(gov, "missing_b2b_phone_requirements", return_value=["at least 2 contact numbers for a B2B customer (found 1)"]):
			with self.assertRaises(frappe.ValidationError):
				gov.validate_b2b_phone_requirements_at_transaction(doc)

	def test_validate_passes_a_compliant_b2b_customer(self):
		doc = sales_order()
		with patch.object(gov, "missing_b2b_phone_requirements", return_value=[]):
			gov.validate_b2b_phone_requirements_at_transaction(doc)  # must not raise

	def test_a_document_with_no_customer_is_not_checked(self):
		with patch.object(gov, "missing_b2b_phone_requirements") as check:
			gov.validate_b2b_phone_requirements_at_transaction(sales_order(customer=None))
		check.assert_not_called()
