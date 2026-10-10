# sf_trading/patches/v0_1/set_master_rules_from.py
"""Start the new-master rules at the moment they arrive (sf_trading/party_documents.py).

A date and time, not a date: a master created earlier on the deploy day was made before anything
asked for its documents, and must not be caught by them. Masters that already exist are never
blocked by these rules; only customers and suppliers created from now on are. A start already set
by hand is left alone.
"""

import frappe
from frappe.utils import now_datetime


def execute():
	settings = "SF Trading Settings"
	if not frappe.db.exists("DocType", settings):
		return
	if (frappe.db.get_singles_dict(settings) or {}).get("master_rules_from"):
		return
	frappe.db.set_single_value(settings, "master_rules_from", now_datetime())
	frappe.db.commit()
