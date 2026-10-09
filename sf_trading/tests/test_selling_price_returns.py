"""A return is not price-checked against today's cost (sf_trading.api.selling_price_validation).

    bench --site <scratch-site> run-tests --module sf_trading.tests.test_selling_price_returns

Why: the save-time floor is max(last purchase rate, valuation) x (1 + margin) at TODAY's cost. A
return gives goods back at the price they were SOLD at, so once cost rises above an old sale price
the return was refused ("Minimum selling price is 28.369" on a delivery note sold at 22.500 and
returned a few months later). These run against stubbed master data, so they need no site data.
"""

from unittest import mock

import frappe
from frappe.tests.utils import FrappeTestCase

from sf_trading.api.selling_price_validation import validate_selling_price

ITEM = "_TEST-SP-RETURN-ITEM"
FLOOR = 26.268 * 1.08  # last purchase rate x (1 + 8% group margin) = 28.36944, shown as 28.369


def stub_masters(doctype, filters=None, fieldname=None, *args, **kwargs):
	"""Master data the validator reads: an item whose cost puts the floor at 28.369."""
	values = {
		("Item", ITEM, "last_purchase_rate"): 26.268,
		("Item", ITEM, "item_group"): "_Test SP Group",
		("Item", ITEM, "is_stock_item"): 0,
		("Item Group", "_Test SP Group", "custom_min_margin_pct"): 8.0,
		("Item Group", "_Test SP Group", "parent_item_group"): None,
	}
	return values.get((doctype, filters, fieldname))


class Document:
	"""Just enough of a Document: attribute access plus .get (a frappe._dict would answer
	`doc.items` with the dict method rather than the rows)."""

	def __init__(self, **values):
		self.__dict__.update(values)

	def get(self, key, default=None):
		return self.__dict__.get(key, default)


def document(rate, is_return=0, rows=1):
	return Document(
		doctype="Delivery Note",
		customer=None,
		is_return=is_return,
		company_currency="BHD",
		selling_price_list=None,
		plc_conversion_rate=1,
		items=[
			frappe._dict(
				idx=i + 1,
				item_code=ITEM,
				base_net_rate=rate,
				conversion_factor=1,
				warehouse=None,
				is_free_item=0,
			)
			for i in range(rows)
		],
	)


class TestSellingPriceOnReturns(FrappeTestCase):
	def setUp(self):
		patcher = mock.patch.object(frappe.db, "get_value", side_effect=stub_masters)
		patcher.start()
		self.addCleanup(patcher.stop)

	def test_a_sale_below_the_floor_is_still_refused(self):
		with self.assertRaises(frappe.ValidationError) as ctx:
			validate_selling_price(document(rate=22.5))
		self.assertIn("28.369", str(ctx.exception))

	def test_a_sale_at_the_floor_passes(self):
		validate_selling_price(document(rate=FLOOR))  # must not raise

	def test_a_return_at_the_original_price_is_not_checked(self):
		# MAT-DN-2026-00071 sold this at 22.500; today's floor is 28.369
		validate_selling_price(document(rate=22.5, is_return=1))  # must not raise

	def test_every_row_of_a_return_is_skipped(self):
		validate_selling_price(document(rate=1.0, is_return=1, rows=4))  # must not raise

	def test_the_same_rows_without_the_return_flag_are_refused(self):
		with self.assertRaises(frappe.ValidationError):
			validate_selling_price(document(rate=22.5, is_return=0))

	def test_a_missing_or_zero_flag_means_a_normal_sale(self):
		doc = document(rate=22.5)
		del doc.is_return
		with self.assertRaises(frappe.ValidationError):
			validate_selling_price(doc)
