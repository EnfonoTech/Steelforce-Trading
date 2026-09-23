"""Tests for the Item naming split (GS Issue 14).

    bench --site <scratch-site> run-tests --module sf_trading.tests.test_item_naming
"""

from unittest.mock import patch

from frappe.tests.utils import FrappeTestCase

from sf_trading import item_naming

MAKE_AUTONAME = "sf_trading.item_naming.make_autoname"


class StubDoc:
	def __init__(self, doctype, **fields):
		self.doctype = doctype
		self.name = None
		self.__dict__.update({k: v for k, v in fields.items() if k != "name"})

	def get(self, key, default=None):
		return self.__dict__.get(key, default)


class TestItemAutoname(FrappeTestCase):
	def test_a_stock_item_is_left_to_stock_settings(self):
		"""No `.name` set here at all -- naming.py's own fallback to meta.autoname then runs,
		which is Stock Settings' existing (already-correct) manual/Item-Code entry."""
		doc = StubDoc("Item", is_stock_item=1, item_group="Raw Material")
		with patch(MAKE_AUTONAME) as make:
			item_naming.autoname(doc)
		make.assert_not_called()
		self.assertIsNone(doc.name)

	def test_a_service_item_gets_the_generic_series(self):
		doc = StubDoc("Item", is_stock_item=0, item_group="Consulting")
		with patch(MAKE_AUTONAME, return_value="SVC-GEN-00001") as make:
			item_naming.autoname(doc)
		make.assert_called_once_with(item_naming._DEFAULT_SERVICE_SERIES, "Item", doc=doc)
		self.assertEqual(doc.name, "SVC-GEN-00001")

	def test_a_mapped_item_group_gets_its_own_series(self):
		doc = StubDoc("Item", is_stock_item=0, item_group="Services")
		with patch(MAKE_AUTONAME, return_value="SVC-00001") as make:
			item_naming.autoname(doc)
		make.assert_called_once_with(
			item_naming._SERVICE_GROUP_SERIES["Services"], "Item", doc=doc
		)
