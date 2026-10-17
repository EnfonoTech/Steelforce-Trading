# sf_trading/tests/test_item_price_branch.py
"""An Item Price may name the branches it serves (sf_trading/item_price_branch.py); with none named it serves all.

Nothing is created: the table reads are patched, and ERPNext's two lookup functions are replaced by
stand-ins, so the rules run without a site and without touching Item Price (never run a test module
against a real site -- ERPNext's before_tests wipes Item Price):

    bench --site <scratch-site> run-tests --module sf_trading.tests.test_item_price_branch
"""

import sys
import types
from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase

from sf_trading import item_price_branch as ipb

# price name -> branches it names. P-ALL is general; P-DOCK serves Dock; P-DUO serves Dock and Mall.
SCOPES = {"P-DOCK": {"Dock"}, "P-DUO": {"Dock", "Mall"}}


class Row(dict):
	__getattr__ = dict.get


def rows(*names):
	"""ERPNext's own shape: (name, price_list_rate, uom) tuples."""
	return [(name, 1.0, "Nos") for name in names]


class TestScopes(FrappeTestCase):
	def test_a_price_naming_no_branch_serves_every_branch(self):
		self.assertTrue(ipb.applies_to(set(), "Dock"))
		self.assertTrue(ipb.applies_to(None, "Anywhere"))

	def test_a_limited_price_serves_only_its_branches(self):
		self.assertTrue(ipb.applies_to({"Dock"}, "Dock"))
		self.assertFalse(ipb.applies_to({"Dock"}, "Mall"))
		self.assertFalse(ipb.applies_to({"Dock"}, None))

	def test_two_prices_compete_only_for_the_same_documents(self):
		self.assertTrue(ipb.same_scope(set(), set()))
		self.assertTrue(ipb.same_scope({"Dock"}, {"Dock", "Mall"}))
		self.assertFalse(ipb.same_scope({"Dock"}, {"Mall"}))
		# a branch price beside the general one is allowed either way round
		self.assertFalse(ipb.same_scope({"Dock"}, set()))
		self.assertFalse(ipb.same_scope(set(), {"Dock"}))


class TestReadingTheTable(FrappeTestCase):
	def test_branches_are_grouped_by_price(self):
		found = [Row(parent="P-DOCK", branch="Dock"), Row(parent="P-DUO", branch="Dock"), Row(parent="P-DUO", branch="Mall")]
		with patch.object(ipb, "table_ready", return_value=True), patch.object(frappe, "get_all", return_value=found):
			self.assertEqual(ipb.branches_by_price(["P-DOCK", "P-DUO", "P-ALL"]), SCOPES)

	def test_nothing_is_read_before_the_table_exists_or_for_no_names(self):
		with patch.object(ipb, "table_ready", return_value=False):
			self.assertEqual(ipb.branches_by_price(["P-DOCK"]), {})
		with patch.object(ipb, "table_ready", return_value=True):
			self.assertEqual(ipb.branches_by_price([]), {})

	def test_names_go_in_slices_so_the_parser_cap_is_never_met(self):
		names = ["P%d" % n for n in range(ipb.BATCH * 2 + 1)]
		with patch.object(ipb, "table_ready", return_value=True), patch.object(frappe, "get_all", return_value=[]) as get_all:
			ipb.branches_by_price(names)
		self.assertEqual([len(call.kwargs["filters"]["parent"][1]) for call in get_all.call_args_list], [ipb.BATCH, ipb.BATCH, 1])


class TestWhatABranchSees(FrappeTestCase):
	def see(self, names, branch, scopes=SCOPES):
		with patch.object(ipb, "branches_by_price", return_value=scopes):
			return [row[0] for row in ipb.visible(rows(*names), branch)]

	def test_the_branchs_own_price_comes_ahead_of_the_general_one(self):
		self.assertEqual(self.see(["P-ALL", "P-DOCK"], "Dock"), ["P-DOCK", "P-ALL"])

	def test_a_price_for_other_branches_is_not_seen(self):
		self.assertEqual(self.see(["P-DOCK", "P-ALL"], "Mall"), ["P-ALL"])
		self.assertEqual(self.see(["P-DOCK"], "Mall"), [])

	def test_a_shared_price_serves_each_of_its_branches(self):
		self.assertEqual(self.see(["P-ALL", "P-DUO"], "Mall"), ["P-DUO", "P-ALL"])
		self.assertEqual(self.see(["P-DUO"], "Dock"), ["P-DUO"])

	def test_a_document_with_no_branch_sees_general_prices_only(self):
		self.assertEqual(self.see(["P-DOCK", "P-ALL"], None), ["P-ALL"])

	def test_the_stock_order_survives_inside_each_group(self):
		self.assertEqual(self.see(["P-ALL2", "P-DUO", "P-ALL", "P-DOCK"], "Dock"), ["P-DUO", "P-DOCK", "P-ALL2", "P-ALL"])

	def test_with_no_limited_price_the_rows_come_back_untouched(self):
		self.assertEqual(self.see(["B", "A", "C"], "Dock", scopes={}), ["B", "A", "C"])

	def test_rate_for_is_the_visible_one(self):
		found = [Row(name="P-ALL", price_list_rate=6.0, currency="BHD"), Row(name="P-DOCK", price_list_rate=5.0, currency="BHD")]
		with patch.object(ipb, "branches_by_price", return_value=SCOPES), patch.object(frappe, "get_all", return_value=found):
			self.assertEqual(ipb.rate_for("ITEM", "Std", "Dock", selling=1).price_list_rate, 5.0)
			self.assertEqual(ipb.rate_for("ITEM", "Std", "Mall", selling=1).price_list_rate, 6.0)
			self.assertEqual(ipb.rate_for("ITEM", "Std", None, selling=1).price_list_rate, 6.0)

	def test_rate_for_is_none_when_every_price_is_for_other_branches(self):
		with patch.object(ipb, "branches_by_price", return_value=SCOPES), patch.object(
			frappe, "get_all", return_value=[Row(name="P-DOCK", price_list_rate=5.0, currency="BHD")]
		):
			self.assertIsNone(ipb.rate_for("ITEM", "Std", "Mall", selling=1))


class TestWhereTheBranchComesFrom(FrappeTestCase):
	def test_args_first(self):
		self.assertEqual(ipb.branch_of({"branch": "Dock", "doc": '{"branch": "Mall"}'}), "Dock")

	def test_then_the_doc_a_browser_call_carries(self):
		frappe.local.form_dict = {"doc": '{"doctype": "Sales Invoice", "branch": "Mall"}'}
		self.assertEqual(ipb.branch_of({}), "Mall")
		frappe.local.form_dict = {"doc": {"branch": "Dock"}}
		self.assertEqual(ipb.branch_of(frappe._dict()), "Dock")

	def test_a_call_with_neither_has_no_branch(self):
		frappe.local.form_dict = {}
		self.assertIsNone(ipb.branch_of({}))
		frappe.local.form_dict = {"doc": "not json"}
		self.assertIsNone(ipb.branch_of({}))
		self.assertIsNone(ipb.branch_of(None))


class TestTheWrappers(FrappeTestCase):
	"""ERPNext's `get_price_list_rate_for` loses the branch on the way to `get_item_price`; the wrappers keep it."""

	def setUp(self):
		self.seen = []

		def stock_get_item_price(args, item_code, ignore_party=False, force_batch_no=False):
			return rows("P-ALL", "P-DOCK")

		def stock_rate_for(args, item_code):
			# what the stock one does: build its own args (no branch) and ask get_item_price
			self.seen.append([row[0] for row in fake.get_item_price({"price_list": "Std"}, item_code)])

		fake = types.ModuleType("erpnext.stock.get_item_details")
		fake.get_item_price = stock_get_item_price
		fake.get_price_list_rate_for = stock_rate_for
		self.fake = fake
		self.stock = (stock_get_item_price, stock_rate_for)

		self.patches = [
			patch.dict(sys.modules, {"erpnext": types.ModuleType("erpnext"), "erpnext.stock": types.ModuleType("erpnext.stock"), "erpnext.stock.get_item_details": fake}),
			patch.dict(ipb._STATE, {"tried": False}),
			patch.dict(ipb._STOCK, {}, clear=True),
			patch.object(ipb, "branches_by_price", return_value=SCOPES),
		]
		for p in self.patches:
			p.start()
		sys.modules["erpnext.stock"].get_item_details = fake
		sys.modules["erpnext"].stock = sys.modules["erpnext.stock"]
		ipb.install()

	def tearDown(self):
		for p in reversed(self.patches):
			p.stop()

	def test_install_repoints_both_functions_once(self):
		self.assertIs(self.fake.get_item_price, ipb.get_item_price)
		self.assertIs(self.fake.get_price_list_rate_for, ipb.get_price_list_rate_for)
		ipb.install()
		self.assertIs(ipb._STOCK["get_item_price"], self.stock[0])

	def test_the_branch_reaches_the_price_lookup(self):
		self.fake.get_price_list_rate_for({"branch": "Dock"}, "ITEM")
		self.fake.get_price_list_rate_for({"branch": "Mall"}, "ITEM")
		self.fake.get_price_list_rate_for({}, "ITEM")
		self.assertEqual(self.seen, [["P-DOCK", "P-ALL"], ["P-ALL"], ["P-ALL"]])

	def test_the_branch_is_forgotten_after_the_call(self):
		self.fake.get_price_list_rate_for({"branch": "Dock"}, "ITEM")
		self.assertFalse(hasattr(frappe.local, "sf_price_branch"))

	def test_the_branch_is_forgotten_even_when_the_stock_function_fails(self):
		def boom(args, item_code):
			raise ValueError("boom")

		ipb._STOCK["get_price_list_rate_for"] = boom
		with self.assertRaises(ValueError):
			self.fake.get_price_list_rate_for({"branch": "Dock"}, "ITEM")
		self.assertFalse(hasattr(frappe.local, "sf_price_branch"))

	def test_a_direct_call_reads_the_branch_from_its_own_args(self):
		self.assertEqual([r[0] for r in self.fake.get_item_price({"branch": "Dock"}, "ITEM")], ["P-DOCK", "P-ALL"])

	def test_the_answer_keeps_the_type_the_stock_function_gave(self):
		ipb._STOCK["get_item_price"] = lambda *a, **k: tuple(rows("P-ALL", "P-DOCK"))
		self.assertIsInstance(self.fake.get_item_price({"branch": "Dock"}, "ITEM"), tuple)

	def test_no_rows_is_passed_straight_back(self):
		ipb._STOCK["get_item_price"] = lambda *a, **k: ()
		self.assertEqual(self.fake.get_item_price({"branch": "Dock"}, "ITEM"), ())

	def test_a_failure_reading_the_table_leaves_the_stock_answer_alone(self):
		with patch.object(ipb, "visible", side_effect=RuntimeError("table gone")):
			self.assertEqual([r[0] for r in self.fake.get_item_price({"branch": "Dock"}, "ITEM")], ["P-ALL", "P-DOCK"])


class TestFingerprint(FrappeTestCase):
	def test_indentation_and_trailing_spaces_do_not_change_it(self):
		def check(self):
			return 1

		class Holder:
			def check(self):
				return 1

		# the same text at two indentation depths is the same function
		self.assertEqual(ipb.fingerprint(check), ipb.fingerprint(Holder.check))
		self.assertEqual(len(ipb.fingerprint(check)), 40)

	def test_unreadable_source_is_an_empty_string(self):
		self.assertEqual(ipb.fingerprint(len), "")


class TestSaving(FrappeTestCase):
	def test_a_branch_twice_in_one_price_is_refused(self):
		doc = Row(custom_applicable_branches=[Row(branch="Dock"), Row(branch="Dock")])
		with self.assertRaises(frappe.ValidationError):
			ipb.validate_item_price_branches(doc)

	def test_distinct_or_no_branches_pass(self):
		for table in ([], [Row(branch="Dock"), Row(branch="Mall")]):
			doc = Row(custom_applicable_branches=table)
			ipb.validate_item_price_branches(doc)
