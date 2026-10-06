"""Tests for the any-order, every-word item search (api/item_search.items_matching_every_word).

    bench --site <scratch-site> run-tests --module sf_trading.tests.test_item_search_words
"""

from unittest import mock

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import add_days, nowdate

from sf_trading.api.item_search import _apply_word_search, items_matching_every_word


class TestItemSearchWords(FrappeTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		cls.tube = cls._item("QQTEST MS TUBE 20X20X6 1.2MM", "square tube")
		cls.plate = cls._item("QQTEST CHEQUERED PLATE 1.22X2.44", "plate for ms works")
		cls.barcoded = cls._item("QQTEST WIDGET", "plain", barcode="QQBAR778899")

	@staticmethod
	def _item(item_name, description, barcode=None):
		item = frappe.get_doc(
			{
				"doctype": "Item",
				"item_code": item_name,
				"item_name": item_name,
				"description": description,
				"item_group": frappe.db.get_value("Item Group", {"is_group": 0}, "name"),
				"stock_uom": frappe.db.get_value("UOM", {}, "name"),
				"is_stock_item": 0,
			}
		)
		if barcode:
			item.append("barcodes", {"barcode": barcode})
		# item naming may rename the code -- use what was saved
		return item.insert(ignore_permissions=True).name

	def test_single_word_is_left_to_erpnext(self):
		self.assertIsNone(items_matching_every_word("qqtest"))

	def test_words_in_any_order(self):
		for txt in ("qqtest ms 1.2", "1.2 ms qqtest", "tube qqtest 20x20 ms"):
			self.assertIn(self.tube, items_matching_every_word(txt), txt)

	def test_every_word_must_match(self):
		self.assertEqual(items_matching_every_word("qqtest ms zzzz"), [])

	def test_name_matches_win_over_description_matches(self):
		"""The plate's "ms" is only in its description: with a name match available it stays out."""
		found = items_matching_every_word("qqtest ms 1.2")
		self.assertIn(self.tube, found)
		self.assertNotIn(self.plate, found)

	def test_description_is_the_fallback(self):
		self.assertIn(self.plate, items_matching_every_word("qqtest plate works"))

	def test_barcode_is_the_fallback(self):
		self.assertIn(self.barcoded, items_matching_every_word("qqtest qqbar778899"))

	def test_like_wildcards_are_literal(self):
		self.assertEqual(items_matching_every_word("qqtest %"), [])
		self.assertEqual(items_matching_every_word("qqtest _"), [])

	def test_company_restriction_is_kept(self):
		# the company's items are applied BEFORE choosing the stage: the tube's name match is no use
		# to a company that only has the plate, so it must not hide the plate's description match
		txt, filters, no_match = _apply_word_search("qqtest ms 1.2", {"name": ["in", [self.plate]]})
		self.assertFalse(no_match)
		self.assertEqual(filters["name"], ["in", [self.plate]])

		txt, filters, no_match = _apply_word_search("qqtest ms 1.2", {"name": ["in", [self.barcoded]]})
		self.assertTrue(no_match)

		txt, filters, no_match = _apply_word_search("qqtest ms 1.2", {"name": ["in", [self.tube, self.plate]]})
		self.assertFalse(no_match)
		self.assertEqual(txt, "")
		self.assertEqual(filters["name"], ["in", [self.tube]])

	def test_items_erpnext_would_not_offer_are_left_out(self):
		hidden = {
			"disabled": self._item("QQTEST MS DISABLED 1.2MM", "x"),
			"expired": self._item("QQTEST MS EXPIRED 1.2MM", "x"),
		}
		frappe.db.set_value("Item", hidden["disabled"], "disabled", 1)
		frappe.db.set_value("Item", hidden["expired"], "end_of_life", add_days(nowdate(), -1))
		found = items_matching_every_word("qqtest ms 1.2")
		for code in hidden.values():
			self.assertNotIn(code, found)
		self.assertIn(self.tube, found)

	def test_a_name_filter_that_is_not_an_in_list_is_left_to_erpnext(self):
		filters = {"name": ["not in", [self.plate]]}
		self.assertEqual(_apply_word_search("qqtest ms 1.2", filters), ("qqtest ms 1.2", filters, False))

	def test_a_party_item_rule_is_left_to_erpnext(self):
		# ERPNext overwrites filters["name"] with the party's own Item rule, so narrowing here would
		# be thrown away together with the text we blank
		filters = {"customer": "Anyone"}
		with mock.patch.object(frappe.db, "exists", return_value=True) as exists:
			result = _apply_word_search("qqtest ms 1.2", filters)
		self.assertEqual(result, ("qqtest ms 1.2", filters, False))
		self.assertEqual(
			exists.call_args.args, ("Party Specific Item", {"party": "Anyone", "restrict_based_on": "Item"})
		)

	def test_a_single_word_is_never_touched(self):
		filters = {"name": ["in", [self.tube]]}
		self.assertEqual(_apply_word_search("qqtest", filters), ("qqtest", filters, False))
