"""Tests for branch-wise pricing through the Price List.

Everything under test is resolution and refusal: which price list a branch is priced from, and
what a document does with the answer. Documents are stubs — the point is the decision, not
ERPNext's invoicing.

    bench --site <scratch-site> run-tests --module sf_trading.tests.test_branch_price_list
"""

import frappe
from frappe.tests.utils import FrappeTestCase

from sf_trading.branch_price_list import (
	BRANCH_TABLE,
	apply_branch_price_list,
	get_branch_price_list,
	kind_for,
	mapped_price_lists,
	validate_price_list,
)

BRANCH_A = "_Test Price Branch A"
BRANCH_B = "_Test Price Branch B"
BRANCH_C = "_Test Price Branch C"
LIST_A = "_Test Branch Price List A"
LIST_B = "_Test Branch Price List B"
LIST_PLAIN = "_Test Branch Price List Plain"


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
		for branch in (BRANCH_A, BRANCH_B, BRANCH_C):
			if not frappe.db.exists("Branch", branch):
				frappe.get_doc({"doctype": "Branch", "branch": branch}).insert()

		cls.make_price_list(LIST_A, branches=[BRANCH_A, BRANCH_B])
		cls.make_price_list(LIST_B, branches=[BRANCH_C])
		cls.make_price_list(LIST_PLAIN, branches=[])

	@classmethod
	def make_price_list(cls, name, branches):
		if frappe.db.exists("Price List", name):
			doc = frappe.get_doc("Price List", name)
		else:
			doc = frappe.get_doc(
				{
					"doctype": "Price List",
					"price_list_name": name,
					"currency": frappe.db.get_single_value("Global Defaults", "default_currency") or "INR",
					"selling": 1,
					"enabled": 1,
				}
			)
		doc.set(BRANCH_TABLE, [])
		for branch in branches:
			doc.append(BRANCH_TABLE, {"branch": branch})
		doc.save()
		return doc

	# ── resolution ───────────────────────────────────────────────────────────
	def test_a_branch_resolves_to_the_list_that_names_it(self):
		self.assertEqual(get_branch_price_list(BRANCH_A, "selling"), LIST_A)
		self.assertEqual(get_branch_price_list(BRANCH_B, "selling"), LIST_A)
		self.assertEqual(get_branch_price_list(BRANCH_C, "selling"), LIST_B)

	def test_a_list_with_no_branches_claims_nothing(self):
		"""Applicable for Branches empty means "everywhere", not "nowhere in particular"."""
		self.assertNotIn(LIST_PLAIN, set(mapped_price_lists("selling").values()))

	def test_an_unmapped_branch_resolves_to_nothing(self):
		self.assertIsNone(get_branch_price_list("_Test Price Branch Unknown", "selling"))
		self.assertIsNone(get_branch_price_list(None, "selling"))

	def test_a_selling_list_is_not_offered_to_a_buying_document(self):
		self.assertIsNone(get_branch_price_list(BRANCH_A, "buying"))

	def test_a_disabled_list_prices_nothing(self):
		frappe.db.set_value("Price List", LIST_B, "enabled", 0)
		frappe.clear_cache(doctype="Price List")
		try:
			self.assertIsNone(get_branch_price_list(BRANCH_C, "selling"))
		finally:
			frappe.db.set_value("Price List", LIST_B, "enabled", 1)
			frappe.clear_cache(doctype="Price List")

	def test_which_doctypes_are_priced_which_way(self):
		self.assertEqual(kind_for("Sales Invoice"), "selling")
		self.assertEqual(kind_for("Purchase Order"), "buying")
		self.assertIsNone(kind_for("Stock Entry"))

	# ── applying it ──────────────────────────────────────────────────────────
	def test_an_empty_price_list_is_filled_in(self):
		doc = StubDoc("Sales Invoice", branch=BRANCH_A, selling_price_list=None)
		apply_branch_price_list(doc)
		self.assertEqual(doc["selling_price_list"], LIST_A)

	def test_another_branchs_list_is_replaced(self):
		doc = StubDoc("Sales Invoice", branch=BRANCH_C, selling_price_list=LIST_A)
		apply_branch_price_list(doc)
		self.assertEqual(doc["selling_price_list"], LIST_B)

	def test_a_deliberate_pick_is_left_alone(self):
		doc = StubDoc("Sales Invoice", branch=BRANCH_A, selling_price_list=LIST_PLAIN)
		apply_branch_price_list(doc)
		self.assertEqual(doc["selling_price_list"], LIST_PLAIN)

	def test_a_document_with_no_branch_is_untouched(self):
		doc = StubDoc("Sales Invoice", branch=None, selling_price_list=LIST_PLAIN)
		apply_branch_price_list(doc)
		self.assertEqual(doc["selling_price_list"], LIST_PLAIN)

	def test_a_branch_with_no_list_is_untouched(self):
		doc = StubDoc("Sales Invoice", branch="_Test Price Branch Unknown", selling_price_list=LIST_PLAIN)
		apply_branch_price_list(doc)
		self.assertEqual(doc["selling_price_list"], LIST_PLAIN)

	def test_a_doctype_nobody_prices_is_untouched(self):
		doc = StubDoc("Stock Entry", branch=BRANCH_A)
		apply_branch_price_list(doc)
		self.assertIsNone(doc.get("selling_price_list"))

	def test_the_currency_is_re_read_when_the_list_changes(self):
		"""Currency and conversion rate belong to the list, so the controller must re-read them."""
		doc = StubDoc(
			"Sales Invoice",
			branch=BRANCH_A,
			selling_price_list=None,
			price_list_currency="USD",
			plc_conversion_rate=3.75,
		)
		apply_branch_price_list(doc)
		self.assertIsNone(doc["price_list_currency"])
		self.assertIsNone(doc["plc_conversion_rate"])

	# ── guarding the mapping ─────────────────────────────────────────────────
	def test_two_lists_cannot_claim_the_same_branch(self):
		clash = frappe.get_doc(
			{
				"doctype": "Price List",
				"price_list_name": "_Test Branch Price List Clash",
				"currency": frappe.db.get_value("Price List", LIST_A, "currency"),
				"selling": 1,
				"enabled": 1,
				BRANCH_TABLE: [{"branch": BRANCH_A}],
			}
		)
		self.assertRaises(frappe.ValidationError, clash.insert)

	def test_the_same_branch_twice_on_one_list_is_refused(self):
		doc = frappe.get_doc("Price List", LIST_A)
		doc.append(BRANCH_TABLE, {"branch": BRANCH_A})
		self.assertRaises(frappe.ValidationError, validate_price_list, doc)
		doc.reload()

	def test_a_list_may_keep_its_own_branches_on_re_save(self):
		doc = frappe.get_doc("Price List", LIST_A)
		doc.save()  # must not raise on itself
		self.assertEqual(
			sorted(row.branch for row in doc.get(BRANCH_TABLE)), sorted([BRANCH_A, BRANCH_B])
		)
