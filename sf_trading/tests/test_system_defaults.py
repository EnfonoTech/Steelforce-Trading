"""Tests for the System Settings default-row sync.

The defect: `time_zone` was stored on the Single but had no `__default` row, so frappe's own
System Settings form wrote None over it on every load and the mandatory field could never be
saved. See sf_trading/system_defaults.py.

    bench --site <scratch-site> run-tests --module sf_trading.tests.test_system_defaults
"""

import frappe
from frappe.tests.utils import FrappeTestCase

from sf_trading import system_defaults


class TestSystemSettingsDefaults(FrappeTestCase):
	def setUp(self):
		self.settings = frappe.get_single("System Settings")

	def test_a_healthy_site_has_no_missing_defaults(self):
		"""Runs against this site as it stands — after the sync there is nothing left to fill."""
		system_defaults.sync_system_settings_defaults()
		self.assertEqual(system_defaults.missing_defaults(), {})

	def test_a_removed_default_is_detected_and_refilled(self):
		value = self.settings.get("time_zone")
		if not value:
			self.skipTest("this site has no time zone stored")

		frappe.db.sql("delete from tabDefaultValue where parent='__default' and defkey='time_zone'")
		frappe.cache.delete_key("defaults")
		frappe.local.default_objects = {}

		self.assertEqual(system_defaults.missing_defaults().get("time_zone"), value)

		filled = system_defaults.sync_system_settings_defaults()
		self.assertIn("time_zone", filled)
		frappe.cache.delete_key("defaults")
		frappe.local.default_objects = {}
		self.assertEqual(frappe.db.get_defaults().get("time_zone"), value)

	def test_an_existing_default_is_never_overwritten(self):
		"""A default that disagrees with the Single is somebody's decision — `app_name` is one."""
		frappe.db.set_default("time_zone", "Etc/UTC")
		frappe.cache.delete_key("defaults")
		frappe.local.default_objects = {}

		self.assertNotIn("time_zone", system_defaults.missing_defaults())
		system_defaults.sync_system_settings_defaults()
		frappe.cache.delete_key("defaults")
		frappe.local.default_objects = {}
		self.assertEqual(frappe.db.get_defaults().get("time_zone"), "Etc/UTC")

	def test_only_select_and_data_fields_are_synced(self):
		meta = frappe.get_meta("System Settings")
		for fieldname in system_defaults.missing_defaults():
			self.assertIn(meta.get_field(fieldname).fieldtype, system_defaults.SYNCED_FIELDTYPES)
