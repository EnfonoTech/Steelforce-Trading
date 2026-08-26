# sf_trading/sf_trading/report/sales_target_scorecard/sales_target_scorecard.py
"""Month-to-date and year-to-date against target, one row per branch or per salesman.

This is the report the dashboard's number cards read, and the one to open on a Monday morning:
narrow enough to take in at a glance, unlike the twelve-month variance grid.

`Target to Date` prorates the current month by the days elapsed. Without it every card reads
"behind" on the 2nd of the month, which trains people to ignore it.
"""

import frappe
from frappe import _

from sf_trading.sales_target import scorecard


def execute(filters=None):
	filters = frappe._dict(filters or {})
	company = filters.company or frappe.defaults.get_user_default("Company")
	fiscal_year = filters.fiscal_year or frappe.defaults.get_user_default("fiscal_year")
	dimension = filters.dimension_type or "Branch"
	currency = frappe.get_cached_value("Company", company, "default_currency") if company else None

	rows = scorecard(company, fiscal_year, dimension, filters.basis or "Net of VAT",
	                 filters.as_on, filters.branch)
	for r in rows:
		r["currency"] = currency

	label = _("Branch") if dimension == "Branch" else _("Sales Person")
	money = lambda fn, lb, w=130: {"label": lb, "fieldname": fn, "fieldtype": "Currency",
	                               "options": "currency", "width": w}
	columns = [
		{"label": label, "fieldname": "dimension_value", "fieldtype": "Data", "width": 170},
		money("mtd_target", _("MTD Target")),
		money("mtd_target_to_date", _("MTD Target to Date")),
		money("mtd_actual", _("MTD Actual")),
		{"label": _("MTD %"), "fieldname": "mtd_pct", "fieldtype": "Percent", "width": 95},
		money("ytd_target", _("YTD Target")),
		money("ytd_actual", _("YTD Actual")),
		money("variance", _("YTD Variance")),
		{"label": _("YTD %"), "fieldname": "ytd_pct", "fieldtype": "Percent", "width": 95},
		{"label": _("Currency"), "fieldname": "currency", "fieldtype": "Link",
		 "options": "Currency", "hidden": 1, "width": 80},
	]
	return columns, rows
