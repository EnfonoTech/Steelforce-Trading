# sf_trading/patches/v0_1/po_procurement_rules_by_company.py
"""Carry the one set of Purchase Order procurement settings (0.1.66, UAT only) into the per-company table
SF Trading Settings > Purchasing > Purchase Order Rules (sf_trading/purchase_order_procurement.py).

0.1.66 held a single switch and four numbers for every company. If the switch was ticked, each company gets a row with those
numbers and Enforce ticked, so the rules behave exactly as they did; if it was never ticked (or nothing was ever stored, as on
every site that did not run 0.1.66) there is nothing to carry over and no company is enforced. The old rows of the Single are
deleted either way: no field reads them any more. A table that already has rows is left as it is.
"""

import frappe
from frappe.utils import cint, flt

SETTINGS = "SF Trading Settings"
RULE_TABLE = "SF Purchase Order Rule"
RULE_FIELD = "purchase_order_rules"

SWITCH = "enforce_po_procurement"
OLD = (
	SWITCH,
	"po_local_quotation_above",
	"po_local_min_quotations",
	"po_import_min_quotations",
	"po_verbal_skips_quotations",
)


def _number(stored, field, default):
	value = stored.get(field)
	return default if value in (None, "") else value


def execute():
	if not frappe.db.exists("DocType", SETTINGS) or not frappe.db.table_exists(RULE_TABLE):
		return
	stored = frappe.db.get_singles_dict(SETTINGS) or {}
	if cint(stored.get(SWITCH)) and not frappe.db.count(RULE_TABLE, {"parent": SETTINGS, "parentfield": RULE_FIELD}):
		settings = frappe.get_single(SETTINGS)
		for company in frappe.get_all("Company", pluck="name", order_by="creation"):
			settings.append(
				RULE_FIELD,
				{
					"company": company,
					"enforce": 1,
					"local_quotation_above": flt(_number(stored, "po_local_quotation_above", 500)),
					"local_min_quotations": cint(_number(stored, "po_local_min_quotations", 3)),
					"import_min_quotations": cint(_number(stored, "po_import_min_quotations", 2)),
					"verbal_skips_quotations": cint(_number(stored, "po_verbal_skips_quotations", 1)),
					# 0.1.66 never excused a verbal order its Material Request
					"verbal_skips_material_request": 0,
				},
			)
		settings.save(ignore_permissions=True)
	frappe.db.sql("DELETE FROM `tabSingles` WHERE `doctype` = %s AND `field` IN %s", (SETTINGS, OLD))
	frappe.db.commit()
