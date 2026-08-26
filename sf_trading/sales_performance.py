# sf_trading/sales_performance.py
"""The Sales Performance workspace: number cards, charts, a dashboard, and the shortcuts.

Provisioned in code rather than exported as a fixture on purpose. A fixture carries the
company and fiscal year that happened to be current when it was exported, and the Report-type
charts below need both as concrete filter values -- so an exported dashboard silently keeps
plotting last year. This runs on every migrate and refreshes those filters, while leaving any
layout the user has since changed alone.

Idempotent: records are created if missing and their filters refreshed; nothing else is
overwritten, so a chart someone recoloured stays recoloured.
"""

import json

import frappe
from frappe.utils import getdate, nowdate

MODULE = "Sf Trading"
DASHBOARD = "Branch Sales Performance"
WORKSPACE = "Sales Performance"

CARDS = [
	("SF MTD Sales", "card_mtd_actual", "Sales this month", "blue"),
	("SF MTD Target", "card_mtd_target", "Target for this month", "cyan"),
	("SF MTD Achievement", "card_mtd_achievement", "This month against target", "green"),
	("SF YTD Sales", "card_ytd_actual", "Sales this fiscal year", "blue"),
	("SF YTD Target", "card_ytd_target", "Target to date this year", "cyan"),
	("SF YTD Achievement", "card_ytd_achievement", "Year against target", "green"),
	("SF YTD Shortfall", "card_ytd_shortfall", "Ahead of or behind target", "orange"),
]


def _defaults():
	company = (
		frappe.db.get_single_value("Global Defaults", "default_company")
		or frappe.db.get_value("Company", {}, "name")
	)
	today = getdate(nowdate())
	fiscal_year = frappe.db.get_value(
		"Fiscal Year",
		{"year_start_date": ["<=", today], "year_end_date": [">=", today]},
		"name",
	) or frappe.db.get_value("Fiscal Year", {}, "name")
	return company, fiscal_year


def ensure_cards():
	for label, method, description, colour in CARDS:
		if frappe.db.exists("Number Card", label):
			# BHD is three decimals and a card rounds whatever it is handed unless told not to
			frappe.db.set_value("Number Card", label, {
				"show_full_number": 1,
				"method": f"sf_trading.sales_target.{method}",
			}, update_modified=False)
			continue
		frappe.get_doc({
			"doctype": "Number Card", "name": label, "label": label, "type": "Custom",
			"method": f"sf_trading.sales_target.{method}", "module": MODULE,
			"is_public": 1, "show_full_number": 1, "color": colour,
			"filters_json": "{}",
		}).insert(ignore_permissions=True)


def ensure_charts():
	company, fiscal_year = _defaults()
	report_filters = json.dumps({"company": company, "fiscal_year": fiscal_year,
	                             "basis": "Net of VAT"})
	group_by_filters = json.dumps([
		["Sales Invoice", "docstatus", "=", 1],
		["Sales Invoice", "company", "=", company],
	])

	charts = [
		{
			"name": "SF Sales by Branch", "chart_name": "SF Sales by Branch",
			"chart_type": "Group By", "document_type": "Sales Invoice",
			"group_by_type": "Sum", "group_by_based_on": "branch",
			"aggregate_function_based_on": "base_net_total", "number_of_groups": 0,
			"type": "Bar", "filters_json": group_by_filters, "timespan": "Last Year",
		},
		{
			"name": "SF Sales by Sales Person", "chart_name": "SF Sales by Sales Person",
			"chart_type": "Group By", "document_type": "Sales Invoice",
			"group_by_type": "Sum", "group_by_based_on": "custom_sales_person",
			"aggregate_function_based_on": "base_net_total", "number_of_groups": 0,
			"type": "Bar", "filters_json": group_by_filters, "timespan": "Last Year",
		},
		{
			"name": "SF Target vs Actual by Month", "chart_name": "SF Target vs Actual by Month",
			"chart_type": "Report", "report_name": "Sales Target Monthly Trend",
			"x_field": "month", "type": "Bar", "filters_json": report_filters,
			"y_axis": [{"y_field": "target_amount"}, {"y_field": "actual_amount"}],
		},
		{
			"name": "SF Achievement by Branch", "chart_name": "SF Achievement by Branch",
			"chart_type": "Report", "report_name": "Sales Target Scorecard",
			"x_field": "dimension_value", "type": "Bar",
			"filters_json": json.dumps({"company": company, "fiscal_year": fiscal_year,
			                            "dimension_type": "Branch", "basis": "Net of VAT"}),
			"y_axis": [{"y_field": "ytd_pct"}],
		},
	]

	for spec in charts:
		spec = dict(spec)
		y_axis = spec.pop("y_axis", None)
		name = spec.pop("name")
		if frappe.db.exists("Dashboard Chart", name):
			# only the filters are refreshed — the fiscal year moves on, the styling should not
			frappe.db.set_value("Dashboard Chart", name,
			                    {"filters_json": spec["filters_json"]}, update_modified=False)
			continue
		doc = frappe.get_doc({
			"doctype": "Dashboard Chart", "name": name, "module": MODULE, "is_public": 1,
			"currency": frappe.get_cached_value("Company", company, "default_currency")
			if company else None,
			"show_values_over_chart": 1, **spec,
		})
		for row in y_axis or []:
			doc.append("y_axis", row)
		doc.insert(ignore_permissions=True)


def ensure_dashboard():
	if not frappe.db.exists("Dashboard", DASHBOARD):
		doc = frappe.get_doc({
			"doctype": "Dashboard", "dashboard_name": DASHBOARD, "module": MODULE,
			"is_default": 0,
		})
		for label, *_rest in CARDS:
			doc.append("cards", {"card": label})
		for chart in ("SF Target vs Actual by Month", "SF Achievement by Branch",
		              "SF Sales by Branch", "SF Sales by Sales Person"):
			doc.append("charts", {"chart": chart, "width": "Half"})
		doc.insert(ignore_permissions=True)


def ensure_workspace():
	"""Where a user goes to set a target and to see whether it was met."""
	if frappe.db.exists("Workspace", WORKSPACE):
		return
	content = [
		{"type": "header", "data": {"text": "<span class=\"h4\"><b>Sales Performance</b></span>", "col": 12}},
		{"type": "number_card", "data": {"number_card_name": "SF MTD Sales", "col": 3}},
		{"type": "number_card", "data": {"number_card_name": "SF MTD Target", "col": 3}},
		{"type": "number_card", "data": {"number_card_name": "SF MTD Achievement", "col": 3}},
		{"type": "number_card", "data": {"number_card_name": "SF YTD Achievement", "col": 3}},
		{"type": "spacer", "data": {"col": 12}},
		{"type": "header", "data": {"text": "<span class=\"h4\"><b>Targets & Reports</b></span>", "col": 12}},
		{"type": "shortcut", "data": {"shortcut_name": "Sales Target", "col": 3}},
		{"type": "shortcut", "data": {"shortcut_name": "Sales Target Scorecard", "col": 3}},
		{"type": "shortcut", "data": {"shortcut_name": "Branch Sales Target vs Actual", "col": 3}},
		{"type": "shortcut", "data": {"shortcut_name": "Sales Person Target vs Actual", "col": 3}},
		{"type": "spacer", "data": {"col": 12}},
		{"type": "chart", "data": {"chart_name": "SF Target vs Actual by Month", "col": 12}},
	]
	doc = frappe.get_doc({
		"doctype": "Workspace", "name": WORKSPACE, "label": WORKSPACE, "title": WORKSPACE,
		"module": MODULE, "public": 1, "icon": "getting-started",
		"content": json.dumps(content),
	})
	doc.append("shortcuts", {"label": "Sales Target", "type": "DocType", "link_to": "Sales Target"})
	for report in ("Sales Target Scorecard", "Branch Sales Target vs Actual",
	               "Sales Person Target vs Actual", "Sales Target Monthly Trend"):
		# doc_view must stay empty for a Report shortcut: the field only accepts the
		# DocType views ("", List, Report Builder, Dashboard, Tree, New, Calendar, Kanban) and
		# "Report" is refused, which aborted a migrate before this was found
		doc.append("shortcuts", {"label": report, "type": "Report", "link_to": report,
		                         "report_ref_doctype": "Sales Invoice"})
	doc.append("shortcuts", {"label": DASHBOARD, "type": "Dashboard", "link_to": DASHBOARD})
	for card in CARDS:
		doc.append("number_cards", {"number_card_name": card[0], "label": card[0]})
	doc.append("charts", {"chart_name": "SF Target vs Actual by Month", "label": "Target vs Actual"})
	doc.append("links", {"label": "Sales Performance", "type": "Card Break", "link_count": 2,
	                     "onboard": 0, "hidden": 0})
	doc.append("links", {"label": "Sales Target", "type": "Link", "link_type": "DocType",
	                     "link_to": "Sales Target", "onboard": 0, "hidden": 0})
	doc.append("links", {"label": "Sales Target Scorecard", "type": "Link", "link_type": "Report",
	                     "link_to": "Sales Target Scorecard", "is_query_report": 1,
	                     "dependencies": "Sales Invoice", "onboard": 0, "hidden": 0})
	doc.insert(ignore_permissions=True)


def setup():
	"""after_migrate entry point.

	Each step is guarded: this is cosmetic furniture, and a dashboard that will not build is
	never a reason to abort a migrate half way through somebody's deploy. Failures are logged
	and the rest still runs.
	"""
	for step in (ensure_cards, ensure_charts, ensure_dashboard, ensure_workspace):
		try:
			step()
		except Exception:
			frappe.log_error(
				frappe.get_traceback(), f"sf_trading sales performance: {step.__name__} failed"
			)
