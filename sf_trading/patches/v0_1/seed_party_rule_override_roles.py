# sf_trading/patches/v0_1/seed_party_rule_override_roles.py
"""Give a Party Master Rule Set seeded by 0.1.88 the roles that may override a Unique Check.

0.1.88 kept them in a Table MultiSelect inside the Unique Checks rows, which Frappe does not keep for a child
row, so every set came out with none. They are now a table of the rule set itself. A set that already lists
roles is left alone.
"""

import frappe

from sf_trading import party_rules_seed


def execute():
	for name in ("party_rule_unique", "party_rule_override_role", "party_master_rule_set"):
		frappe.reload_doc("sf_trading", "doctype", name)
	party_rules_seed.add_override_roles()
