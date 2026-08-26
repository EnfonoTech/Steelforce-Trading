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
	                 filters.as_on, filters.branch,
	                 from_date=filters.from_date, to_date=filters.to_date)
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
		{"label": _("MTD %"), "fieldname": "mtd_pct", "fieldtype": "Percent", "width": 95,
		 "disable_total": 1},
		money("ytd_target", _("YTD Target")),
		money("ytd_actual", _("YTD Actual")),
		money("variance", _("YTD Variance")),
		{"label": _("YTD %"), "fieldname": "ytd_pct", "fieldtype": "Percent", "width": 95,
		 "disable_total": 1},
		{"label": _("Currency"), "fieldname": "currency", "fieldtype": "Link",
		 "options": "Currency", "hidden": 1, "width": 80},
	]
	named = [r for r in rows if r["dimension_value"] != "Unassigned"]
	chart = {
		"data": {
			"labels": [r["dimension_value"] for r in named][:10],
			"datasets": [{"name": _("YTD %"), "values": [round(r["ytd_pct"], 1) for r in named][:10]}],
			"yMarkers": [{"label": _("Target"), "value": 100}],
		},
		"type": "bar",
		"colors": ["#29cd42"],
	}
	# the same rule as the variance reports: Unassigned is shown, never counted in the headline
	attributed = [r for r in rows if r["dimension_value"] != "Unassigned"]
	ytd_t = sum(r["ytd_target"] for r in attributed)
	ytd_a = sum(r["ytd_actual"] for r in attributed)
	pct = (ytd_a / ytd_t * 100) if ytd_t else 0
	summary = [
		{"label": _("YTD Target"), "value": ytd_t, "datatype": "Currency", "currency": currency},
		{"label": _("YTD Actual"), "value": ytd_a, "datatype": "Currency", "currency": currency},
		{"label": _("Variance"), "value": ytd_a - ytd_t, "datatype": "Currency",
		 "currency": currency, "indicator": "Green" if ytd_a >= ytd_t else "Red"},
		{"label": _("Achieved"), "value": pct, "datatype": "Percent",
		 "indicator": "Green" if pct >= 100 else "Orange" if pct >= 80 else "Red"},
	]
	return columns, rows, None, chart, summary
