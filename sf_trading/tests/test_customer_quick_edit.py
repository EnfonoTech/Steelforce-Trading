"""Tests for the Sales Invoice Customer quick-edit provision (client ask, 2026-09-26).

    bench --site <scratch-site> run-tests --module sf_trading.tests.test_customer_quick_edit
"""

from unittest.mock import MagicMock, patch

import frappe
from frappe.tests.utils import FrappeTestCase

from sf_trading.api import customer_quick_edit as qe

MOD = "sf_trading.api.customer_quick_edit"


class StubDoc:
	def __init__(self, doctype, **fields):
		self.doctype = doctype
		self.name = fields.pop("name", None)
		self.__dict__.update(fields)
		self._appended = {}

	def get(self, key, default=None):
		return self.__dict__.get(key, default)

	def set(self, key, value):
		setattr(self, key, value)

	def append(self, table, row):
		self._appended.setdefault(table, []).append(row)

	def save(self, ignore_permissions=False):
		pass


class TestPrimaryContactAndAddress(FrappeTestCase):
	def test_primary_contact_prefers_the_customer_field(self):
		with patch(f"{MOD}.frappe.db.get_value", return_value="CONTACT-0001") as get_value:
			result = qe._primary_contact("CUST-0001")
		self.assertEqual(result, "CONTACT-0001")
		get_value.assert_called_once_with("Customer", "CUST-0001", "customer_primary_contact")

	def test_primary_contact_falls_back_to_dynamic_link(self):
		with patch(f"{MOD}.frappe.db.get_value", side_effect=[None, "CONTACT-0002"]):
			result = qe._primary_contact("CUST-0001")
		self.assertEqual(result, "CONTACT-0002")

	def test_primary_contact_none_when_nothing_linked(self):
		with patch(f"{MOD}.frappe.db.get_value", side_effect=[None, None]):
			result = qe._primary_contact("CUST-0001")
		self.assertIsNone(result)


class TestGetQuickEditData(FrappeTestCase):
	def test_returns_current_values_and_missing_gates(self):
		customer_doc = StubDoc(
			"Customer",
			name="CUST-0001",
			customer_name="Acme",
			customer_type="Company",
			custom_commercial_registration_number="",
			custom_vat_registration_number="12345",
		)
		with patch(f"{MOD}.frappe.has_permission"):
			with patch(f"{MOD}.frappe.get_cached_doc", return_value=customer_doc):
				with patch.object(qe, "_primary_contact", return_value="CONTACT-0001"):
					with patch.object(qe, "_primary_address", return_value=None):
						with patch(f"{MOD}.party_phone_numbers", return_value=["33445566"]):
							with patch(f"{MOD}.missing_company_fields", return_value=["Commercial Registration Number"]):
								with patch(f"{MOD}.missing_credit_customer_requirements", return_value=[]):
									with patch(f"{MOD}.missing_b2b_phone_requirements", return_value=["at least 2 contact numbers for a B2B customer (found 1)"]):
										with patch(f"{MOD}.frappe.db.exists", return_value=False):
											data = qe.get_quick_edit_data("CUST-0001")

		self.assertEqual(data["customer_name"], "Acme")
		self.assertTrue(data["is_company"])
		self.assertTrue(data["is_b2b"])
		self.assertEqual(data["phone_1"], "33445566")
		self.assertEqual(data["phone_2"], "")
		self.assertFalse(data["has_attachment"])
		self.assertIn("Commercial Registration Number", data["missing"]["company_fields"])
		self.assertEqual(data["missing"]["credit_customer"], [])
		self.assertTrue(data["missing"]["b2b_phone"])
		self.assertEqual(data["missing"]["any_phone"], [])

	def test_an_individual_typed_customer_with_a_vat_number_is_flagged_b2b_but_not_company(self):
		"""is_b2b (VAT-only, party_completeness.is_b2b_customer) and is_company (customer_type==
		"Company", mirrors missing_company_fields/GS Issue 1) are independent flags -- a
		customer_type "Individual" record with a VAT number on file is is_b2b=True (so the dialog
		requires a 2nd phone number) but is_company=False (so the dialog does NOT show CR/VAT
		fields -- missing_company_fields never applies to a non-Company customer either)."""
		customer_doc = StubDoc(
			"Customer",
			name="CUST-0002",
			customer_name="Legacy Trading",
			customer_type="Individual",
			custom_commercial_registration_number="",
			custom_vat_registration_number="200098765400003",
		)
		with patch(f"{MOD}.frappe.has_permission"):
			with patch(f"{MOD}.frappe.get_cached_doc", return_value=customer_doc):
				with patch.object(qe, "_primary_contact", return_value=None):
					with patch.object(qe, "_primary_address", return_value=None):
						with patch(f"{MOD}.party_phone_numbers", return_value=[]):
							with patch(f"{MOD}.missing_company_fields", return_value=[]):
								with patch(f"{MOD}.missing_credit_customer_requirements", return_value=[]):
									with patch(f"{MOD}.missing_b2b_phone_requirements", return_value=[]):
										with patch(f"{MOD}.frappe.db.exists", return_value=False):
											data = qe.get_quick_edit_data("CUST-0002")

		self.assertFalse(data["is_company"])
		self.assertTrue(data["is_b2b"])

	def test_a_company_typed_customer_with_no_vat_yet_shows_cr_vat_fields_regardless(self):
		"""The exact bug reported live (2026-09-26): "Havelock One Interiors WLL" is customer_type
		"Company" with a blank VAT. missing_company_fields (GS Issue 1) still names CR+VAT as
		blocking billing -- so the dialog MUST still show is_company=True (CR/VAT fields visible)
		even though is_b2b (VAT-only) is False here. Before this fix, a single conflated
		is_company=is_b2b_customer(doc) flag hid the very fields the banner said were missing."""
		customer_doc = StubDoc(
			"Customer",
			name="CUST-0003",
			customer_name="Havelock One Interiors WLL",
			customer_type="Company",
			custom_commercial_registration_number="",
			custom_vat_registration_number="",
		)
		with patch(f"{MOD}.frappe.has_permission"):
			with patch(f"{MOD}.frappe.get_cached_doc", return_value=customer_doc):
				with patch.object(qe, "_primary_contact", return_value=None):
					with patch.object(qe, "_primary_address", return_value=None):
						with patch(f"{MOD}.party_phone_numbers", return_value=["33445566"]):
							with patch(f"{MOD}.missing_company_fields", return_value=["Commercial Registration Number", "VAT Registration Number"]):
								with patch(f"{MOD}.missing_credit_customer_requirements", return_value=[]):
									with patch(f"{MOD}.missing_b2b_phone_requirements", return_value=[]):
										with patch(f"{MOD}.frappe.db.exists", return_value=False):
											data = qe.get_quick_edit_data("CUST-0003")

		self.assertTrue(data["is_company"])
		self.assertFalse(data["is_b2b"])


class TestSaveQuickEditData(FrappeTestCase):
	def test_phone_only_update_does_not_touch_address_or_customer(self):
		with patch(f"{MOD}.frappe.has_permission"):
			with patch.object(qe, "_save_contact_phones") as save_phones:
				with patch.object(qe, "_save_address") as save_address:
					with patch(f"{MOD}.frappe.get_doc") as get_doc:
						result = qe.save_quick_edit_data("CUST-0001", {"phone_1": "33445566"})

		save_phones.assert_called_once_with("CUST-0001", "33445566", "")
		save_address.assert_not_called()
		get_doc.assert_not_called()
		self.assertEqual(result["warnings"], [])

	def test_cr_vat_update_saves_the_customer(self):
		customer_doc = StubDoc("Customer", name="CUST-0001")
		with patch(f"{MOD}.frappe.has_permission"):
			with patch.object(qe, "_save_contact_phones") as save_phones:
				with patch.object(qe, "_save_address") as save_address:
					with patch(f"{MOD}.frappe.get_doc", return_value=customer_doc):
						result = qe.save_quick_edit_data(
							"CUST-0001",
							{"custom_commercial_registration_number": "CR-123", "custom_vat_registration_number": "VAT-456"},
						)

		save_phones.assert_not_called()
		save_address.assert_not_called()
		self.assertEqual(customer_doc.custom_commercial_registration_number, "CR-123")
		self.assertEqual(customer_doc.custom_vat_registration_number, "VAT-456")
		self.assertEqual(result["warnings"], [])

	def test_a_still_incomplete_cr_vat_save_is_downgraded_to_a_warning(self):
		"""A ValidationError from the Customer's own save() must not wipe out a phone/address fix
		made in the same call -- Frappe rolls back the WHOLE request on an uncaught exception."""
		customer_doc = StubDoc("Customer", name="CUST-0001")
		customer_doc.save = MagicMock(side_effect=frappe.ValidationError("Customer CUST-0001 is missing required field(s): VAT Registration Number"))

		with patch(f"{MOD}.frappe.has_permission"):
			with patch.object(qe, "_save_contact_phones") as save_phones:
				with patch(f"{MOD}.frappe.get_doc", return_value=customer_doc):
					result = qe.save_quick_edit_data(
						"CUST-0001",
						{"phone_1": "33445566", "custom_commercial_registration_number": "CR-123"},
					)

		save_phones.assert_called_once_with("CUST-0001", "33445566", "")
		self.assertTrue(result["saved"])
		self.assertEqual(len(result["warnings"]), 1)

	def test_no_values_at_all_is_a_no_op(self):
		with patch(f"{MOD}.frappe.has_permission"):
			with patch.object(qe, "_save_contact_phones") as save_phones:
				with patch.object(qe, "_save_address") as save_address:
					with patch(f"{MOD}.frappe.get_doc") as get_doc:
						qe.save_quick_edit_data("CUST-0001", {})
		save_phones.assert_not_called()
		save_address.assert_not_called()
		get_doc.assert_not_called()


class TestSaveContactPhones(FrappeTestCase):
	def test_creates_a_new_contact_when_none_exists(self):
		new_contact = StubDoc("Contact")
		with patch.object(qe, "_primary_contact", return_value=None):
			with patch(f"{MOD}.frappe.new_doc", return_value=new_contact):
				with patch(f"{MOD}.frappe.db.get_value", side_effect=["Acme", None]):
					with patch(f"{MOD}.frappe.db.set_value") as set_value:
						qe._save_contact_phones("CUST-0001", "33445566", "")

		self.assertEqual(new_contact.first_name, "Acme")
		self.assertEqual(new_contact._appended["links"], [{"link_doctype": "Customer", "link_name": "CUST-0001"}])
		self.assertEqual(len(new_contact._appended["phone_nos"]), 1)
		self.assertEqual(new_contact._appended["phone_nos"][0]["phone"], "33445566")
		self.assertEqual(new_contact._appended["phone_nos"][0]["is_primary_phone"], 1)
		set_value.assert_called_once_with("Customer", "CUST-0001", "customer_primary_contact", new_contact.name)

	def test_updates_an_existing_contact_with_two_numbers(self):
		existing_contact = StubDoc("Contact", name="CONTACT-0001")
		with patch.object(qe, "_primary_contact", return_value="CONTACT-0001"):
			with patch(f"{MOD}.frappe.get_doc", return_value=existing_contact):
				with patch(f"{MOD}.frappe.db.get_value", return_value="CONTACT-0001"):
					qe._save_contact_phones("CUST-0001", "33445566", "17001122")

		numbers = [row["phone"] for row in existing_contact._appended["phone_nos"]]
		self.assertEqual(numbers, ["33445566", "17001122"])
		primaries = [row["is_primary_phone"] for row in existing_contact._appended["phone_nos"]]
		self.assertEqual(primaries, [1, 0])


class TestSaveAddress(FrappeTestCase):
	def test_creates_a_new_address_with_bahrain_default_country(self):
		new_address = StubDoc("Address")
		with patch.object(qe, "_primary_address", return_value=None):
			with patch(f"{MOD}.frappe.new_doc", return_value=new_address):
				with patch(f"{MOD}.frappe.db.get_value", side_effect=["Acme", None]):
					with patch(f"{MOD}.frappe.db.set_value") as set_value:
						qe._save_address("CUST-0001", "Building 1, Road 2", "Manama")

		self.assertEqual(new_address.address_title, "Acme")
		self.assertEqual(new_address.address_type, "Billing")
		self.assertEqual(new_address.country, "Bahrain")
		self.assertEqual(new_address.address_line1, "Building 1, Road 2")
		self.assertEqual(new_address.city, "Manama")
		set_value.assert_called_once_with("Customer", "CUST-0001", "customer_primary_address", new_address.name)
