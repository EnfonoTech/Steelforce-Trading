# sf_trading/patches/v0_1/seed_po_procurement_settings.py
"""Fill SF Trading Settings > Purchase Order Procurement Rules with the numbers the code already uses
(sf_trading/purchase_order_procurement.py), on the sites that already have the settings.

The code reads a number nobody stored as its default (500 / 3 / 2) and the verbal switch as ON, but a new field's default
only ever reaches a Single created after it, so the settings page would show 0 and an unticked switch while the code runs
the defaults, and saving the page would store those zeros. Only a field with no stored value is filled: a number somebody
has already set is left as it is. Enforcement itself is not touched: nobody having stored it already reads as off.
"""

import frappe

from sf_trading.purchase_order_procurement import DEFAULTS

SETTINGS = "SF Trading Settings"
SWITCH = "enforce_po_procurement"


def execute():
	if not frappe.db.exists("DocType", SETTINGS):
		return
	stored = frappe.db.get_singles_dict(SETTINGS) or {}
	for field, value in DEFAULTS.items():
		if field != SWITCH and stored.get(field) is None:
			frappe.db.set_single_value(SETTINGS, field, value, update_modified=False)
	frappe.db.commit()
