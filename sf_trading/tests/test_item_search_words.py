"""Tests for the any-order, every-word item search (api/item_search.items_matching_every_word).

    bench --site <scratch-site> run-tests --module sf_trading.tests.test_item_search_words
"""

import frappe
from frappe.tests.utils import FrappeTestCase

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
		txt, filters, no_match = _apply_word_search("qqtest ms 1.2", {"name": ["in", [self.plate]]})
		self.assertTrue(no_match)

		txt, filters, no_match = _apply_word_search("qqtest ms 1.2", {"name": ["in", [self.tube, self.plate]]})
		self.assertFalse(no_match)
		self.assertEqual(txt, "")
		self.assertEqual(filters["name"], ["in", [self.tube]])
