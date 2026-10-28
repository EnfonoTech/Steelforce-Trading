# sf_trading/patches/v0_1/seed_similar_masters_switch.py
"""Tick SF Trading Settings > "Show Similar Names While Creating Masters" on the sites that already have the settings
(sf_trading/api/duplicate_check.py).

The duplicate-name warning it grew out of was always on, so the switch ships ON; but a new field's default only ever
reaches a Single created after it, so on an existing site the warning would go quiet until somebody found the switch.
Only a switch with no stored value is filled: one somebody has already unticked is left as it is.
"""

import frappe

SETTINGS = "SF Trading Settings"
SWITCH = "suggest_similar_masters"


def execute():
	if not frappe.db.exists("DocType", SETTINGS):
		return
	if (frappe.db.get_singles_dict(SETTINGS) or {}).get(SWITCH) is None:
		frappe.db.set_single_value(SETTINGS, SWITCH, 1, update_modified=False)
	frappe.db.commit()
