# sf_trading/patches/v0_1/seed_asset_tracking_default_row.py
"""Second half of moving SF Trading Settings' single Default Tracking Asset Location field into
the new per-Company Asset Tracking Defaults table (see sf_trading/asset_tracking_item.py and the
counterpart patch, stash_default_asset_location, [pre_model_sync]).

Seeds one row for Steel Force Trading WLL -- the one company every purchase tested against this
feature so far (this session) actually used, and the only company whose Location the old flat
field could have meant. Every other company gets no row and simply resolves no default until an
admin adds one -- exactly the same "logged, not guessed" behaviour as before this table existed.

Only runs once: nothing to do if the old field was empty, or a row for that company already
exists (a second migrate run, or an admin who has since added one by hand).
"""

import os

import frappe

from sf_trading.patches.v0_1.stash_default_asset_location import STASH_PATH

COMPANY = "Steel Force Trading WLL"


def execute():
	if not os.path.exists(STASH_PATH):
		return
	with open(STASH_PATH) as f:
		value = f.read().strip()
	os.remove(STASH_PATH)
	if not value:
		return

	settings = frappe.get_single("SF Trading Settings")
	if any(row.company == COMPANY for row in settings.get("asset_tracking_defaults") or []):
		return

	settings.append("asset_tracking_defaults", {"company": COMPANY, "default_asset_location": value})
	settings.save(ignore_permissions=True)
