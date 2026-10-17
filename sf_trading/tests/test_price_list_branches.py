# sf_trading/tests/test_price_list_branches.py
"""A Price List can be limited to branches (Applicable Branches); with none named it is open to all.

Nothing is created: the table reads are patched, so the rules run without a site and without
touching Item Price (never run a test module against a real site -- ERPNext's before_tests wipes
Item Price):

    bench --site <scratch-site> run-tests --module sf_trading.tests.test_price_list_branches
"""

from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase

from sf_trading import branch_price_list as bpl

# Retail is open to everyone; Offer is the Dock branch's; Wholesale is the Dock and Mall branches'.
LIMITS = {"Offer": {"Dock"}, "Wholesale": {"Dock", "Mall"}}


class Row(dict):
	def get(self, key, default=None):
		return dict.get(self, key, default)

	__getattr__ = dict.get


class Doc(dict):
	def __init__(self, doctype, **fields):
		super().__init__(doctype=doctype, **fields)

	doctype = property(lambda self: self["doctype"])
	name = property(lambda self: self.get("name"))

	def get(self, key, default=None):
		return dict.get(self, key, default)


def config_rows(*lists):
	return [Row(price_list=name, is_default=default, idx=i) for i, (name, default) in enumerate(lists, 1)]


class TestOpenToBranch(FrappeTestCase):
	def test_a_list_naming_no_branch_is_open_to_every_branch(self):
		self.assertTrue(bpl.open_to_branch("Retail", "Dock", LIMITS))
		self.assertTrue(bpl.open_to_branch("Retail", "Anywhere", LIMITS))

	def test_a_limited_list_is_open_to_its_branches_only(self):
		self.assertTrue(bpl.open_to_branch("Offer", "Dock", LIMITS))
		self.assertFalse(bpl.open_to_branch("Offer", "Mall", LIMITS))
		self.assertTrue(bpl.open_to_branch("Wholesale", "Mall", LIMITS))

	def test_a_document_with_no_branch_is_not_limited(self):
		self.assertTrue(bpl.open_to_branch("Offer", None, LIMITS))

	def test_limited_lists_groups_the_rows_by_list(self):
		rows = [Row(parent="Offer", branch="Dock"), Row(parent="Wholesale", branch="Dock"), Row(parent="Wholesale", branch="Mall")]
		with patch.object(frappe.db, "table_exists", return_value=True, create=True), patch.object(
			frappe, "get_all", return_value=rows
		):
			self.assertEqual(bpl.limited_lists(), LIMITS)

	def test_limited_lists_is_empty_before_the_table_exists(self):
		with patch.object(frappe.db, "table_exists", return_value=False, create=True):
			self.assertEqual(bpl.limited_lists(), {})


class TestBranchConfigurationFilteredByTheLists(FrappeTestCase):
	def patched(self, rows):
		return (
			patch.object(bpl, "_rows", return_value=rows),
			patch.object(bpl, "_of_kind", side_effect=lambda r, kind: r),
			patch.object(bpl, "limited_lists", return_value=LIMITS),
		)

	def run_with(self, rows, fn, *args):
		a, b, c = self.patched(rows)
		with a, b, c:
			return fn(*args)

	def test_a_list_limited_to_other_branches_drops_out_of_a_branchs_set(self):
		rows = config_rows(("Retail", 0), ("Offer", 0))
		self.assertEqual(self.run_with(rows, bpl.allowed_price_lists, "Mall", "selling"), ["Retail"])
		self.assertEqual(self.run_with(rows, bpl.allowed_price_lists, "Dock", "selling"), ["Retail", "Offer"])

	def test_the_default_is_never_a_list_the_branch_is_kept_out_of(self):
		rows = config_rows(("Offer", 1))
		self.assertIsNone(self.run_with(rows, bpl.default_price_list, "Mall", "selling"))
		self.assertEqual(self.run_with(rows, bpl.default_price_list, "Dock", "selling"), "Offer")

	def test_a_branch_with_no_configuration_keeps_no_opinion(self):
		self.assertEqual(self.run_with([], bpl.allowed_price_lists, "Mall", "selling"), [])
		self.assertIsNone(self.run_with([], bpl.default_price_list, "Mall", "selling"))

	def test_excluded_lists_are_the_enabled_ones_limited_elsewhere(self):
		with patch.object(bpl, "limited_lists", return_value=LIMITS), patch.object(
			frappe, "get_all", return_value=["Offer"]
		) as get_all:
			self.assertEqual(bpl.excluded_price_lists("Mall", "selling"), ["Offer"])
		# Wholesale names Mall, so only Offer is asked about
		self.assertEqual(get_all.call_args.kwargs["filters"]["name"], ["in", ["Offer"]])
		self.assertEqual(get_all.call_args.kwargs["filters"]["selling"], 1)

	def test_nothing_is_excluded_without_a_branch_or_a_limit(self):
		self.assertEqual(bpl.excluded_price_lists("", "selling"), [])
		with patch.object(bpl, "limited_lists", return_value={}):
			self.assertEqual(bpl.excluded_price_lists("Mall", "selling"), [])

	def test_branch_managed_lists_counts_the_limited_ones_too(self):
		with patch.object(frappe.db, "table_exists", return_value=False, create=True), patch.object(
			bpl, "limited_lists", return_value=LIMITS
		), patch.object(frappe, "get_all", return_value=["Offer", "Wholesale"]):
			self.assertEqual(bpl.branch_managed_lists("selling"), {"Offer", "Wholesale"})


class TestTheGuardOnADocument(FrappeTestCase):
	def guard(self, doc, allowed=()):
		with patch.object(bpl, "limited_lists", return_value=LIMITS), patch.object(
			bpl, "allowed_price_lists", return_value=list(allowed)
		):
			bpl.validate_price_list_allowed(doc)

	def test_a_branch_with_no_configuration_may_not_use_a_list_limited_elsewhere(self):
		doc = Doc("Sales Invoice", branch="Mall", selling_price_list="Offer")
		with self.assertRaises(frappe.ValidationError):
			self.guard(doc)

	def test_a_list_open_to_all_passes(self):
		self.guard(Doc("Sales Invoice", branch="Mall", selling_price_list="Retail"))

	def test_a_limited_list_passes_for_its_own_branch(self):
		self.guard(Doc("Sales Invoice", branch="Dock", selling_price_list="Offer"))

	def test_the_configured_set_still_decides_when_there_is_one(self):
		doc = Doc("Sales Invoice", branch="Dock", selling_price_list="Retail")
		with self.assertRaises(frappe.ValidationError):
			self.guard(doc, allowed=["Offer"])

	def test_a_sales_invoice_row_with_a_list_limited_elsewhere_is_refused(self):
		doc = Doc(
			"Sales Invoice",
			branch="Mall",
			selling_price_list="Retail",
			items=[Row(idx=1, custom_price_list="Retail"), Row(idx=2, custom_price_list="Offer")],
		)
		with self.assertRaises(frappe.ValidationError) as caught:
			self.guard(doc)
		self.assertIn("Row 2", str(caught.exception))

	def test_a_row_with_an_open_or_blank_list_passes(self):
		doc = Doc(
			"Sales Invoice",
			branch="Mall",
			selling_price_list="Retail",
			items=[Row(idx=1, custom_price_list="Retail"), Row(idx=2, custom_price_list=None), Row(idx=3)],
		)
		self.guard(doc)

	def test_a_document_with_no_branch_is_left_alone(self):
		self.guard(Doc("Sales Invoice", selling_price_list="Offer", items=[Row(idx=1, custom_price_list="Offer")]))


class TestTheLinkQuery(FrappeTestCase):
	def ask(self, filters, txt="", allowed=(), excluded=()):
		with patch.object(bpl, "allowed_price_lists", return_value=list(allowed)), patch.object(
			bpl, "excluded_price_lists", return_value=list(excluded)
		), patch.object(frappe, "get_all", return_value=[]) as get_all:
			bpl.branch_price_list_query("Price List", txt, "name", 0, 20, filters)
		return get_all.call_args.kwargs["filters"]

	def test_lists_limited_to_other_branches_are_not_offered(self):
		conditions = self.ask({"branch": "Mall", "kind": "selling"}, excluded=["Offer"])
		self.assertIn(["name", "not in", ["Offer"]], conditions)

	def test_a_configured_branch_is_offered_only_its_own_set(self):
		conditions = self.ask({"branch": "Dock", "kind": "selling"}, allowed=["Retail", "Offer"])
		self.assertIn(["name", "in", ["Retail", "Offer"]], conditions)

	def test_a_row_list_ignores_the_configured_set_but_not_the_limits(self):
		conditions = self.ask({"branch": "Mall", "kind": "selling", "row": 1}, allowed=["Retail"], excluded=["Offer"])
		self.assertNotIn(["name", "in", ["Retail"]], conditions)
		self.assertIn(["name", "not in", ["Offer"]], conditions)

	def test_typing_narrows_the_offer(self):
		conditions = self.ask({"branch": "Dock", "kind": "selling"}, txt="ret", allowed=["Retail", "Offer"])
		self.assertIn(["name", "in", ["Retail"]], conditions)
		conditions = self.ask({"branch": "", "kind": "buying"}, txt="whole")
		self.assertIn(["name", "like", "%whole%"], conditions)
		self.assertIn(["buying", "=", 1], conditions)


class TestSavingAPriceList(FrappeTestCase):
	def save(self, doc, configured=()):
		with patch.object(frappe.db, "table_exists", return_value=True, create=True), patch.object(
			frappe, "get_all", return_value=list(configured)
		):
			bpl.validate_price_list_branches(doc)

	def test_a_list_with_no_branches_is_left_alone(self):
		self.save(Doc("Price List", name="Offer", custom_applicable_branches=[]), configured=["Mall"])

	def test_the_same_branch_twice_is_refused(self):
		doc = Doc("Price List", name="Offer", custom_applicable_branches=[Row(branch="Dock"), Row(branch="Dock")])
		with self.assertRaises(frappe.ValidationError):
			self.save(doc)

	def test_a_branch_whose_configuration_names_the_list_cannot_be_left_out(self):
		doc = Doc("Price List", name="Offer", custom_applicable_branches=[Row(branch="Dock")])
		with self.assertRaises(frappe.ValidationError) as caught:
			self.save(doc, configured=["Dock", "Mall"])
		self.assertIn("Mall", str(caught.exception))

	def test_naming_every_configured_branch_passes(self):
		doc = Doc("Price List", name="Offer", custom_applicable_branches=[Row(branch="Dock"), Row(branch="Mall")])
		self.save(doc, configured=["Dock", "Mall"])


class TestSavingABranchConfiguration(FrappeTestCase):
	def save(self, doc):
		with patch.object(bpl, "limited_lists", return_value=LIMITS), patch.object(
			frappe.db, "get_value", return_value=1, create=True
		):
			bpl.validate_branch_price_lists(doc)

	def test_a_list_limited_to_other_branches_cannot_be_given_to_this_one(self):
		doc = Doc("Branch Configuration", name="Mall", branch="Mall", price_list=config_rows(("Offer", 0)))
		with self.assertRaises(frappe.ValidationError):
			self.save(doc)

	def test_a_list_that_names_the_branch_or_nobody_passes(self):
		doc = Doc("Branch Configuration", name="Mall", branch="Mall", price_list=config_rows(("Retail", 0), ("Wholesale", 1)))
		self.save(doc)
