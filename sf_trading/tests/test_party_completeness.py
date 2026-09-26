"""Tests for the Customer B2B mandatory-field rule (GS Issue 1).

    bench --site <scratch-site> run-tests --module sf_trading.tests.test_party_completeness
"""

from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase

from sf_trading import party_completeness as pc

GET_VALUE = "sf_trading.party_completeness.frappe.db.get_value"


class StubDoc:
	def __init__(self, doctype, **fields):
		self.doctype = doctype
		self.__dict__.update(fields)
		self.name = fields.get("name")

	def get(self, key, default=None):
		return self.__dict__.get(key, default)


def company_customer(**overrides):
	fields = {
		"customer_type": "Company",
		"customer_name": "Al Test Trading W.L.L.",
		pc.CR_FIELD: "CR-12345",
		pc.VAT_FIELD: "200012345600003",
	}
	fields.update(overrides)
	return StubDoc("Customer", **fields)


class TestPartyCompleteness(FrappeTestCase):
	def test_individual_customer_is_never_checked(self):
		"""B2C is out of scope for this rule -- mobile/name are core-mandatory already."""
		doc = StubDoc("Customer", customer_type="Individual")
		self.assertEqual(pc.missing_company_fields(doc), [])

	def test_complete_company_customer_passes(self):
		self.assertEqual(pc.missing_company_fields(company_customer()), [])

	def test_company_customer_missing_cr_is_caught(self):
		missing = pc.missing_company_fields(company_customer(**{pc.CR_FIELD: ""}))
		self.assertIn("Commercial Registration Number", missing)
		self.assertNotIn("VAT Registration Number", missing)

	def test_company_customer_missing_vat_is_caught(self):
		missing = pc.missing_company_fields(company_customer(**{pc.VAT_FIELD: ""}))
		self.assertIn("VAT Registration Number", missing)

	def test_company_customer_missing_both_lists_both(self):
		missing = pc.missing_company_fields(
			company_customer(**{pc.CR_FIELD: "", pc.VAT_FIELD: ""})
		)
		self.assertEqual(len(missing), 2)

	def test_validate_throws_on_incomplete_company_customer(self):
		with self.assertRaises(frappe.ValidationError):
			pc.validate_company_fields(company_customer(**{pc.CR_FIELD: ""}))

	def test_validate_is_silent_on_a_complete_customer(self):
		# Must not raise.
		pc.validate_company_fields(company_customer())

	def test_validate_applies_to_an_existing_customer_being_edited(self):
		"""No is_new() guard: an existing customer with a field blanked out must also fail."""
		existing = company_customer(name="CUST-2026-00042", **{pc.VAT_FIELD: ""})
		with self.assertRaises(frappe.ValidationError):
			pc.validate_company_fields(existing)


class TestIsB2BCustomer(FrappeTestCase):
	"""2026-09-26: B2B widened to "customer_type == Company OR a VAT number is on file" -- a
	VAT-bearing customer must count as B2B even when customer_type was left at "Individual" by
	old migrated data (confirmed live the same day: "Arab United Trading & Marketing (000752)").
	missing_company_fields (GS Issue 1's own Company-only CR/VAT rule, above) is untouched --
	these two functions answer different questions on purpose."""

	def test_company_type_is_b2b_even_with_no_vat_yet(self):
		doc = StubDoc("Customer", customer_type="Company", **{pc.VAT_FIELD: ""})
		self.assertTrue(pc.is_b2b_customer(doc))

	def test_individual_with_no_vat_is_b2c(self):
		doc = StubDoc("Customer", customer_type="Individual", **{pc.VAT_FIELD: ""})
		self.assertFalse(pc.is_b2b_customer(doc))

	def test_individual_with_a_vat_number_is_still_b2b(self):
		"""The exact real case: customer_type stuck at "Individual", but a genuine VAT-bearing
		business."""
		doc = StubDoc("Customer", customer_type="Individual", **{pc.VAT_FIELD: "200012345600003"})
		self.assertTrue(pc.is_b2b_customer(doc))

	def test_blank_customer_type_with_a_vat_number_is_b2b(self):
		doc = StubDoc("Customer", customer_type="", **{pc.VAT_FIELD: "200012345600003"})
		self.assertTrue(pc.is_b2b_customer(doc))

	def test_accepts_a_customer_name_string_too(self):
		with patch(GET_VALUE, return_value=frappe._dict({"customer_type": "Individual", pc.VAT_FIELD: "200012345600003"})):
			self.assertTrue(pc.is_b2b_customer("CUST-0001"))

	def test_a_customer_name_string_with_neither_signal_is_b2c(self):
		with patch(GET_VALUE, return_value=frappe._dict({"customer_type": "Individual", pc.VAT_FIELD: ""})):
			self.assertFalse(pc.is_b2b_customer("CUST-0001"))

	def test_a_nonexistent_customer_name_is_b2c_not_an_error(self):
		with patch(GET_VALUE, return_value=None):
			self.assertFalse(pc.is_b2b_customer("CUST-DOES-NOT-EXIST"))
