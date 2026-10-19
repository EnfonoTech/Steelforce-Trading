// apps/sf_trading/sf_trading/report/cash_flow_statement/cash_flow_statement.js
//
// The Cash Flow Statement, direct or indirect (sf_trading/cash_flow_statement.py). Every figure drills
// into the General Ledger for the accounts behind it and the dates of its column
// (public/js/cash_flow_statement.js); the lines open into their accounts.

frappe.query_reports["Cash Flow Statement"] = {
	filters: [
		{
			fieldname: "company",
			label: __("Company"),
			fieldtype: "Link",
			options: "Company",
			reqd: 1,
			default: frappe.defaults.get_user_default("Company"),
		},
		{
			fieldname: "from_date",
			label: __("From Date"),
			fieldtype: "Date",
			reqd: 1,
			default: frappe.datetime.add_months(frappe.datetime.get_today(), -12),
		},
		{
			fieldname: "to_date",
			label: __("To Date"),
			fieldtype: "Date",
			reqd: 1,
			default: frappe.datetime.get_today(),
		},
		{
			fieldname: "method",
			label: __("Method"),
			fieldtype: "Select",
			options: ["Direct", "Indirect"].join("\n"),
			default: "Direct",
			reqd: 1,
		},
		{
			fieldname: "periodicity",
			label: __("Periodicity"),
			fieldtype: "Select",
			options: ["Total", "Monthly", "Quarterly", "Yearly"].join("\n"),
			default: "Total",
		},
		{
			fieldname: "branch",
			label: __("Branch"),
			fieldtype: "MultiSelectList",
			get_data: (txt) => frappe.db.get_link_options("Branch", txt),
		},
		{
			fieldname: "cost_center",
			label: __("Cost Center"),
			fieldtype: "Link",
			options: "Cost Center",
			get_query: () => ({ filters: { company: frappe.query_report.get_filter_value("company") } }),
		},
		{
			fieldname: "show_accounts",
			label: __("Accounts Under Each Line"),
			fieldtype: "Select",
			options: ["None", "Top 15 and others", "All"].join("\n"),
			default: "Top 15 and others",
		},
		{
			fieldname: "show_zero_lines",
			label: __("Show Lines With No Movement"),
			fieldtype: "Check",
			default: 0,
		},
	],
	formatter: (value, row, column, data, default_formatter) =>
		sf_trading.cash_flow_statement.formatter(value, row, column, data, default_formatter),
	onload: (report) => sf_trading.cash_flow_statement.onload(report),
	tree: true,
	name_field: "label",
	initial_depth: 2,
};
