"""Customer and Supplier names in Title Case, the ID too (sf_trading/title_case.py).

    bench --site <site> run-tests --module sf_trading.tests.test_title_case
"""

import importlib
from unittest.mock import patch

from frappe.tests.utils import FrappeTestCase

from sf_trading import title_case as tc


def resolve(path):
	"""The function a hook names, imported directly (no site needed)."""
	module, _, name = path.rpartition(".")
	return getattr(importlib.import_module(module), name)


class TestTheRule(FrappeTestCase):
	def check(self, text, expected):
		self.assertEqual(tc.title_case(text), expected, text)

	def test_the_names_that_reach_steel_force(self):
		self.check("THE DAILY BREAD BAKERY WLL", "The Daily Bread Bakery Wll")
		self.check("abdul rahman", "Abdul Rahman")
		self.check("ultra galaxy publicity & advertising", "Ultra Galaxy Publicity & Advertising")
		self.check("RUHEIBEH METAL FABRICATION W.L.L", "Ruheibeh Metal Fabrication W.L.L")
		self.check("ART DESIGN ADVARTISING AND PROMOTION CO WLL", "Art Design Advartising And Promotion Co Wll")

	def test_a_digit_or_a_mark_ends_a_word(self):
		self.check("AL-AWANI TRADING", "Al-Awani Trading")
		self.check("E2E TRADING", "E2E Trading")
		self.check("3m gulf", "3M Gulf")
		self.check("SHAS STEEL CO W.L.L - 33925725", "Shas Steel Co W.L.L - 33925725")
		self.check("ABDULRAHMAN ALI  CO WLL (000143)", "Abdulrahman Ali Co Wll (000143)")

	def test_runs_of_spaces_become_one_and_the_ends_are_trimmed(self):
		self.check("  Abdulla   Ali  Abdulla    Workshop ", "Abdulla Ali Abdulla Workshop")

	def test_an_apostrophe_between_letters_is_part_of_the_word(self):
		self.check("MA'AN TRADING & JOHN'S SONS", "Ma'an Trading & John's Sons")
		self.check("O'BRIEN", "O'brien")
		self.check("don’t stop", "Don’t Stop")
		self.check("ST. JOHN'S", "St. John's")

	def test_an_apostrophe_with_no_letter_after_it_ends_the_word(self):
		self.check("JOHNS' STEEL", "Johns' Steel")
		self.check("'AL SALAM", "'Al Salam")

	def test_a_letter_of_any_script_counts_so_nothing_is_split_or_lost(self):
		self.check("DÉCOR HOUSE", "Décor House")
		self.check("شركة الفلاح AL FALAH", "شركة الفلاح Al Falah")

	def test_nothing_in_nothing_out(self):
		self.check("", "")
		self.check(None, "")
		self.check("   ", "")

	def test_it_is_idempotent(self):
		for text in ("THE DAILY BREAD BAKERY WLL", "O'BRIEN 3m E2E", "Al-Awani  &  Sons", "شركة X"):
			once = tc.title_case(text)
			self.assertEqual(tc.title_case(once), once, text)


class Party:
	"""Enough of a Customer / Supplier for a hook: a doctype and the fields it carries."""

	def __init__(self, doctype, **values):
		self.doctype = doctype
		self.__dict__.update(values)

	def get(self, field):
		return self.__dict__.get(field)


class TestTheSwitch(FrappeTestCase):
	def read(self, stored):
		with patch.object(tc.frappe.db, "get_single_value", return_value=stored):
			return tc.switched_on()

	def test_a_site_that_never_set_it_counts_as_on(self):
		self.assertTrue(self.read(None))

	def test_a_stored_value_decides(self):
		self.assertTrue(self.read(1))
		self.assertTrue(self.read("1"))
		self.assertFalse(self.read(0))
		self.assertFalse(self.read("0"))


class TestTheHooks(FrappeTestCase):
	def setUp(self):
		on = patch.object(tc, "switched_on", return_value=True)
		on.start()
		self.addCleanup(on.stop)

	def test_a_customer_name_is_saved_in_title_case_before_the_id_is_made(self):
		customer = Party("Customer", customer_name="THE DAILY BREAD BAKERY WLL")
		tc.title_case_party(customer, "before_insert")
		self.assertEqual(customer.customer_name, "The Daily Bread Bakery Wll")

	def test_a_supplier_name_the_same(self):
		supplier = Party("Supplier", supplier_name="LULU BAHRAIN HYPERMARKET WLL")
		tc.title_case_party(supplier, "validate")
		self.assertEqual(supplier.supplier_name, "Lulu Bahrain Hypermarket Wll")

	def test_a_name_already_right_is_left_alone(self):
		customer = Party("Customer", customer_name="Khalid")
		tc.title_case_party(customer)
		self.assertEqual(customer.customer_name, "Khalid")

	def test_a_blank_name_is_left_for_the_mandatory_check(self):
		customer = Party("Customer", customer_name=None)
		tc.title_case_party(customer)
		self.assertIsNone(customer.customer_name)

	def test_another_doctype_is_not_touched(self):
		item = Party("Item", item_name="STEEL BAR", customer_name="X Y")
		tc.title_case_party(item)
		self.assertEqual(item.item_name, "STEEL BAR")
		self.assertEqual(item.customer_name, "X Y")

	def test_switched_off_nothing_changes(self):
		customer = Party("Customer", customer_name="THE DAILY BREAD BAKERY WLL")
		with patch.object(tc, "switched_on", return_value=False):
			tc.title_case_party(customer)
			self.assertIsNone(tc.title_case_rename(customer, "before_rename", "A", "B B"))
		self.assertEqual(customer.customer_name, "THE DAILY BREAD BAKERY WLL")


class TestTheRename(FrappeTestCase):
	def setUp(self):
		on = patch.object(tc, "switched_on", return_value=True)
		on.start()
		self.addCleanup(on.stop)

	def rename(self, doctype, new, naming, merge=False):
		with patch.object(tc.frappe.defaults, "get_global_default", return_value=naming):
			return tc.title_case_rename(Party(doctype), "before_rename", "Old Name", new, merge)

	def test_a_customer_named_by_its_name_is_renamed_in_title_case(self):
		self.assertEqual(self.rename("Customer", "ABDUL RAHMAN CO", "Customer Name"), "Abdul Rahman Co")

	def test_a_supplier_named_by_its_name_the_same(self):
		self.assertEqual(self.rename("Supplier", "SHARAF DG WLL", "Supplier Name"), "Sharaf Dg Wll")

	def test_a_naming_series_id_is_not_a_name_and_is_left_alone(self):
		self.assertIsNone(self.rename("Customer", "CUST-00012", "Naming Series"))

	def test_a_merge_keeps_the_spelling_of_the_record_it_merges_into(self):
		self.assertIsNone(self.rename("Customer", "ABDUL RAHMAN CO", "Customer Name", merge=True))

	def test_a_name_already_right_returns_nothing_to_change(self):
		self.assertIsNone(self.rename("Customer", "Abdul Rahman Co", "Customer Name"))

	def test_the_defaults_that_decide_are_the_ones_erpnext_reads(self):
		asked = []
		with patch.object(tc.frappe.defaults, "get_global_default", side_effect=lambda key: asked.append(key) or "x"):
			tc.title_case_rename(Party("Customer"), "before_rename", "A", "B")
			tc.title_case_rename(Party("Supplier"), "before_rename", "A", "B")
		self.assertEqual(asked, ["cust_master_name", "supp_master_name"])


class TestRegistered(FrappeTestCase):
	def test_the_hooks_are_on_both_masters(self):
		from sf_trading import hooks

		for doctype in ("Customer", "Supplier"):
			events = hooks.doc_events[doctype]
			for event, handler in (
				("before_insert", "sf_trading.title_case.title_case_party"),
				("validate", "sf_trading.title_case.title_case_party"),
				("before_rename", "sf_trading.title_case.title_case_rename"),
			):
				registered = events[event]
				registered = [registered] if isinstance(registered, str) else registered
				self.assertIn(handler, registered, "%s %s" % (doctype, event))

	def test_every_handler_named_in_the_hooks_exists(self):
		for path in ("sf_trading.title_case.title_case_party", "sf_trading.title_case.title_case_rename",
				"sf_trading.party_accounts.apply_title_case"):
			self.assertTrue(callable(resolve(path)), path)

	def test_the_old_entry_point_still_does_the_new_rule(self):
		customer = Party("Customer", customer_name="THE DAILY BREAD BAKERY WLL")
		with patch.object(tc, "switched_on", return_value=True):
			resolve("sf_trading.party_accounts.apply_title_case")(customer)
		self.assertEqual(customer.customer_name, "The Daily Bread Bakery Wll")
