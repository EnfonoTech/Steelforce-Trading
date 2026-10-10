"""Tests for the Customer B2B mandatory-field rule (GS Issue 1).

    bench --site <scratch-site> run-tests --module sf_trading.tests.test_party_completeness
"""

from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase

from sf_trading import party_completeness as pc


# Not a test of the new-master rules (sf_trading/tests/test_master_rules.py is): parties made here
# are not held to them, nor to the every-save Company Tax ID check.
_MASTER_RULES = (
	patch("sf_trading.party_documents.rules_from", return_value=None),
	patch("sf_trading.party_documents.validate_supplier_tax_id", return_value=None),
)


def setUpModule():
	for guard in _MASTER_RULES:
		guard.start()


def tearDownModule():
	for guard in _MASTER_RULES:
		guard.stop()


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
	"""2026-09-27, fifth round on this same live bug: this is now a plain truthy check on
	is_frozen/disabled -- client call: a BLANKET exemption, not just an "only status changed" one.
	No dependency on get_doc_before_save()/load_doc_before_save() at all any more, so a plain
	StubDoc exercises the real logic exactly like a real Document -- there is no separate
	"real Document" test class needed the way there was for the old diff-based version."""

	def test_a_plain_stub_with_is_frozen_set_is_exempt(self):
		doc = StubDoc("Customer", customer_type="Company", is_frozen=1)
		self.assertTrue(pc.only_status_fields_changed(doc))

	def test_a_plain_stub_with_disabled_set_is_exempt(self):
		doc = StubDoc("Customer", customer_type="Company", disabled=1)
		self.assertTrue(pc.only_status_fields_changed(doc))

	def test_a_plain_stub_with_neither_set_is_not_exempt(self):
		doc = StubDoc("Customer", customer_type="Company", is_frozen=0, disabled=0)
		self.assertFalse(pc.only_status_fields_changed(doc))

	def test_a_plain_stub_missing_the_fields_entirely_is_not_exempt(self):
		"""Must not raise for an object that never carries is_frozen/disabled at all."""
		doc = StubDoc("Customer", customer_type="Company")
		self.assertFalse(pc.only_status_fields_changed(doc))


class TestValidateCompanyFieldsStatusOnlyExemption(FrappeTestCase):
	"""2026-09-26 onward, several rounds on this same live bug ("313 Contracting", then "Bu Sanad
	for Steel and Aluminium WLL", then a live sweep across 240 real prod customers still found more
	blocked the same way): every attempt at proving "nothing ELSE changed in this same save" via a
	before/after diff kept finding a new field shape that broke it. 2026-09-27, client call: drop
	that entirely -- a frozen/disabled customer's master must be saveable NO MATTER what else is
	being edited in the same save. Real Documents + real .save() to exercise the full validate
	hook chain, not just the exemption function in isolation."""

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

	def test_allows_freezing_even_when_another_field_changes_in_the_same_save(self):
		"""The BLANKET part of the client's own wording, proven directly: a save that freezes the
		customer AND edits an unrelated field in the same request must still not raise -- the old
		diff-based version would have blocked exactly this."""
		customer = self._make_incomplete_b2b_customer("Test Blanket Freeze Plus Edit")
		customer.reload()
		customer.is_frozen = 1
		customer.website = "https://example.com"
		customer.save(ignore_permissions=True)  # must not raise
		self.assertEqual(frappe.db.get_value("Customer", customer.name, "is_frozen"), 1)
		self.assertEqual(frappe.db.get_value("Customer", customer.name, "website"), "https://example.com")


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
