# sf_trading/patches/v0_1/seed_party_master_rules.py
"""Seed the Customer and Supplier Party Master Rule Sets with the rules the code enforces today.

Never touches a set that exists. Not enabled: the code's own rules still answer until each consumer is switched
over (sf_trading/party_rules.py).
"""

import frappe

from sf_trading import party_rules, party_rules_seed


def execute():
	frappe.reload_doc("sf_trading", "doctype", "party_rule_override_role")
	for name in ("party_rule_required", "party_rule_format", "party_rule_unique", "party_rule_exemption",
			"party_master_rule_set"):
		frappe.reload_doc("sf_trading", "doctype", name)
	party_rules.setup()
	party_rules_seed.seed()
