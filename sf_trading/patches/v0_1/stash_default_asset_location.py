# sf_trading/patches/v0_1/stash_default_asset_location.py
"""Half of moving SF Trading Settings' single Default Tracking Asset Location field into the new
per-Company Asset Tracking Defaults table (see sf_trading/asset_tracking_item.py).

Runs [pre_model_sync], before the old field is dropped from the DocType and the new child table
is created -- the counterpart patch, seed_asset_tracking_default_row (post_model_sync), can't read
the old value directly because the new child doctype's table doesn't exist yet at that point in a
fresh migrate. Stashed in cache rather than written anywhere durable, since the whole point is to
hand it to the very next patch a few seconds later in the same migrate run.
"""

import frappe

CACHE_KEY = "sf_trading_migrate_default_asset_location"


def execute():
	# raw SQL, not frappe.db.get_value: Singles is a low-level key-value table with no `modified`
	# column, and get_value's implicit default ordering assumes one on every table it queries
	rows = frappe.db.sql(
		"SELECT value FROM `tabSingles` WHERE doctype=%s AND field=%s",
		("SF Trading Settings", "default_asset_location"),
	)
	value = rows[0][0] if rows else None
	if value:
		frappe.cache.set_value(CACHE_KEY, value, expires_in_sec=3600)
