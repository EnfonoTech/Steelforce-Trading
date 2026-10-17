# sf_trading/tests/test_item_barcode.py
"""Every Item carries a barcode: its Item Code, CODE-128, in its stock UOM (sf_trading/item_barcode.py).

The database is patched, so nothing is created or read:

    bench --site <scratch-site> run-tests --module sf_trading.tests.test_item_barcode
"""

import importlib.util
import os
from unittest.mock import MagicMock, patch

import frappe
from frappe.tests.utils import FrappeTestCase

from sf_trading import item_barcode


class FakeItem:
	"""Just enough of a Document: get and append."""

	def __init__(self, name="ITEM-1", item_code=None, stock_uom="Nos", barcodes=None):
		self.name = name
		self.item_code = item_code if item_code is not None else name
		self.stock_uom = stock_uom
		self.barcodes = list(barcodes or [])

	def get(self, key, default=None):
		return getattr(self, key, default)

	def append(self, fieldname, row):
		getattr(self, fieldname).append(row)


class TestTheHook(FrappeTestCase):
	def run_hook(self, item, taken=False):
		with patch.object(item_barcode, "barcode_taken", return_value=taken) as asked:
			item_barcode.add_item_barcode(item)
		return asked

	def test_an_item_with_no_barcode_gets_its_code_as_one(self):
		item = FakeItem("110101010044", stock_uom="Nos")
		self.run_hook(item)
		self.assertEqual(item.barcodes, [{"barcode": "110101010044", "barcode_type": "CODE-128", "uom": "Nos"}])

	def test_the_uom_is_the_items_stock_uom(self):
		item = FakeItem("110103081007", stock_uom="PCS")
		self.run_hook(item)
		self.assertEqual(item.barcodes[0]["uom"], "PCS")

	def test_an_alphanumeric_code_is_as_good_as_a_number(self):
		item = FakeItem("SVC-00007")
		self.run_hook(item)
		self.assertEqual(item.barcodes[0]["barcode"], "SVC-00007")

	def test_an_item_that_already_has_a_barcode_is_left_alone(self):
		own = {"barcode": "6291041500213", "barcode_type": "EAN", "uom": "Nos"}
		item = FakeItem("110101010044", barcodes=[own])
		asked = self.run_hook(item)
		self.assertEqual(item.barcodes, [own])
		asked.assert_not_called()

	def test_a_second_save_does_not_add_a_second_row(self):
		item = FakeItem("110101010044")
		self.run_hook(item)
		self.run_hook(item)
		self.assertEqual(len(item.barcodes), 1)

	def test_the_item_code_is_used_and_the_name_only_when_there_is_none(self):
		item = FakeItem("NAME-1", item_code="CODE-1")
		self.run_hook(item)
		self.assertEqual(item.barcodes[0]["barcode"], "CODE-1")
		item = FakeItem("NAME-2", item_code="")
		self.run_hook(item)
		self.assertEqual(item.barcodes[0]["barcode"], "NAME-2")

	def test_an_item_with_no_code_at_all_gets_nothing_and_no_error(self):
		item = FakeItem(name="", item_code="")
		self.run_hook(item)
		self.assertEqual(item.barcodes, [])

	def test_a_code_another_item_holds_as_its_barcode_is_not_added(self):
		item = FakeItem("110101010044")
		self.run_hook(item, taken=True)
		self.assertEqual(item.barcodes, [])

	def test_the_check_is_against_other_items_only(self):
		with patch.object(frappe.db, "exists", return_value=False, create=True) as exists:
			self.assertFalse(item_barcode.barcode_taken("110101010044", "110101010044"))
		self.assertEqual(exists.call_args.args, ("Item Barcode", {"barcode": "110101010044", "parent": ["!=", "110101010044"]}))
		with patch.object(frappe.db, "exists", return_value="x", create=True):
			self.assertTrue(item_barcode.barcode_taken("110101010044", "OTHER"))


def load_patch():
	path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "patches", "v0_1", "add_item_barcodes.py")
	spec = importlib.util.spec_from_file_location("sf_trading_test_add_item_barcodes", path)
	module = importlib.util.module_from_spec(spec)
	spec.loader.exec_module(module)
	return module


class TestTheBackfillPatch(FrappeTestCase):
	def run_patch(self, taken, items, blank):
		module = load_patch()
		inserted = []

		def make(values):
			doc = MagicMock()
			doc.values = values
			doc.db_insert.side_effect = lambda: inserted.append(values)
			return doc

		def sql(query, *args, **kwargs):
			return items if "not exists" in query else blank

		with patch.object(frappe.db, "sql_list", return_value=taken, create=True), patch.object(
			frappe.db, "sql", side_effect=sql, create=True
		), patch.object(frappe, "get_doc", side_effect=make, create=True), patch.object(
			frappe.db, "set_value", create=True
		) as set_value, patch.object(frappe.db, "commit", create=True) as commit:
			module.execute()
		return inserted, set_value, commit

	def test_each_item_without_a_barcode_gets_the_import_shaped_row(self):
		items = [frappe._dict(name="A-1", stock_uom="Nos"), frappe._dict(name="B-2", stock_uom="PCS")]
		inserted, _set_value, commit = self.run_patch([], items, [])
		self.assertEqual(
			inserted,
			[
				{"doctype": "Item Barcode", "parent": "A-1", "parenttype": "Item", "parentfield": "barcodes", "idx": 1, "barcode": "A-1", "barcode_type": "CODE-128", "uom": "Nos"},
				{"doctype": "Item Barcode", "parent": "B-2", "parenttype": "Item", "parentfield": "barcodes", "idx": 1, "barcode": "B-2", "barcode_type": "CODE-128", "uom": "PCS"},
			],
		)
		commit.assert_called_once()

	def test_a_code_that_is_another_items_barcode_is_skipped_whatever_its_case(self):
		items = [frappe._dict(name="Clash-1", stock_uom="Nos"), frappe._dict(name="B-2", stock_uom="Nos")]
		inserted, _s, _c = self.run_patch(["CLASH-1"], items, [])
		self.assertEqual([row["parent"] for row in inserted], ["B-2"])

	def test_two_items_with_codes_that_differ_only_in_case_do_not_collide_with_each_other(self):
		items = [frappe._dict(name="Same-1", stock_uom="Nos"), frappe._dict(name="SAME-1", stock_uom="Nos")]
		inserted, _s, _c = self.run_patch([], items, [])
		self.assertEqual([row["parent"] for row in inserted], ["Same-1"])

	def test_a_blank_uom_on_an_item_code_barcode_gets_the_stock_uom_and_nothing_else_changes(self):
		blank = [frappe._dict(name="un135u9i6o", stock_uom="Nos")]
		inserted, set_value, _c = self.run_patch([], [], blank)
		self.assertEqual(inserted, [])
		set_value.assert_called_once_with("Item Barcode", "un135u9i6o", "uom", "Nos", update_modified=False)

	def test_a_second_run_with_nothing_to_do_writes_nothing(self):
		inserted, set_value, commit = self.run_patch(["A-1"], [], [])
		self.assertEqual(inserted, [])
		set_value.assert_not_called()
		commit.assert_called_once()


class TestTheCode128Option(FrappeTestCase):
	"""ERPNext blanks a barcode type that is not in its list on every save, so CODE-128 has to be put in the list."""

	def fixture(self):
		import json

		path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "fixtures", "property_setter.json")
		with open(path, encoding="utf-8") as handle:
			return {row["name"]: row for row in json.load(handle)}

	def test_the_property_setter_adds_code_128_to_erpnexts_list(self):
		row = self.fixture()["Item Barcode-barcode_type-options"]
		self.assertEqual((row["doc_type"], row["field_name"], row["property"]), ("Item Barcode", "barcode_type", "options"))
		options = row["value"].split("\n")
		self.assertEqual(options[0], "")
		self.assertIn("CODE-128", options)
		# nothing of ERPNext's own list was lost
		for kept in ("EAN", "UPC-A", "CODE-39", "EAN-8", "GS1", "GTIN", "ISBN", "ISSN", "JAN", "PZN", "UPC"):
			self.assertIn(kept, options)

	def test_the_fixture_is_listed_in_hooks_so_it_is_exported_and_synced(self):
		path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "hooks.py")
		with open(path, encoding="utf-8") as handle:
			self.assertIn('"Item Barcode-barcode_type-options"', handle.read())

	def test_the_type_the_hook_uses_is_the_one_in_the_list(self):
		self.assertIn(item_barcode.BARCODE_TYPE, self.fixture()["Item Barcode-barcode_type-options"]["value"].split("\n"))
