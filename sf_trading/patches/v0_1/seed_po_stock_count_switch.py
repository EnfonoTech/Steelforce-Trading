# sf_trading/patches/v0_1/seed_po_stock_count_switch.py
"""Tick SF Trading Settings > "Require Physical Stock Count on Purchase Orders" on the sites that already have the settings
(sf_trading/po_stock_count.py).

The code reads a switch nobody stored as ON (switches.py), but a new field's default only ever reaches a Single created after it, so the settings
page would show the switch unticked while the code runs it. Only a switch with no stored value is filled: one somebody has already unticked is left
as it is.
"""

import frappe

SETTINGS = "SF Trading Settings"
SWITCH = "require_po_stock_count"


def execute():
	if not frappe.db.exists("DocType", SETTINGS):
		return
	if (frappe.db.get_singles_dict(SETTINGS) or {}).get(SWITCH) is None:
		frappe.db.set_single_value(SETTINGS, SWITCH, 1, update_modified=False)
	frappe.db.commit()
