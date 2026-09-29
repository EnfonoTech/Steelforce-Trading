from erpnext.assets.doctype.asset.asset import Asset

from sf_trading.asset_tracking_item import SOURCE_ROW_FIELD


class CustomAsset(Asset):
	def validate_item(self):
		"""Lets a custom_track_as_asset shadow Asset be saved and submitted normally after its
		initial insert, not only that one time.

		Core refuses any Asset whose Item is not is_fixed_asset -- by design, since an ordinary
		Asset always capitalizes a fixed-asset Item. asset_tracking_item.py's own insert bypasses
		this once via flags.ignore_validate (see its module docstring), but that flag only covers
		the insert call itself: the very next time staff open the resulting draft to add a
		custodian and save it normally, or submit it, validate() runs for real and hits this same
		throw again -- permanently un-savable, which defeats the whole feature (confirmed live,
		2026-09-29: ACC-ASS-2026-00050 refused "Item GRN Billing Clearing must be a Fixed Asset
		Item" on an ordinary save).

		Skips only for an Asset this app actually created (stamped with our own
		custom_source_row) -- every other Asset, and every other check further down this same
		validate() on THIS Asset, is untouched.
		"""
		if self.get(SOURCE_ROW_FIELD):
			return
		super().validate_item()
