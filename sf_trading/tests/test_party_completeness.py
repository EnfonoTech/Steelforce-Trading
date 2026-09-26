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
	def test_individual_customer_with_no_vat_is_never_checked(self):
		"""B2C (no VAT on file) is out of scope for this rule -- mobile/name are core-mandatory already."""
		doc = StubDoc("Customer", customer_type="Individual")
		self.assertEqual(pc.missing_company_fields(doc), [])

	def test_company_type_with_no_vat_is_not_checked(self):
		"""2026-09-26, second correction: the exact live case -- "Havelock One Interiors WLL",
		customer_type "Company", both CR and VAT blank -- must NOT be blocked any more. Being B2B
		(is_b2b_customer, VAT on file) is now the gate, not customer_type alone."""
		doc = StubDoc("Customer", customer_type="Company", **{pc.CR_FIELD: "", pc.VAT_FIELD: ""})
		self.assertEqual(pc.missing_company_fields(doc), [])

	def test_complete_company_customer_passes(self):
		self.assertEqual(pc.missing_company_fields(company_customer()), [])

	def test_b2b_customer_missing_cr_is_caught(self):
		"""VAT on file (so is_b2b_customer is True) but CR blank -- still reported."""
		missing = pc.missing_company_fields(company_customer(**{pc.CR_FIELD: ""}))
		self.assertEqual(missing, ["Commercial Registration Number"])

	def test_individual_with_a_vat_number_and_no_cr_is_also_caught(self):
		"""customer_type no longer matters -- is_b2b_customer does. An "Individual" record with a
		VAT number on file but no CR is just as much B2B here as a "Company" one."""
		missing = pc.missing_company_fields(
			StubDoc("Customer", customer_type="Individual", **{pc.CR_FIELD: "", pc.VAT_FIELD: "200012345600003"})
		)
		self.assertEqual(missing, ["Commercial Registration Number"])

	def test_validate_throws_on_incomplete_b2b_customer(self):
		with self.assertRaises(frappe.ValidationError):
			pc.validate_company_fields(company_customer(**{pc.CR_FIELD: ""}))

	def test_validate_is_silent_on_a_complete_customer(self):
		# Must not raise.
		pc.validate_company_fields(company_customer())

	def test_validate_is_silent_on_a_company_type_customer_with_no_vat(self):
		"""The Havelock case again, through the validate entry point this time."""
		pc.validate_company_fields(
			StubDoc("Customer", customer_type="Company", **{pc.CR_FIELD: "", pc.VAT_FIELD: ""})
		)

	def test_validate_applies_to_an_existing_customer_being_edited(self):
		"""No is_new() guard: an existing B2B customer with CR blanked out must also fail."""
		existing = company_customer(name="CUST-2026-00042", **{pc.CR_FIELD: ""})
		with self.assertRaises(frappe.ValidationError):
			pc.validate_company_fields(existing)


class TestOnlyStatusFieldsChanged(FrappeTestCase):
	def test_a_plain_stub_with_no_get_doc_before_save_is_never_status_only(self):
		"""only_status_fields_changed must not crash on an object that isn't a real Document --
		every unit test in this file (and customer_permission's) passes a plain StubDoc/_dict with
		no get_doc_before_save at all, and existing behaviour for those must not change."""
		doc = StubDoc("Customer", customer_type="Company", is_frozen=1)
		self.assertFalse(pc.only_status_fields_changed(doc))


class TestValidateCompanyFieldsStatusOnlyExemption(FrappeTestCase):
	"""2026-09-26, third round on the same live bug: "313 Contracting" (B2B via VAT on file, CR
	still blank) kept failing THIS gate right after customer_override's own VAT-attachment check
	was fixed to skip on a status-only save -- validate_company_fields is a separate hook and had
	no such exemption of its own. Real Documents + real .save(), not StubDoc -- StubDoc has no
	get_doc_before_save, so it can never exercise this exemption at all (see the class above)."""

	def _make_incomplete_b2b_customer(self, name):
		"""ignore_validate on the insert -- the incompleteness under test is exactly what a normal
		insert would already refuse; this needs an already-existing record sitting in that state,
		the same way real migrated/legacy data would arrive at it without ever passing through this
		rule. Reset immediately after -- it must not leak into the caller's own later .save() and
		silently skip the very validation that save is meant to exercise."""
		leaf_group = frappe.db.get_value("Customer Group", {"is_group": 0}, "name")
		customer = frappe.get_doc(
			{
				"doctype": "Customer",
				"customer_name": name,
				"customer_type": "Company",
				"customer_group": leaf_group,
				"mobile_no": "33445566",
				pc.VAT_FIELD: "200013075500002",
				# CR deliberately left blank
			}
		)
		customer.flags.ignore_validate = True
		customer.insert(ignore_permissions=True)
		customer.flags.ignore_validate = False
		return customer

	def test_blocks_a_normal_edit_with_cr_still_missing(self):
		customer = self._make_incomplete_b2b_customer("Test 313 Contracting Style Customer")
		customer.reload()
		customer.website = "https://example.com"
		with self.assertRaises(frappe.ValidationError):
			customer.save(ignore_permissions=True)

	def test_allows_freezing_with_cr_still_missing(self):
		"""The exact live case: 313 Contracting, VAT on file, CR blank, freezing it."""
		customer = self._make_incomplete_b2b_customer("Test 313 Contracting Style Freeze")
		customer.reload()
		customer.is_frozen = 1
		customer.save(ignore_permissions=True)  # must not raise
		self.assertEqual(frappe.db.get_value("Customer", customer.name, "is_frozen"), 1)


class TestIsB2BCustomer(FrappeTestCase):
	"""2026-09-26: B2B = VAT Registration Number on file. Full stop -- customer_type is NOT
	consulted (client correction, same day, after live UAT test surfaced "Havelock One Interiors
	WLL": customer_type "Company" with a blank VAT was still reading as B2B under the earlier
	widened OR-with-customer_type version). missing_company_fields (GS Issue 1's own CR/VAT rule,
	above) shares this SAME gate as of the same day's second correction -- see
	TestPartyCompleteness.test_company_type_with_no_vat_is_not_checked."""

	def test_company_type_with_no_vat_is_b2c(self):
		"""The exact real case: customer_type "Company" but VAT still blank -- must NOT be B2B."""
		doc = StubDoc("Customer", customer_type="Company", **{pc.VAT_FIELD: ""})
		self.assertFalse(pc.is_b2b_customer(doc))

	def test_individual_with_no_vat_is_b2c(self):
		doc = StubDoc("Customer", customer_type="Individual", **{pc.VAT_FIELD: ""})
		self.assertFalse(pc.is_b2b_customer(doc))

	def test_individual_with_a_vat_number_is_b2b(self):
		"""customer_type stuck at "Individual" by old migrated data, but a VAT number is on file."""
		doc = StubDoc("Customer", customer_type="Individual", **{pc.VAT_FIELD: "200012345600003"})
		self.assertTrue(pc.is_b2b_customer(doc))

	def test_company_type_with_a_vat_number_is_b2b(self):
		doc = StubDoc("Customer", customer_type="Company", **{pc.VAT_FIELD: "200012345600003"})
		self.assertTrue(pc.is_b2b_customer(doc))

	def test_blank_customer_type_with_a_vat_number_is_b2b(self):
		doc = StubDoc("Customer", customer_type="", **{pc.VAT_FIELD: "200012345600003"})
		self.assertTrue(pc.is_b2b_customer(doc))

	def test_accepts_a_customer_name_string_too(self):
		with patch(GET_VALUE, return_value="200012345600003"):
			self.assertTrue(pc.is_b2b_customer("CUST-0001"))

	def test_a_customer_name_string_with_no_vat_is_b2c(self):
		with patch(GET_VALUE, return_value=""):
			self.assertFalse(pc.is_b2b_customer("CUST-0001"))

	def test_a_nonexistent_customer_name_is_b2c_not_an_error(self):
		with patch(GET_VALUE, return_value=None):
			self.assertFalse(pc.is_b2b_customer("CUST-DOES-NOT-EXIST"))
