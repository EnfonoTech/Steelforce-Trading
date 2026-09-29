"""Tests for the shadow Asset created on a custom_track_as_asset item's purchase.

Covers the three invariants the feature promises: a tracked, non-fixed-asset item produces
exactly one draft Asset (no capitalization, no depreciation) on submit; an untracked item
produces none; and a hook that somehow fires twice for the same purchase row never doubles it.

    bench --site <scratch-site> run-tests --module sf_trading.tests.test_asset_tracking_item
"""

import frappe
from frappe.tests.utils import FrappeTestCase

from sf_trading.asset_tracking_item import (
	SOURCE_PI_FIELD,
	SOURCE_ROW_FIELD,
	TRACK_FIELD,
	backfill_tracking_assets,
	create_tracking_asset,
)
from sf_trading.tests.test_open_items import SUPPLIER, TestOpenItems

LOCATION = "SF Test Asset Tracking Location"


class TestAssetTrackingItem(FrappeTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		TestOpenItems.setUpClass()
		cls.company = TestOpenItems.company.name
		cls.cost_center = TestOpenItems.cost_center
		cls.location = cls.make_location()
		# non-stock, non-fixed-asset items: exactly the "laptop bought and expensed" case this
		# feature is for -- a stock item is excluded on purpose, see asset_tracking_item.py
		cls.tracked_item = cls.make_item("SF Test Tracked Asset Item", track=1)
		cls.plain_item = cls.make_item("SF Test Untracked Expense Item", track=0)

	@classmethod
	def make_location(cls):
		if not frappe.db.exists("Location", LOCATION):
			frappe.get_doc({"doctype": "Location", "location_name": LOCATION}).insert()
		return LOCATION

	@classmethod
	def make_item(cls, name, track):
		from erpnext.stock.doctype.item.test_item import make_item

		# stock_uom spelled out for the same reason test_open_items.py spells it out: this
		# site's Item defaults were cleared, so make_item cannot insert without it
		return make_item(
			name,
			properties={
				"is_stock_item": 0,
				"is_fixed_asset": 0,
				"item_group": "Products",
				"stock_uom": "Nos",
				TRACK_FIELD: track,
			},
		).name

	def make_invoice(self, item_code, asset_location=None):
		"""A direct Purchase Invoice, the way a business expensing an item straight away would."""
		pi = frappe.get_doc(
			{
				"doctype": "Purchase Invoice",
				"company": self.company,
				"supplier": SUPPLIER,
				"cost_center": self.cost_center,
				"items": [
					{
						"item_code": item_code,
						"qty": 1,
						"rate": 1000,
						"cost_center": self.cost_center,
						"asset_location": asset_location,
					}
				],
			}
		)
		TestOpenItems.fill_site_mandatories(pi)
		pi.insert()
		pi.submit()
		return pi

	def tracking_assets(self, pi):
		return frappe.get_all(
			"Asset",
			filters={SOURCE_PI_FIELD: pi.name},
			fields=[
				"name",
				"docstatus",
				"is_existing_asset",
				"calculate_depreciation",
				"location",
				SOURCE_ROW_FIELD,
			],
		)

	def test_a_tracked_item_gets_exactly_one_draft_asset(self):
		pi = self.make_invoice(self.tracked_item, asset_location=self.location)

		assets = self.tracking_assets(pi)
		self.assertEqual(len(assets), 1, "exactly one Asset for the one tracked row")

		asset = assets[0]
		self.assertEqual(asset.docstatus, 0, "left as a draft so staff can complete it")
		self.assertEqual(int(asset.is_existing_asset), 1, "no fresh capitalization")
		self.assertEqual(int(asset.calculate_depreciation), 0, "no depreciation schedule")
		self.assertEqual(asset.location, self.location)
		self.assertEqual(
			asset.get(SOURCE_ROW_FIELD),
			pi.items[0].name,
			"stamped with the exact row that created it, for the idempotency guard",
		)

	def test_an_untracked_item_gets_none(self):
		pi = self.make_invoice(self.plain_item, asset_location=self.location)
		self.assertFalse(
			self.tracking_assets(pi),
			"is_fixed_asset=0 and custom_track_as_asset=0: nothing here asked to be tracked",
		)

	def test_replaying_the_hook_does_not_duplicate(self):
		"""A resubmit/replay firing on_submit twice for the same row must not double the Asset."""
		pi = self.make_invoice(self.tracked_item, asset_location=self.location)
		self.assertEqual(len(self.tracking_assets(pi)), 1)

		create_tracking_asset(pi)  # simulate the hook firing again for the same document

		self.assertEqual(
			len(self.tracking_assets(pi)),
			1,
			"the per-row idempotency guard must stop a second Asset for the same purchase row",
		)

	def test_no_location_skips_quietly_instead_of_raising(self):
		"""No row location, no Warehouse mapping, no Settings default for this Company: skip,
		don't blow up the submit that's already in flight (Asset's own mandatory-field check
		would otherwise throw)."""
		settings = frappe.get_single("SF Trading Settings")
		settings.set("asset_tracking_defaults", [])
		settings.save()
		pi = self.make_invoice(self.tracked_item, asset_location=None)  # must not raise
		self.assertFalse(
			self.tracking_assets(pi),
			"no Location resolvable anywhere -- logged for staff to finish by hand, not created",
		)

	def test_company_default_location_is_used_when_row_is_blank(self):
		"""Falls back to this purchase's own Company's row in Asset Tracking Defaults."""
		settings = frappe.get_single("SF Trading Settings")
		settings.set("asset_tracking_defaults", [])
		settings.append(
			"asset_tracking_defaults", {"company": self.company, "default_asset_location": self.location}
		)
		settings.save()

		pi = self.make_invoice(self.tracked_item, asset_location=None)
		assets = self.tracking_assets(pi)
		self.assertEqual(len(assets), 1)
		self.assertEqual(assets[0].location, self.location)

	def test_a_different_companys_default_is_not_used(self):
		"""A row for some OTHER Company must not leak into this purchase's Location."""
		settings = frappe.get_single("SF Trading Settings")
		settings.set("asset_tracking_defaults", [])
		settings.append(
			"asset_tracking_defaults",
			{"company": "SF Test Asset Tracking Other Co", "default_asset_location": self.location},
		)
		settings.flags.ignore_links = True  # the other Company need not exist for this check
		settings.save()

		pi = self.make_invoice(self.tracked_item, asset_location=None)
		self.assertFalse(
			self.tracking_assets(pi),
			"a default configured for a different Company must not be picked up here",
		)

	def test_the_draft_can_be_saved_and_submitted_normally_afterward(self):
		"""Confirmed live on UAT (2026-09-29): opening the created draft and saving it as staff
		normally would (fill custodian, submit) hit core's own Asset.validate_item(), which
		refuses any Asset whose Item isn't a Fixed Asset Item -- on every save, not only the
		insert `_create_for_row` already bypasses via ignore_validate. Without the
		override_doctype_class fix (sf_trading.overrides.asset_class.CustomAsset), the draft is
		permanently stuck: this is the regression guard for that fix."""
		pi = self.make_invoice(self.tracked_item, asset_location=self.location)
		asset = frappe.get_doc("Asset", self.tracking_assets(pi)[0].name)

		asset.custodian = "Administrator"
		asset.save()  # must not raise "Item ... must be a Fixed Asset Item"
		asset.reload()
		self.assertEqual(asset.custodian, "Administrator")

		asset.submit()  # must behave like an ordinary existing Asset from here on
		self.assertEqual(asset.docstatus, 1)

	def test_an_ordinary_non_fixed_asset_item_is_still_refused(self):
		"""The override above must not blanket-disable the check for everyone -- only for an
		Asset this app actually stamped with SOURCE_ROW_FIELD."""
		asset = frappe.get_doc(
			{
				"doctype": "Asset",
				"asset_name": "SF Test Not Our Asset",
				"item_code": self.plain_item,
				"company": self.company,
				"location": self.location,
				"is_existing_asset": 1,
				"calculate_depreciation": 0,
				"gross_purchase_amount": 100,
				"purchase_amount": 100,
			}
		)
		self.assertRaises(frappe.ValidationError, asset.insert)

	def test_backfill_creates_for_an_already_submitted_purchase(self):
		"""Item ticked custom_track_as_asset AFTER an old purchase already posted: backfill must
		still create the Asset for that historical row, not only ones submitted from here on."""
		from erpnext.stock.doctype.item.test_item import make_item

		late_item = make_item(
			"SF Test Late Tracked Item",
			properties={
				"is_stock_item": 0,
				"is_fixed_asset": 0,
				"item_group": "Products",
				"stock_uom": "Nos",
				TRACK_FIELD: 0,  # NOT tracked yet at the time of purchase
			},
		).name

		pi = self.make_invoice(late_item, asset_location=self.location)
		self.assertFalse(self.tracking_assets(pi), "not tracked yet -- nothing created on submit")

		frappe.db.set_value("Item", late_item, TRACK_FIELD, 1)  # ticked retroactively

		result = backfill_tracking_assets(item_code=late_item)
		self.assertEqual(result["created"], 1)
		self.assertEqual(
			len(self.tracking_assets(pi)),
			1,
			"backfill must create the Asset for the historical row now that the Item is tracked",
		)

		# idempotent: running it again must not duplicate
		backfill_tracking_assets(item_code=late_item)
		self.assertEqual(len(self.tracking_assets(pi)), 1)
