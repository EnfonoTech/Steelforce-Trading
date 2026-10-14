# sf_trading/patches/v0_1/seed_naming_switches.py
"""Tick the two house-standard switches on the sites that already have the settings (sf_trading/title_case.py,
sf_trading/account_numbering.py).

A new field's default only reaches a document made after it arrived, never a Single that was saved before
(see the migrate notes in the repo), so SF Trading Settings would show both unticked while the code, which reads
a switch nobody ever set as on, runs them. Only a switch with no stored value is filled: one somebody has already
unticked, or ticked, is left as it is.
"""

import frappe

SETTINGS = "SF Trading Settings"
SWITCHES = ("title_case_masters", "number_new_accounts")


def execute():
	if not frappe.db.exists("DocType", SETTINGS):
		return
	stored = frappe.db.get_singles_dict(SETTINGS) or {}
	for fieldname in SWITCHES:
		if stored.get(fieldname) is None:
			frappe.db.set_single_value(SETTINGS, fieldname, 1, update_modified=False)
	frappe.db.commit()
