"""Tests for the Create Customer / Supplier rules on the master itself (party_master_rules.py).

    bench --site <scratch-site> run-tests --module sf_trading.tests.test_party_master_rules

Documents are stand-ins and the database reads are patched: the rules are about what a save
refuses, not about what a site happens to hold.
"""

from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase

from sf_trading import party_master_rules as rules

GOOD_VAT = "300000000000003"
DB_VALUE = "sf_trading.party_master_rules.frappe.db.get_value"
SAUDI = "sf_trading.party_master_rules.is_saudi"
STANDS_ASIDE = "sf_trading.party_master_rules.stands_aside"
STATUS_ONLY = "sf_trading.party_master_rules.only_status_fields_changed"


class Doc(frappe._dict):
	"""Just enough of a Document for the rules."""

	def __init__(self, doctype, new=True, changed=(), before=None, **values):
		super().__init__(doctype=doctype, **values)
		self.flags = frappe._dict()
		self._new = new
		self._changed = set(changed)
		self._before = before

	def is_new(self):
		return self._new

	def has_value_changed(self, fieldname):
		return fieldname in self._changed

	def get_doc_before_save(self):
		return self._before


def customer(**values):
	values.setdefault("custom_vat_registration_number", GOOD_VAT)
	return Doc("Customer", **values)


def supplier(**values):
	values.setdefault("tax_id", GOOD_VAT)
	return Doc("Supplier", **values)


class TestVatFormat(FrappeTestCase):
	def test_saudi_party_with_a_malformed_number_is_refused(self):
		for bad in ("12345", "200000000000002", "30000000000003", "3000000000000033", "30000000000000A"):
			with patch(SAUDI, return_value=True), self.assertRaises(frappe.ValidationError, msg=bad):
				rules.validate_vat_format(customer(custom_vat_registration_number=bad))

	def test_a_well_formed_number_passes(self):
		with patch(SAUDI, return_value=True):
			rules.validate_vat_format(customer())
			rules.validate_vat_format(supplier())

	def test_a_party_outside_saudi_arabia_is_not_held_to_the_format(self):
		with patch(SAUDI, return_value=False):
			rules.validate_vat_format(customer(custom_vat_registration_number="12345"))

	def test_an_old_number_that_did_not_change_is_left_alone(self):
		doc = customer(new=False, custom_vat_registration_number="12345")
		with patch(SAUDI, return_value=True):
			rules.validate_vat_format(doc)

	def test_a_changed_number_on_an_old_party_is_checked(self):
		doc = customer(new=False, changed=("custom_vat_registration_number",),
			custom_vat_registration_number="12345")
		with patch(SAUDI, return_value=True), self.assertRaises(frappe.ValidationError):
			rules.validate_vat_format(doc)

	def test_no_number_is_not_this_rules_business(self):
		with patch(SAUDI, return_value=True):
			rules.validate_vat_format(customer(custom_vat_registration_number=""))
			rules.validate_vat_format(customer(custom_vat_registration_number="0"))


class TestUniqueVat(FrappeTestCase):
	def test_a_number_held_by_another_party_is_refused(self):
		with patch(DB_VALUE, return_value="CUST-0001"), self.assertRaises(frappe.ValidationError):
			rules.validate_unique_vat(customer())
		with patch(DB_VALUE, return_value="SUP-0001"), self.assertRaises(frappe.ValidationError):
			rules.validate_unique_vat(supplier())

	def test_it_looks_in_the_partys_own_field_and_skips_itself(self):
		with patch(DB_VALUE, return_value=None) as get_value:
			rules.validate_unique_vat(customer(name="CUST-0002"))
		doctype, filters, field = get_value.call_args[0]
		self.assertEqual(doctype, "Customer")
		self.assertEqual(filters, {"custom_vat_registration_number": GOOD_VAT, "name": ("!=", "CUST-0002")})
		self.assertEqual(field, "name")

		with patch(DB_VALUE, return_value=None) as get_value:
			rules.validate_unique_vat(supplier())
		self.assertEqual(get_value.call_args[0][1]["tax_id"], GOOD_VAT)

	def test_the_dialogs_manager_override_lets_it_through(self):
		for flag in ("allow_duplicate_vat", "allow_duplicate_tax_id"):
			doc = customer()
			doc.flags[flag] = True
			with patch(DB_VALUE, return_value="CUST-0001") as get_value:
				rules.validate_unique_vat(doc)
			get_value.assert_not_called()

	def test_a_pair_that_already_exists_does_not_block_an_unrelated_edit(self):
		with patch(DB_VALUE, return_value="CUST-0001") as get_value:
			rules.validate_unique_vat(customer(new=False))
		get_value.assert_not_called()

	def test_no_number_means_no_lookup(self):
		with patch(DB_VALUE, return_value="CUST-0001") as get_value:
			rules.validate_unique_vat(customer(custom_vat_registration_number=""))
		get_value.assert_not_called()


class TestUniqueCr(FrappeTestCase):
	def test_a_b2b_customer_with_a_cr_held_by_another_is_refused(self):
		with patch(DB_VALUE, return_value="CUST-0001"), self.assertRaises(frappe.ValidationError):
			rules.validate_unique_cr(customer(custom_commercial_registration_number="CR-1"))

	def test_it_looks_among_enabled_customers_and_skips_itself(self):
		with patch(DB_VALUE, return_value=None) as get_value:
			rules.validate_unique_cr(customer(name="CUST-0002", custom_commercial_registration_number=" CR-1 "))
		self.assertEqual(get_value.call_args[0][1],
			{"custom_commercial_registration_number": "CR-1", "disabled": 0, "name": ("!=", "CUST-0002")})

	def test_a_customer_without_a_vat_number_is_b2c_and_unchecked(self):
		with patch(DB_VALUE, return_value="CUST-0001") as get_value:
			rules.validate_unique_cr(customer(custom_vat_registration_number="",
				custom_commercial_registration_number="CR-1"))
		get_value.assert_not_called()

	def test_a_supplier_has_no_cr(self):
		with patch(DB_VALUE, return_value="SUP-0001") as get_value:
			rules.validate_unique_cr(supplier(custom_commercial_registration_number="CR-1"))
		get_value.assert_not_called()

	def test_the_dialogs_manager_override_lets_it_through(self):
		doc = customer(custom_commercial_registration_number="CR-1")
		doc.flags.allow_duplicate_vat = True
		with patch(DB_VALUE, return_value="CUST-0001") as get_value:
			rules.validate_unique_cr(doc)
		get_value.assert_not_called()

	def test_an_old_cr_that_did_not_change_is_left_alone(self):
		with patch(DB_VALUE, return_value="CUST-0001") as get_value:
			rules.validate_unique_cr(customer(new=False, custom_commercial_registration_number="CR-1"))
			rules.validate_unique_cr(customer(custom_commercial_registration_number=""))
		get_value.assert_not_called()

	def test_a_changed_cr_on_an_old_customer_is_checked(self):
		doc = customer(new=False, changed=("custom_commercial_registration_number",),
			custom_commercial_registration_number="CR-1")
		with patch(DB_VALUE, return_value="CUST-0001"), self.assertRaises(frappe.ValidationError):
			rules.validate_unique_cr(doc)


class TestMobile(FrappeTestCase):
	def test_a_short_saudi_mobile_is_refused(self):
		with patch(SAUDI, return_value=True), self.assertRaises(frappe.ValidationError):
			rules.validate_mobile(customer(mobile_no="055 123"))

	def test_ten_digits_pass_however_they_are_written(self):
		for number in ("0551234567", "+966 55 123 4567", "(055) 123-4567"):
			with patch(SAUDI, return_value=True):
				rules.validate_mobile(customer(mobile_no=number))

	def test_a_supplier_quick_entry_number_is_read_from_the_cached_field(self):
		with patch(SAUDI, return_value=True), self.assertRaises(frappe.ValidationError):
			rules.validate_mobile(supplier(custom_mobile_no="12345"))

	def test_outside_saudi_arabia_any_number_passes(self):
		with patch(SAUDI, return_value=False):
			rules.validate_mobile(customer(mobile_no="33445566"))

	def test_an_unchanged_old_number_is_left_alone(self):
		with patch(SAUDI, return_value=True):
			rules.validate_mobile(customer(new=False, mobile_no="12345"))


class TestMasterValidate(FrappeTestCase):
	def test_all_three_rules_run(self):
		with patch(SAUDI, return_value=True), patch(DB_VALUE, return_value=None):
			with self.assertRaises(frappe.ValidationError):
				rules.validate_master(customer(custom_vat_registration_number="12345", mobile_no="0551234567"))
			with self.assertRaises(frappe.ValidationError):
				rules.validate_master(customer(mobile_no="123"))
			rules.validate_master(customer(mobile_no="0551234567"))

	def test_a_frozen_or_disabled_party_is_exempt(self):
		with patch(SAUDI, return_value=True), patch(STATUS_ONLY, return_value=True):
			rules.validate_master(customer(custom_vat_registration_number="12345"))

	def test_an_import_or_patch_stands_aside(self):
		with patch(SAUDI, return_value=True), patch(STANDS_ASIDE, return_value=True):
			rules.validate_master(customer(custom_vat_registration_number="12345"))


class TestContactPhones(FrappeTestCase):
	def contact(self, phones, before=None, parties=(("Customer", "CUST-0001"),)):
		doc = Doc("Contact", before=before)
		doc["links"] = [frappe._dict(link_doctype=dt, link_name=name) for dt, name in parties]
		doc["phone_nos"] = [frappe._dict(phone=p) for p in phones]
		return doc

	def test_a_short_new_phone_on_a_saudi_partys_contact_is_refused(self):
		with patch(SAUDI, return_value=True), patch(DB_VALUE, return_value=None):
			with self.assertRaises(frappe.ValidationError):
				rules.validate_contact_phones(self.contact(["12345"]))
			rules.validate_contact_phones(self.contact(["0551234567"]))

	def test_a_phone_the_contact_already_held_is_left_alone(self):
		before = self.contact(["12345"])
		with patch(SAUDI, return_value=True), patch(DB_VALUE, return_value=None):
			rules.validate_contact_phones(self.contact(["12345"], before=before))
			with self.assertRaises(frappe.ValidationError):
				rules.validate_contact_phones(self.contact(["12345", "999"], before=before))

	def test_a_contact_of_no_party_or_a_non_saudi_party_is_left_alone(self):
		with patch(SAUDI, return_value=True):
			rules.validate_contact_phones(self.contact(["12345"], parties=()))
			rules.validate_contact_phones(self.contact(["12345"], parties=(("Lead", "LEAD-0001"),)))
		with patch(SAUDI, return_value=False), patch(DB_VALUE, return_value=None):
			rules.validate_contact_phones(self.contact(["12345"]))


class TestB2bAddress(FrappeTestCase):
	FULL = dict(address_line1="1 King Road", custom_building_number="1234", custom_area="Olaya",
		city="Riyadh", pincode="12345")

	def address(self, new=True, changed=(), **values):
		doc = Doc("Address", new=new, changed=changed, **{**self.FULL, **values})
		doc["links"] = [frappe._dict(link_doctype="Customer", link_name="CUST-0001")]
		return doc

	def b2b(self, vat=GOOD_VAT):
		return patch(DB_VALUE, side_effect=lambda dt, name, field=None: vat if field else None)

	def test_a_complete_address_passes(self):
		with patch(SAUDI, return_value=True), self.b2b():
			rules.validate_b2b_address(self.address())

	def test_each_missing_part_is_refused(self):
		for field in self.FULL:
			with patch(SAUDI, return_value=True), self.b2b(), self.assertRaises(frappe.ValidationError, msg=field):
				rules.validate_b2b_address(self.address(**{field: ""}))

	def test_the_postal_code_is_five_digits(self):
		for bad in ("1234", "123456"):
			with patch(SAUDI, return_value=True), self.b2b(), self.assertRaises(frappe.ValidationError, msg=bad):
				rules.validate_b2b_address(self.address(pincode=bad))

	def test_a_party_without_a_vat_number_is_b2c_and_unchecked(self):
		with patch(SAUDI, return_value=True), self.b2b(vat=""):
			rules.validate_b2b_address(self.address(address_line1=""))

	def test_outside_saudi_arabia_it_is_unchecked(self):
		with patch(SAUDI, return_value=False), self.b2b():
			rules.validate_b2b_address(self.address(address_line1=""))

	def test_an_old_address_is_only_checked_once_a_part_of_it_changes(self):
		with patch(SAUDI, return_value=True), self.b2b():
			rules.validate_b2b_address(self.address(new=False, address_line1=""))
			with self.assertRaises(frappe.ValidationError):
				rules.validate_b2b_address(self.address(new=False, changed=("address_line1",), address_line1=""))


class TestOverrideRoles(FrappeTestCase):
	def test_only_the_named_managers_may_override(self):
		with patch("sf_trading.party_master_rules.frappe.get_roles", return_value=["Sales Manager"]):
			self.assertTrue(rules.can_override_duplicate("Customer", "u@x.com"))
			self.assertFalse(rules.can_override_duplicate("Supplier", "u@x.com"))
		with patch("sf_trading.party_master_rules.frappe.get_roles", return_value=["Sales User"]):
			self.assertFalse(rules.can_override_duplicate("Customer", "u@x.com"))
		self.assertTrue(rules.can_override_duplicate("Customer", "Administrator"))
