import frappe
from erpnext.assets.doctype.asset.asset import Asset

from sf_trading.asset_tracking_item import SOURCE_ROW_FIELD


class CustomAsset(Asset):
	"""Core Asset, with the capitalization paths switched off for a tracking-only Asset.

	A tracking Asset is one `sf_trading.asset_tracking_item` created for an expensed
	(`custom_track_as_asset`) item: it exists for location / custodian / movement history, books no
	GL of its own and never depreciates. It is recognised by `custom_source_row`, which only that
	module stamps. Core Asset assumes every record is a capitalized Fixed Asset, so several of its
	methods go and look for a Fixed Asset Account in the Asset Category -- which a tracking Asset has
	not got, and must not be given. Each such method is answered here for a tracking Asset and left
	to core for every other Asset.
	"""

	def is_tracking_asset(self) -> bool:
		return bool(self.get(SOURCE_ROW_FIELD))

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
		if self.is_tracking_asset():
			return
		super().validate_item()

	@frappe.whitelist()
	def get_manual_depreciation_entries(self):
		"""The manual depreciation journal entries of this Asset -- none, for a tracking Asset.

		Core's Asset form calls this on every refresh of a submitted Asset that has no depreciation
		schedule (asset.js, setup_chart_and_depr_schedule_view), to draw the value chart, and core
		calls it again from `delete_depreciation_entries` when the Asset is cancelled. It starts by
		reading the Fixed Asset Account of the Asset Category and throws "Please set Fixed Asset
		Account in Asset Category None" when there is none (depreciation.get_depreciation_accounts).
		A tracking Asset has no category and no depreciation, so there is nothing to read and nothing
		to find: this is the throw that came up whenever a submitted tracking Asset was opened.
		"""
		if self.is_tracking_asset():
			return []
		return super().get_manual_depreciation_entries()

	def delete_depreciation_entries(self):
		"""On cancel: a tracking Asset has booked no depreciation entry, so there is none to cancel
		(and core would look for the Fixed Asset Account to find out -- see above)."""
		if self.is_tracking_asset():
			return
		super().delete_depreciation_entries()

	def validate_make_gl_entry(self):
		"""On submit: a tracking Asset never capitalizes. Core would book Fixed Asset against CWIP
		for an Asset raised from a Purchase Receipt, and throw "Set Fixed Asset Account in asset
		category ..." when the category has none -- the purchase's own expense entry is all the
		accounting there is."""
		if self.is_tracking_asset():
			return False
		return super().validate_make_gl_entry()

	def make_gl_entries(self):
		"""Also reached without submit: core's daily make_post_gl_entry books the GL of an Asset
		whose Category has CWIP accounting on, on its available-for-use date."""
		if self.is_tracking_asset():
			return
		super().make_gl_entries()
