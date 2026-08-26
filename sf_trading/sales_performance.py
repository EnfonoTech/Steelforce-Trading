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

from sf_trading.sales_performance_block import BLOCK, ensure_block
from frappe.utils import getdate, nowdate

MODULE = "Sf Trading"
DASHBOARD = "Branch Sales Performance"
WORKSPACE = "Sales Performance"

# record name (what the workspace references), the label a human reads, the method, the colour.
# "SF MTD SALES" told nobody anything; the label says which period and which quantity it is.
CARDS = [
	("SF MTD Sales", "Sales · This Month", "card_mtd_actual", "#2490ef"),
	("SF MTD Target", "Target · This Month", "card_mtd_target", "#7c3aed"),
	("SF MTD Achievement", "Achieved · This Month", "card_mtd_achievement", "#29cd42"),
	("SF Needed Per Day", "Needed per Day · Rest of Month", "card_needed_per_day", "#f5a524"),
	("SF YTD Sales", "Sales · Year to Date", "card_ytd_actual", "#2490ef"),
	("SF YTD Target", "Target · Year to Date", "card_ytd_target", "#7c3aed"),
	("SF YTD Achievement", "Achieved · Year to Date", "card_ytd_achievement", "#29cd42"),
	("SF YTD Shortfall", "Ahead / Behind · Year to Date", "card_ytd_shortfall", "#ff5858"),
	("SF Top Seller", "Top Sales Person", "card_top_seller", "#00bcd4"),
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


def ensure_report_flags():
	"""Keep the trend report free of a total row.

	The flag is in the report's own JSON, but the standard-report sync copied the file's
	`modified` onto the record and left `add_total_row` at 1 anyway -- so the trend kept
	returning a thirteenth row and the chart drew it as a month taller than the year. Set it
	here, where it is deterministic on every site.
	"""
	if frappe.db.exists("Report", "Sales Target Monthly Trend"):
		frappe.db.set_value("Report", "Sales Target Monthly Trend", "add_total_row", 0,
		                    update_modified=False)


def ensure_cards():
	for name, label, method, colour in CARDS:
		if frappe.db.exists("Number Card", name):
			# BHD is three decimals and a card rounds whatever it is handed unless told not to.
			# Colour and description are refreshed too, so a restyle ships without hand-editing
			# nine cards on every site.
			frappe.db.set_value("Number Card", name, {
				"show_full_number": 1,
				"method": f"sf_trading.sales_target.{method}",
				"color": colour,
				"label": label,
			}, update_modified=False)
			continue
		frappe.get_doc({
			"doctype": "Number Card", "name": name, "label": label, "type": "Custom",
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
	# without this the chart is topped by a "null" bar worth 750k: every invoice raised before
	# the sales person field became mandatory on 15 July 2026
	person_filters = json.dumps([
		["Sales Invoice", "docstatus", "=", 1],
		["Sales Invoice", "company", "=", company],
		["Sales Invoice", "custom_sales_person", "is", "set"],
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
			"type": "Bar", "filters_json": person_filters, "timespan": "Last Year",
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
		for name, *_rest in CARDS:
			doc.append("cards", {"card": name})
		for chart in ("SF Target vs Actual by Month", "SF Achievement by Branch",
		              "SF Sales by Branch", "SF Sales by Sales Person"):
			doc.append("charts", {"chart": chart, "width": "Half"})
		doc.insert(ignore_permissions=True)


def workspace_content():
	"""The workspace layout: numbers first, then the overview block, then where to go next."""
	return [
		{"type": "header", "data": {"text": "<span class=\"h4\"><b>This month</b></span>", "col": 12}},
		{"type": "number_card", "data": {"number_card_name": "SF MTD Sales", "col": 3}},
		{"type": "number_card", "data": {"number_card_name": "SF MTD Target", "col": 3}},
		{"type": "number_card", "data": {"number_card_name": "SF MTD Achievement", "col": 3}},
		{"type": "number_card", "data": {"number_card_name": "SF Needed Per Day", "col": 3}},
		{"type": "header", "data": {"text": "<span class=\"h4\"><b>Year to date</b></span>", "col": 12}},
		{"type": "number_card", "data": {"number_card_name": "SF YTD Sales", "col": 3}},
		{"type": "number_card", "data": {"number_card_name": "SF YTD Target", "col": 3}},
		{"type": "number_card", "data": {"number_card_name": "SF YTD Achievement", "col": 3}},
		{"type": "number_card", "data": {"number_card_name": "SF YTD Shortfall", "col": 3}},
		{"type": "header", "data": {"text": "<span class=\"h4\"><b>People</b></span>", "col": 12}},
		{"type": "number_card", "data": {"number_card_name": "SF Top Seller", "col": 3}},
		{"type": "spacer", "data": {"col": 12}},
		{"type": "custom_block", "data": {"custom_block_name": BLOCK, "col": 12}},
		{"type": "spacer", "data": {"col": 12}},
		{"type": "header", "data": {"text": "<span class=\"h4\"><b>Where the money came from</b></span>", "col": 12}},
		{"type": "chart", "data": {"chart_name": "SF Target vs Actual by Month", "col": 6}},
		{"type": "chart", "data": {"chart_name": "SF Achievement by Branch", "col": 6}},
		{"type": "chart", "data": {"chart_name": "SF Sales by Branch", "col": 6}},
		{"type": "chart", "data": {"chart_name": "SF Sales by Sales Person", "col": 6}},
		{"type": "spacer", "data": {"col": 12}},
		{"type": "header", "data": {"text": "<span class=\"h4\"><b>Targets &amp; Reports</b></span>", "col": 12}},
		{"type": "shortcut", "data": {"shortcut_name": "Performance Board", "col": 3}},
		{"type": "shortcut", "data": {"shortcut_name": "Sales Target", "col": 3}},
		{"type": "shortcut", "data": {"shortcut_name": "Sales Target Scorecard", "col": 3}},
		{"type": "shortcut", "data": {"shortcut_name": "Branch Sales Target vs Actual", "col": 3}},
		{"type": "shortcut", "data": {"shortcut_name": "Sales Person Target vs Actual", "col": 3}},
		{"type": "shortcut", "data": {"shortcut_name": "Sales Target Monthly Trend", "col": 3}},
		{"type": "quick_list", "data": {"quick_list_name": "Sales Target", "col": 6}},
	]


def _fill_workspace(doc):
	"""One canonical arrangement, used for a new workspace and for upgrading an old one."""
	doc.set("shortcuts", [])
	doc.set("number_cards", [])
	doc.set("charts", [])
	doc.set("custom_blocks", [])
	doc.set("quick_lists", [])
	doc.content = json.dumps(workspace_content())

	doc.append("shortcuts", {"label": "Performance Board", "type": "Page",
	                         "link_to": "sales-performance-board"})
	doc.append("shortcuts", {"label": "Sales Target", "type": "DocType", "link_to": "Sales Target",
	                         "stats_filter": json.dumps({"fiscal_year": _defaults()[1]})})
	for report in ("Sales Target Scorecard", "Branch Sales Target vs Actual",
	               "Sales Person Target vs Actual", "Sales Target Monthly Trend"):
		# doc_view must stay empty for a Report shortcut: the field only accepts the DocType
		# views, and "Report" is refused -- which aborted a migrate before this was found
		doc.append("shortcuts", {"label": report, "type": "Report", "link_to": report,
		                         "report_ref_doctype": "Sales Invoice"})
	doc.append("shortcuts", {"label": DASHBOARD, "type": "Dashboard", "link_to": DASHBOARD})

	for card in CARDS:
		# the workspace block matches on label, so this label must stay the RECORD name; the
		# friendly wording lives on the card itself and is what gets displayed
		doc.append("number_cards", {"number_card_name": card[0], "label": card[0]})
	for chart in ("SF Target vs Actual by Month", "SF Achievement by Branch",
	              "SF Sales by Branch", "SF Sales by Sales Person"):
		doc.append("charts", {"chart_name": chart, "label": chart})
	doc.append("custom_blocks", {"custom_block_name": BLOCK, "label": BLOCK})
	doc.append("quick_lists", {"document_type": "Sales Target", "label": "Sales Target",
	                           "quick_list_filter": json.dumps({})})


def ensure_workspace():
	"""Build the workspace, or upgrade one that predates the overview block.

	An existing workspace is only rewritten while it still lacks the block -- after that a
	layout somebody rearranged is theirs, and a migrate leaves it alone.
	"""
	if frappe.db.exists("Workspace", WORKSPACE):
		doc = frappe.get_doc("Workspace", WORKSPACE)
		if BLOCK in (doc.content or ""):
			return
		_fill_workspace(doc)
		doc.save(ignore_permissions=True)
		return

	doc = frappe.get_doc({
		"doctype": "Workspace", "name": WORKSPACE, "label": WORKSPACE, "title": WORKSPACE,
		"module": MODULE, "public": 1, "icon": "getting-started",
	})
	_fill_workspace(doc)
	doc.insert(ignore_permissions=True)


def setup():
	"""after_migrate entry point.

	Each step is guarded: this is cosmetic furniture, and a dashboard that will not build is
	never a reason to abort a migrate half way through somebody's deploy. Failures are logged
	and the rest still runs.
	"""
	for step in (ensure_report_flags, ensure_block, ensure_cards, ensure_charts,
	             ensure_dashboard, ensure_workspace):
		try:
			step()
		except Exception:
			frappe.log_error(
				frappe.get_traceback(), f"sf_trading sales performance: {step.__name__} failed"
			)
