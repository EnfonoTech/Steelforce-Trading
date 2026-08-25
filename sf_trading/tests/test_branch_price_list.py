"""Tests for branch-wise pricing, now owned by Branch Configuration.

Under test: which list a branch is priced from, which lists it may use at all, what a document does
with the answer, and the refusals that keep the mapping unambiguous.

    bench --site <scratch-site> run-tests --module sf_trading.tests.test_branch_price_list
"""

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import flt

from sf_trading.branch_price_list import (
	allowed_price_lists,
	apply_branch_price_list,
	branch_managed_lists,
	default_price_list,
	kind_for,
	validate_branch_price_lists,
	validate_price_list_allowed,
)

BRANCH_A = "_Test Price Branch A"
BRANCH_B = "_Test Price Branch B"
RETAIL = "_Test Branch Retail"
OFFER = "_Test Branch Offer"
OTHER = "_Test Branch Other"
PLAIN = "_Test Branch Unmapped List"


class StubDoc(dict):
	def __init__(self, doctype, **fields):
		super().__init__(doctype=doctype, **fields)

	@property
	def doctype(self):
		return self["doctype"]

	def get(self, key, default=None):
		return dict.get(self, key, default)

	def set(self, key, value):
		self[key] = value


class TestBranchPriceList(FrappeTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		cls.currency = frappe.db.get_single_value("Global Defaults", "default_currency") or "INR"
		for branch in (BRANCH_A, BRANCH_B):
			if not frappe.db.exists("Branch", branch):
				frappe.get_doc({"doctype": "Branch", "branch": branch}).insert()
		for name in (RETAIL, OFFER, OTHER, PLAIN):
			cls.make_price_list(name)

		cls.configure(BRANCH_A, [(RETAIL, 1), (OFFER, 0)])
		cls.configure(BRANCH_B, [(OTHER, 0)])          # single row, no tick

	@classmethod
	def make_price_list(cls, name):
		if frappe.db.exists("Price List", name):
			return
		frappe.get_doc(
			{
				"doctype": "Price List",
				"price_list_name": name,
				"currency": cls.currency,
				"selling": 1,
				"enabled": 1,
			}
		).insert()

	@classmethod
	def configure(cls, branch, rows):
		if frappe.db.exists("Branch Configuration", branch):
			config = frappe.get_doc("Branch Configuration", branch)
		else:
			config = frappe.get_doc({"doctype": "Branch Configuration", "branch": branch})
		config.set("price_list", [])
		for price_list, is_default in rows:
			config.append("price_list", {"price_list": price_list, "is_default": is_default})
		config.flags.ignore_permissions = True
		config.save()
		return config

	# ── reading the configuration ────────────────────────────────────────────
	def test_the_ticked_row_is_what_a_branch_is_priced_from(self):
		self.assertEqual(default_price_list(BRANCH_A, "selling"), RETAIL)

	def test_a_single_row_needs_no_tick(self):
		self.assertEqual(default_price_list(BRANCH_B, "selling"), OTHER)

	def test_every_configured_list_may_be_used(self):
		self.assertEqual(sorted(allowed_price_lists(BRANCH_A, "selling")), sorted([RETAIL, OFFER]))

	def test_a_branch_with_no_rows_has_no_opinion(self):
		self.assertEqual(allowed_price_lists("_Test Price Branch Unknown", "selling"), [])
		self.assertIsNone(default_price_list("_Test Price Branch Unknown", "selling"))

	def test_a_selling_list_is_not_offered_to_a_buying_document(self):
		self.assertIsNone(default_price_list(BRANCH_A, "buying"))
		self.assertEqual(allowed_price_lists(BRANCH_A, "buying"), [])

	def test_a_disabled_list_prices_nothing(self):
		frappe.db.set_value("Price List", OTHER, "enabled", 0)
		frappe.clear_cache(doctype="Price List")
		try:
			self.assertIsNone(default_price_list(BRANCH_B, "selling"))
		finally:
			frappe.db.set_value("Price List", OTHER, "enabled", 1)
			frappe.clear_cache(doctype="Price List")

	def test_which_doctypes_are_priced_which_way(self):
		self.assertEqual(kind_for("Sales Invoice"), "selling")
		self.assertEqual(kind_for("Purchase Order"), "buying")
		self.assertIsNone(kind_for("Stock Entry"))

	def test_branch_managed_lists_covers_every_branch(self):
		managed = branch_managed_lists("selling")
		self.assertIn(RETAIL, managed)
		self.assertIn(OTHER, managed)
		self.assertNotIn(PLAIN, managed)

	# ── applying it ──────────────────────────────────────────────────────────
	def test_an_empty_price_list_is_filled_with_the_default(self):
		doc = StubDoc("Sales Invoice", branch=BRANCH_A, selling_price_list=None)
		apply_branch_price_list(doc)
		self.assertEqual(doc["selling_price_list"], RETAIL)

	def test_another_branchs_list_is_replaced(self):
		doc = StubDoc("Sales Invoice", branch=BRANCH_B, selling_price_list=RETAIL)
		apply_branch_price_list(doc)
		self.assertEqual(doc["selling_price_list"], OTHER)

	def test_a_second_list_of_this_branch_is_left_alone(self):
		"""Somebody picked the branch's offer list on purpose."""
		doc = StubDoc("Sales Invoice", branch=BRANCH_A, selling_price_list=OFFER)
		apply_branch_price_list(doc)
		self.assertEqual(doc["selling_price_list"], OFFER)

	def test_a_document_with_no_branch_is_untouched(self):
		doc = StubDoc("Sales Invoice", branch=None, selling_price_list=PLAIN)
		apply_branch_price_list(doc)
		self.assertEqual(doc["selling_price_list"], PLAIN)

	def test_the_currency_is_re_read_when_the_list_changes(self):
		doc = StubDoc(
			"Sales Invoice", branch=BRANCH_A, selling_price_list=None,
			price_list_currency="USD", plc_conversion_rate=3.75,
		)
		apply_branch_price_list(doc)
		self.assertIsNone(doc["price_list_currency"])
		self.assertIsNone(doc["plc_conversion_rate"])

	# ── the guard on what a document may use ─────────────────────────────────
	def test_a_list_the_branch_does_not_name_is_refused(self):
		doc = StubDoc("Sales Invoice", branch=BRANCH_A, selling_price_list=PLAIN)
		self.assertRaises(frappe.ValidationError, validate_price_list_allowed, doc)

	def test_a_list_the_branch_names_passes(self):
		for chosen in (RETAIL, OFFER):
			validate_price_list_allowed(StubDoc("Sales Invoice", branch=BRANCH_A, selling_price_list=chosen))

	def test_a_branch_with_no_rows_accepts_anything(self):
		validate_price_list_allowed(
			StubDoc("Sales Invoice", branch="_Test Price Branch Unknown", selling_price_list=PLAIN)
		)

	# ── guarding the configuration ───────────────────────────────────────────
	def test_two_defaults_of_one_kind_are_refused(self):
		config = frappe.get_doc("Branch Configuration", BRANCH_A)
		for row in config.get("price_list"):
			row.is_default = 1
		self.assertRaises(frappe.ValidationError, validate_branch_price_lists, config)
		config.reload()

	def test_the_same_list_twice_is_refused(self):
		config = frappe.get_doc("Branch Configuration", BRANCH_A)
		config.append("price_list", {"price_list": RETAIL})
		self.assertRaises(frappe.ValidationError, validate_branch_price_lists, config)
		config.reload()

	def test_a_disabled_list_cannot_be_configured(self):
		frappe.db.set_value("Price List", PLAIN, "enabled", 0)
		try:
			config = frappe.get_doc("Branch Configuration", BRANCH_B)
			config.append("price_list", {"price_list": PLAIN})
			self.assertRaises(frappe.ValidationError, validate_branch_price_lists, config)
			config.reload()
		finally:
			frappe.db.set_value("Price List", PLAIN, "enabled", 1)
