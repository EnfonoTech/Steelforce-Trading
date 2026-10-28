# sf_trading/tests/test_party_rules.py
"""The Party Master Rule Set engine (sf_trading/party_rules.py) and the rows it is seeded with.

    bench --site <site> run-tests --module sf_trading.tests.test_party_rules

Never run on a site whose Item Prices matter (run-tests wipes them): the engine is also checked against the
code's own approval gate, on real data, by the Party Rule Set Diff report.
"""

from datetime import datetime
from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase

from sf_trading import party_rules as pr
from sf_trading import party_rules_seed as seed

START = datetime(2026, 10, 1, 0, 0)
OLD = datetime(2026, 1, 1, 0, 0)
NEW = datetime(2026, 11, 1, 0, 0)
SAUDI = "saudi arabia"


class Party(frappe._dict):
	"""Enough of a Customer / Supplier for the engine."""

	def __init__(self, doctype, new=False, changed=(), **values):
		super().__init__(doctype=doctype, name="P-1", creation=values.pop("creation", NEW), **values)
		self.flags = frappe._dict()
		self._new = new
		self._changed = set(changed)

	def is_new(self):
		return self._new

	def has_value_changed(self, fieldname):
		return fieldname in self._changed


class Facts:
	"""What the party has, said outright; nothing is read from a database."""

	payment_terms = mobile = email = address = document_rows = document_any = file_any = False
	phones = 0
	tax_id = cr = ""
	expired = ()
	is_b2b = is_credit = False
	country = ""

	def __init__(self, **values):
		self.__dict__.update(values)


def rules(party_type):
	"""The seed, as a rule set document would hand it to the engine."""
	build = seed.SETS[party_type]()
	return frappe._dict(
		applies_from=START, enabled=1,
		required=[frappe._dict(r) for r in build["required"]],
		formats=[frappe._dict(r) for r in build["formats"]],
		uniques=[frappe._dict(dict(r, override_roles=[frappe._dict(x) for x in r["override_roles"]])) for r in build["uniques"]],
		exemptions=[frappe._dict(r) for r in build["exemptions"]],
	)


def texts(doc, stage, facts, party_type=None):
	return [f.text for f in pr.evaluate(doc, stage, rules(party_type or doc.doctype), facts)]


class Approval(FrappeTestCase):
	"""The approval stage, the one the diff report holds to the code on real data."""

	def credit_customer(self, **facts):
		return (Party("Customer", creation=OLD),
			Facts(is_b2b=True, is_credit=True, tax_id="300000000000003", country=SAUDI, **facts))

	def test_a_credit_customer_lacking_everything_is_told_in_the_codes_order(self):
		doc, facts = self.credit_customer()
		self.assertEqual(texts(doc, "approval", facts), [
			"Commercial Registration Number",
			"a Contact with a phone number",
			"Payment Terms",
			"a Supporting Document with its file attached",
			"at least 2 contact numbers (found 0)",
			"an email address",
		])

	def test_a_document_row_with_no_file_attached_to_the_customer_asks_for_the_attachment(self):
		doc, facts = self.credit_customer(cr="1010", phones=2, email=True, payment_terms=True, document_rows=True,
			document_any=True)
		self.assertEqual(texts(doc, "approval", facts), ["at least one attachment"])

	def test_a_complete_credit_customer_lacks_nothing(self):
		doc, facts = self.credit_customer(cr="1010", phones=2, email=True, payment_terms=True, document_rows=True,
			file_any=True, document_any=True)
		self.assertEqual(texts(doc, "approval", facts), [])

	def test_an_expired_document_is_listed(self):
		doc, facts = self.credit_customer(cr="1010", phones=2, email=True, payment_terms=True, document_rows=True,
			file_any=True, document_any=True, expired=["VAT Certificate 3 expired on 01-01-2026"])
		self.assertEqual(texts(doc, "approval", facts), ["VAT Certificate 3 expired on 01-01-2026"])

	def test_a_customer_with_no_credit_limit_is_asked_for_a_phone_only(self):
		doc = Party("Customer", creation=OLD)
		self.assertEqual(texts(doc, "approval", Facts(country=SAUDI)), ["a Contact with a phone number"])

	def test_a_supplier_in_the_govt_group_is_not_asked_for_terms_a_document_or_a_tax_id(self):
		doc = Party("Supplier", creation=OLD, supplier_group="Govt")
		facts = Facts(is_b2b=True, country=SAUDI)
		self.assertEqual(texts(doc, "approval", facts), ["Mobile Number", "Email", "an Address"])

	def test_an_ordinary_company_supplier_is_asked_for_all_of_it(self):
		doc = Party("Supplier", creation=OLD, supplier_group="Distributor")
		facts = Facts(is_b2b=True, country=SAUDI)
		self.assertEqual(texts(doc, "approval", facts), ["Payment Terms", "a Supporting Document with its file attached",
			"Mobile Number", "Email", "an Address", "Tax ID"])

	def test_other_supplier_holds_no_document_but_still_terms(self):
		doc = Party("Supplier", creation=OLD, supplier_group="Other Supplier")
		self.assertEqual(texts(doc, "approval", Facts(country=SAUDI)), ["Payment Terms", "Mobile Number", "Email"])

	def test_an_individual_supplier_is_not_asked_for_an_address_or_a_tax_id(self):
		doc = Party("Supplier", creation=OLD, supplier_group="Distributor")
		self.assertNotIn("Tax ID", texts(doc, "approval", Facts(country=SAUDI)))
		self.assertNotIn("an Address", texts(doc, "approval", Facts(country=SAUDI)))


class Stages(FrappeTestCase):
	def test_a_first_save_is_not_asked_for_what_core_makes_after_it(self):
		doc = Party("Supplier", new=True, supplier_group="Distributor")
		found = texts(doc, "save", Facts(is_b2b=True, country=SAUDI))
		self.assertEqual(found, ["Payment Terms", "Tax ID"])

	def test_a_second_save_asks_for_them(self):
		doc = Party("Supplier", creation=NEW, supplier_group="Distributor")
		found = texts(doc, "save", Facts(is_b2b=True, country=SAUDI))
		self.assertEqual(found, ["Payment Terms", "a Supporting Document with its file attached", "Mobile Number",
			"Email", "an Address", "Tax ID"])

	def test_an_older_supplier_is_asked_for_terms_at_a_voucher_but_not_for_the_new_master_rows(self):
		doc = Party("Supplier", creation=OLD, supplier_group="Distributor")
		found = texts(doc, "voucher", Facts(is_b2b=True, country=SAUDI))
		self.assertEqual(found, ["Payment Terms", "a supporting document (Company supplier)"])

	def test_an_older_supplier_is_not_held_at_save_for_new_master_rows(self):
		doc = Party("Supplier", creation=OLD, supplier_group="Distributor")
		self.assertEqual(texts(doc, "save", Facts(is_b2b=True, country=SAUDI)), ["Tax ID"])

	def test_the_rules_are_off_while_the_set_has_no_applies_from(self):
		build = rules("Supplier")
		build.applies_from = None
		doc = Party("Supplier", creation=NEW, supplier_group="Distributor")
		found = [f.text for f in pr.evaluate(doc, "voucher", build, Facts(is_b2b=True, country=SAUDI))]
		self.assertEqual(found, ["Payment Terms", "a supporting document (Company supplier)"])

	def test_a_disabled_row_and_an_unticked_stage_are_not_asked(self):
		build = rules("Supplier")
		for row in build.required:
			row.enabled = 0
		self.assertEqual([f.text for f in pr.evaluate(Party("Supplier", creation=OLD), "approval", build, Facts())], [])

	def test_the_address_rows_are_not_asked_of_the_master(self):
		doc = Party("Customer", creation=NEW)
		found = texts(doc, "save", Facts(is_b2b=True, country=SAUDI, tax_id="3"))
		self.assertNotIn("Address Line 1", " ".join(found))


class Formats(FrappeTestCase):
	def test_the_vat_pattern(self):
		row = rules("Customer").formats[0]
		self.assertTrue(pr.passes(row, "300000000000003"))
		self.assertFalse(pr.passes(row, "30000000000003"))
		self.assertFalse(pr.passes(row, "100000000000003"))
		self.assertFalse(pr.passes(row, "300000000000001"))

	def test_digits_checks(self):
		minimum = frappe._dict(check="Minimum Digits", value="10")
		exact = frappe._dict(check="Exact Digits", value="5")
		self.assertTrue(pr.passes(minimum, "+966 50 123 4567"))
		self.assertFalse(pr.passes(minimum, "050123"))
		self.assertTrue(pr.passes(exact, "12345"))
		self.assertFalse(pr.passes(exact, "123456"))

	def test_prefix_and_suffix_checks(self):
		self.assertTrue(pr.passes(frappe._dict(check="Starts With", value="3"), "300"))
		self.assertFalse(pr.passes(frappe._dict(check="Ends With", value="3"), "301"))

	def test_a_new_or_changed_saudi_value_is_checked_an_old_one_is_left(self):
		build = rules("Customer")
		facts = Facts(country=SAUDI, is_b2b=True)
		with patch.object(pr, "_target", return_value=("30000", "custom_vat_registration_number")):
			new = Party("Customer", new=True)
			self.assertEqual(len([f for f in pr.format_failures(new, build, facts) if f.field == "VAT / Tax ID"]), 1)
			old = Party("Customer", creation=OLD)
			self.assertEqual([f for f in pr.format_failures(old, build, facts) if f.field == "VAT / Tax ID"], [])
			changed = Party("Customer", creation=OLD, changed=["custom_vat_registration_number"])
			self.assertEqual(len([f for f in pr.format_failures(changed, build, facts) if f.field == "VAT / Tax ID"]), 1)

	def test_a_company_outside_saudi_arabia_is_not_held_to_the_format(self):
		build = rules("Customer")
		with patch.object(pr, "_target", return_value=("30000", "custom_vat_registration_number")):
			self.assertEqual(pr.format_failures(Party("Customer", new=True), build, Facts(country="bahrain")), [])


class Uniques(FrappeTestCase):
	def test_a_clash_names_the_other_party_and_who_may_override(self):
		build = rules("Customer")
		doc = Party("Customer", new=True)
		with patch.object(pr, "_target", return_value=("300000000000003", "custom_vat_registration_number")), \
				patch("frappe.db.get_value", return_value="C-9"):
			found = pr.unique_failures(doc, build, Facts(is_b2b=True))
		clash = [f for f in found if f.field == "VAT / Tax ID"][0]
		self.assertEqual(clash.clash, "C-9")
		self.assertEqual(clash.override_roles, ["Sales Manager", "Sales Master Manager", "System Manager"])
		self.assertTrue(clash.needs_reason)

	def test_the_cr_check_is_for_b2b_customers_only(self):
		build = rules("Customer")
		doc = Party("Customer", new=True)
		with patch.object(pr, "_target", return_value=("1010", "custom_commercial_registration_number")), \
				patch("frappe.db.get_value", return_value="C-9"):
			found = pr.unique_failures(doc, build, Facts(is_b2b=False))
		self.assertEqual([f for f in found if f.field == "CR Number"], [])


class Seed(FrappeTestCase):
	def test_both_sets_use_only_requirements_the_doctype_offers(self):
		offered = set(frappe.get_meta("Party Rule Required").get_field("requirement").options.split("\n")) \
			if frappe.db.exists("DocType", "Party Rule Required") else None
		known = {pr.PAYMENT_TERMS, pr.MOBILE, pr.EMAIL, pr.ADDRESS, pr.ADDRESS_FIELD, pr.TAX_ID, pr.CR, pr.CONTACTS,
			pr.DOCUMENT_ROW, pr.DOCUMENT_ANY, pr.FILE_ANY, pr.EXPIRED, pr.ANY_FIELD}
		for party_type, build in seed.SETS.items():
			used = {r["requirement"] for r in build()["required"]}
			self.assertLessEqual(used, known, party_type)
			if offered:
				self.assertLessEqual(used, offered, party_type)

	def test_every_seeded_requirement_names_a_stage(self):
		for party_type, build in seed.SETS.items():
			for row in build()["required"]:
				self.assertTrue(row["at_save"] or row["at_approval"] or row["at_voucher"], (party_type, row["requirement"]))

	def test_the_exemptions_are_the_codes_groups(self):
		groups = {r["supplier_group"]: r for r in seed.supplier_set()["exemptions"]}
		self.assertEqual(set(groups), {"Govt", "W/O VAT", "Other Supplier"})
		self.assertEqual((groups["Other Supplier"]["document"], groups["Other Supplier"]["payment_terms"]), (1, 0))
		self.assertEqual(seed.customer_set()["exemptions"], [])

	def test_the_vat_pattern_is_the_codes(self):
		from sf_trading.party_master_rules import VAT_FORMAT

		self.assertEqual(seed.VAT_REGEX, VAT_FORMAT.pattern)
