# sf_trading/tests/test_supplier_exempt_groups.py
"""Suppliers in the Govt and W/O VAT Supplier Groups are not asked for Payment Terms, a document or a Tax ID.

Client, 2026-10-18: "Customs Declaration- Ministry Of Interior" could not be paid -- "Supplier Details Incomplete:
Payment Terms, a supporting document (Company supplier)". A ministry has neither, and a supplier without VAT has no
VAT certificate or Tax ID. The database is patched; nothing is created:

    bench --site <scratch-site> run-tests --module sf_trading.tests.test_supplier_exempt_groups
"""

import datetime
import importlib.util
import os
import re
from unittest.mock import MagicMock, patch

import frappe
from frappe.tests.utils import FrappeTestCase

from sf_trading import party_documents as pd
from sf_trading import supplier_validation as sv

ROOT = os.path.dirname(os.path.dirname(__file__))
RULES_FROM = datetime.datetime(2026, 10, 11, 8, 0, 0)


class Supplier(frappe._dict):
	"""Enough of a Supplier for the rules: get, attribute access, is_new, flags."""

	def __init__(self, group=None, new=False, created=datetime.datetime(2026, 7, 18), **values):
		values.setdefault("supplier_type", "Company")
		super().__init__(doctype="Supplier", name="S-1", supplier_name="Customs Declaration- Ministry Of Interior",
			supplier_group=group, creation=created, **values)
		self.flags = frappe._dict()
		self._new = new
		self[pd.TABLE] = []

	def is_new(self):
		return self._new

	def has_value_changed(self, fieldname):
		return False


class TestTheGroups(FrappeTestCase):
	def test_govt_and_wo_vat_are_matched_loosely(self):
		for group in ("Govt", "  govt ", "GOVT", "W/O VAT", "w/o vat", " W/O VAT"):
			self.assertTrue(pd.completeness_exempt_group(group), group)

	def test_other_groups_are_not(self):
		for group in ("Other Supplier", "Distributor", "Government", "W/O", "VAT", "", None):
			self.assertFalse(pd.completeness_exempt_group(group), group)

	def test_only_a_supplier_can_be_exempt(self):
		self.assertTrue(pd.completeness_exempt(Supplier("Govt")))
		customer = frappe._dict(doctype="Customer", supplier_group="Govt")
		self.assertFalse(pd.completeness_exempt(customer))

	def test_the_document_exemption_covers_all_three_groups(self):
		for group in ("Other Supplier", "Govt", "W/O VAT"):
			self.assertTrue(pd.document_exempt_group(group), group)
		self.assertFalse(pd.document_exempt_group("Distributor"))

	def test_other_supplier_keeps_its_old_meaning(self):
		# Payment Terms and the Tax ID were never waived for it
		self.assertFalse(pd.completeness_exempt_group("Other Supplier"))


class TestTheMasterRules(FrappeTestCase):
	def test_a_new_govt_master_is_not_asked_for_terms_or_a_document_but_still_for_contact_details(self):
		with patch.object(pd, "has_mobile", return_value=False), patch.object(pd, "has_email", return_value=False):
			missing = pd.missing_details(Supplier("Govt", new=False), after_insert=True)
		self.assertNotIn("Payment Terms", missing)
		self.assertFalse(any("Supporting Document" in item for item in missing))
		self.assertEqual(sorted(missing), ["Email", "Mobile Number"])

	def test_the_same_master_in_another_group_is_asked_for_everything(self):
		with patch.object(pd, "has_mobile", return_value=False), patch.object(pd, "has_email", return_value=False):
			missing = pd.missing_details(Supplier("Distributor"), after_insert=True)
		self.assertEqual(len(missing), 4)
		self.assertIn("Payment Terms", missing)

	def test_a_complete_govt_master_is_missing_nothing(self):
		with patch.object(pd, "has_mobile", return_value=True), patch.object(pd, "has_email", return_value=True):
			self.assertEqual(pd.missing_details(Supplier("W/O VAT"), after_insert=True), [])

	def test_payment_terms_are_not_asked_of_an_old_exempt_supplier(self):
		with patch.object(pd, "rules_from", return_value=RULES_FROM):
			self.assertEqual(pd.missing_payment_terms(Supplier("Govt")), [])
			self.assertEqual(pd.missing_payment_terms(Supplier("W/O VAT")), [])
			self.assertEqual(len(pd.missing_payment_terms(Supplier("Distributor"))), 1)
			self.assertEqual(pd.missing_payment_terms(Supplier("Distributor", payment_terms="Net 30")), [])

	def test_a_company_supplier_needs_no_document_in_the_two_groups(self):
		for group in ("Govt", "W/O VAT"):
			supplier = Supplier(group)
			self.assertFalse(pd.needs_document(supplier), group)
			self.assertEqual(pd.missing_document(supplier), [], group)
		with patch.object(frappe.db, "exists", return_value=False, create=True):
			self.assertEqual(len(pd.missing_document(Supplier("Distributor"))), 1)

	def test_a_company_supplier_needs_no_tax_id_in_the_two_groups(self):
		pd.validate_supplier_tax_id(Supplier("Govt"))
		pd.validate_supplier_tax_id(Supplier("W/O VAT"))
		with self.assertRaises(frappe.ValidationError):
			pd.validate_supplier_tax_id(Supplier("Distributor"))
		with self.assertRaises(frappe.ValidationError):
			pd.validate_supplier_tax_id(Supplier(None))

	def test_a_tax_id_given_is_still_accepted(self):
		pd.validate_supplier_tax_id(Supplier("Govt", tax_id="300000000000003"))

	def test_the_form_is_told_which_groups_are_exempt(self):
		with patch.object(pd, "rules_from", return_value=RULES_FROM), patch.object(frappe.db, "exists", return_value=True, create=True), patch.object(
			frappe.db, "get_value", return_value=None, create=True
		), patch.object(frappe, "has_permission", return_value=True, create=True), patch.object(
			frappe, "get_doc", return_value=Supplier("Govt"), create=True
		):
			new_form = pd.get_master_rules("Supplier")
			named = pd.get_master_rules("Supplier", "S-1")
		for rules in (new_form, named):
			self.assertEqual(rules["completeness_exempt_groups"], ["Govt", "W/O VAT"])
			self.assertEqual(rules["exempt_groups"], ["Other Supplier", "Govt", "W/O VAT"])
		# the named supplier is in Govt, so Payment Terms are not required of it
		self.assertFalse(named["payment_terms_required"])
		self.assertTrue(new_form["payment_terms_required"])


class TestTheGateOnADocument(FrappeTestCase):
	"""The Payment Entry in the screenshot: a Company supplier with no Payment Terms and no document."""

	def pay(self, group):
		doc = frappe._dict(doctype="Payment Entry", party_type="Supplier", party="S-1", payment_type="Pay", is_return=0, is_opening="No")
		master = Supplier(group)
		# frappe.db.exists answers True for the party itself and False for the File check
		with patch.object(pd, "rules_from", return_value=RULES_FROM), patch.object(
			frappe.db, "exists", side_effect=lambda doctype, *a, **k: doctype == "Supplier", create=True
		), patch.object(frappe, "get_doc", return_value=master, create=True), patch.object(pd, "expired_financial_rows", return_value=[]):
			pd.validate_party_at_transaction(doc)

	def test_a_govt_supplier_can_be_paid(self):
		self.pay("Govt")

	def test_a_wo_vat_supplier_can_be_paid(self):
		self.pay("W/O VAT")

	def test_a_supplier_in_no_exempt_group_is_still_refused(self):
		with self.assertRaises(frappe.ValidationError) as caught:
			self.pay(None)
		text = str(caught.exception)
		self.assertIn("Payment Terms", text)
		self.assertIn("supporting document", text)

	def test_a_distributor_is_still_refused(self):
		with self.assertRaises(frappe.ValidationError):
			self.pay("Distributor")


class TestSupplierValidation(FrappeTestCase):
	"""supplier_validation.py: the Tax ID and document rules (switched on in SF Trading Settings)."""

	def test_a_govt_supplier_is_saved_without_a_tax_id(self):
		with patch.object(sv, "enforcement_enabled", return_value=True), patch.object(sv, "only_status_fields_changed", return_value=False):
			sv.validate(Supplier("Govt", new=True, tax_id=None))
			sv.validate(Supplier("W/O VAT", new=True, tax_id=None))

	def test_any_other_company_supplier_still_needs_one(self):
		with patch.object(sv, "enforcement_enabled", return_value=True), patch.object(sv, "only_status_fields_changed", return_value=False):
			with self.assertRaises(frappe.ValidationError):
				sv.validate(Supplier("Distributor", new=True, tax_id=None))

	def test_the_gap_list_for_a_govt_supplier_has_no_tax_id_or_document(self):
		row = ("Company", None, None, None, "Govt")
		with patch.object(frappe.db, "get_value", return_value=row, create=True), patch.object(
			sv, "party_phone_numbers", return_value=[]
		), patch.object(sv, "party_email_addresses", return_value=[]), patch.object(sv, "has_attachment", return_value=False):
			missing = sv.missing_supplier_details("S-1")
		self.assertEqual(sorted(str(item) for item in missing), ["Email", "Phone Number"])

	def test_the_gap_list_for_any_other_supplier_still_has_all_four(self):
		row = ("Company", None, None, None, "Distributor")
		with patch.object(frappe.db, "get_value", return_value=row, create=True), patch.object(
			sv, "party_phone_numbers", return_value=[]
		), patch.object(sv, "party_email_addresses", return_value=[]), patch.object(sv, "has_attachment", return_value=False):
			missing = sv.missing_supplier_details("S-1")
		self.assertEqual(len(missing), 4)


class TestTheMirrorsStayInStep(FrappeTestCase):
	"""The browser has no round trip to read these from, so its copies must say what the server says."""

	def read(self, *parts):
		with open(os.path.join(ROOT, *parts), encoding="utf-8") as handle:
			return handle.read()

	def js_list(self, source, name):
		match = re.search(r"window\." + name + r"\s*=\s*\[([^\]]*)\]", source)
		self.assertTrue(match, name)
		return [item.strip().strip('"') for item in match.group(1).split(",")]

	def test_the_quick_entry_lists_equal_the_server_lists(self):
		source = self.read("public", "js", "party_quick_entry.js")
		self.assertEqual(
			self.js_list(source, "SF_COMPLETENESS_EXEMPT_SUPPLIER_GROUPS"),
			[g.lower() for g in pd.COMPLETENESS_EXEMPT_SUPPLIER_GROUPS],
		)
		self.assertEqual(
			self.js_list(source, "SF_DOCUMENT_EXEMPT_SUPPLIER_GROUPS"),
			[g.lower() for g in pd.DOCUMENT_EXEMPT_SUPPLIER_GROUPS + pd.COMPLETENESS_EXEMPT_SUPPLIER_GROUPS],
		)

	def test_the_tax_id_mark_on_the_form_skips_both_groups(self):
		import json

		rows = {row["name"]: row for row in json.loads(self.read("fixtures", "property_setter.json"))}
		value = rows["Supplier-tax_id-mandatory_depends_on"]["value"]
		self.assertIn("supplier_type=='Company'", value)
		for group in pd.COMPLETENESS_EXEMPT_SUPPLIER_GROUPS:
			self.assertIn("'" + group.lower() + "'", value)


def load_patch():
	path = os.path.join(ROOT, "patches", "v0_1", "seed_exempt_supplier_groups.py")
	spec = importlib.util.spec_from_file_location("sf_trading_test_seed_exempt_supplier_groups", path)
	module = importlib.util.module_from_spec(spec)
	spec.loader.exec_module(module)
	return module


class TestTheGroupsAreMade(FrappeTestCase):
	def run_patch(self, existing, parent_exists=True, doctype_exists=True):
		module = load_patch()
		made = []

		def exists(doctype, name=None, *a, **k):
			if doctype == "DocType":
				return doctype_exists
			if name == module.PARENT:
				return parent_exists
			return name in existing

		def make(values):
			doc = MagicMock()
			doc.insert.side_effect = lambda **k: made.append(values)
			return doc

		with patch.object(frappe.db, "exists", side_effect=exists, create=True), patch.object(
			frappe, "get_doc", side_effect=make, create=True
		), patch.object(frappe.db, "commit", create=True):
			module.execute()
		return made

	def test_both_groups_are_made_under_all_supplier_groups(self):
		made = self.run_patch(existing=set())
		self.assertEqual([m["supplier_group_name"] for m in made], ["Govt", "W/O VAT"])
		self.assertTrue(all(m["parent_supplier_group"] == "All Supplier Groups" and m["is_group"] == 0 for m in made))

	def test_a_group_that_exists_is_left_alone(self):
		made = self.run_patch(existing={"Govt"})
		self.assertEqual([m["supplier_group_name"] for m in made], ["W/O VAT"])
		self.assertEqual(self.run_patch(existing={"Govt", "W/O VAT"}), [])

	def test_nothing_is_made_without_the_parent_group(self):
		self.assertEqual(self.run_patch(existing=set(), parent_exists=False), [])

	def test_the_names_are_the_ones_the_rules_look_for(self):
		module = load_patch()
		self.assertEqual(tuple(module.GROUPS), pd.COMPLETENESS_EXEMPT_SUPPLIER_GROUPS)
