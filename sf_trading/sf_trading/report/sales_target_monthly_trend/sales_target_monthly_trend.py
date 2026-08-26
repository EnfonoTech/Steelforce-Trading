# sf_trading/sf_trading/report/sales_target_monthly_trend/sales_target_monthly_trend.py
"""Target against actual, month by month, for one branch or one salesman or all of them.

Rows are months rather than columns, which is what a Dashboard Chart of type Report needs: it
plots one x field against named y fields, so the twelve-month grid of the variance reports
cannot feed a chart and this can.
"""

import frappe
from frappe import _
from frappe.utils import flt

from sf_trading.sales_target import actuals, month_slots, targets


def execute(filters=None):
	filters = frappe._dict(filters or {})
	company = filters.company or frappe.defaults.get_user_default("Company")
	fiscal_year = filters.fiscal_year or frappe.defaults.get_user_default("fiscal_year")
	basis = filters.basis or "Net of VAT"
	currency = frappe.get_cached_value("Company", company, "default_currency") if company else None

	dimension = "Sales Person" if filters.sales_person else "Branch"

	# invoices are always narrowed by the branch filter; targets only when the dimension is a
	# person, because a branch's target carries no branch field of its own
	act = actuals(company, fiscal_year, dimension, basis, filters.branch)
	tgt = targets(company, fiscal_year, dimension,
	              filters.branch if dimension == "Sales Person" else None)

	def keep(name):
		if dimension == "Sales Person":
			return name == filters.sales_person
		return name == filters.branch if filters.branch else True

	data = []
	for slot in month_slots(fiscal_year):
		target = sum(flt(v) for (name, m), v in tgt.items() if m == slot.month and keep(name))
		actual = sum(flt(v) for (name, m), v in act.items() if m == slot.month and keep(name))
		data.append({
			"month": slot.month, "target_amount": target, "actual_amount": actual,
			"variance": actual - target,
			"achieved_pct": (actual / target * 100) if target else 0.0,
			"currency": currency,
		})

	columns = [
		{"label": _("Month"), "fieldname": "month", "fieldtype": "Data", "width": 120},
		{"label": _("Target"), "fieldname": "target_amount", "fieldtype": "Currency",
		 "options": "currency", "width": 140},
		{"label": _("Actual"), "fieldname": "actual_amount", "fieldtype": "Currency",
		 "options": "currency", "width": 140},
		{"label": _("Variance"), "fieldname": "variance", "fieldtype": "Currency",
		 "options": "currency", "width": 140},
		{"label": _("Achieved %"), "fieldname": "achieved_pct", "fieldtype": "Percent", "width": 110},
		{"label": _("Currency"), "fieldname": "currency", "fieldtype": "Link",
		 "options": "Currency", "hidden": 1, "width": 80},
	]
	chart = {
		"data": {
			"labels": [d["month"][:3] for d in data],
			"datasets": [
				{"name": _("Actual"), "chartType": "bar", "values": [d["actual_amount"] for d in data]},
				{"name": _("Target"), "chartType": "line", "values": [d["target_amount"] for d in data]},
			],
		},
		"type": "axis-mixed",
		"colors": ["#2490ef", "#ff5858"],
	}
	return columns, data, None, chart
