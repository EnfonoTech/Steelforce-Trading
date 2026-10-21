# sf_trading/patches/v0_1/po_rule_verbal_exempt.py
"""A verbal Purchase Order is exempt from the whole chain (0.1.69): Material Request and quotations alike.

0.1.67 and 0.1.68 had two verbal ticks on SF Purchase Order Rule (`verbal_skips_quotations`, `verbal_skips_material_request`);
they became one, `verbal_skips_rules`, ticked by default. A rule row made while those two existed carries neither value over, so
the new tick is set on every existing row that has none stored: a row somebody made has always meant "a verbal order is
exempt". The two old columns stay in the table, unread.
"""

import frappe

RULE_TABLE = "SF Purchase Order Rule"


def execute():
	if not frappe.db.table_exists(RULE_TABLE) or not frappe.db.has_column(RULE_TABLE, "verbal_skips_rules"):
		return
	frappe.db.sql("UPDATE `tabSF Purchase Order Rule` SET `verbal_skips_rules` = 1 WHERE `verbal_skips_rules` IS NULL")
	frappe.db.commit()
