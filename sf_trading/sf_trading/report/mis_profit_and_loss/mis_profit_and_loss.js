// apps/sf_trading/sf_trading/report/mis_profit_and_loss/mis_profit_and_loss.js
//
// The management profit and loss of a company with the vouchers other companies charged to its cost centres
// (sf_trading/mis_profit_loss.py). Every amount drills into the vouchers behind it (MIS Profit and Loss Detail).

frappe.query_reports["MIS Profit and Loss"] = {
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
			fieldname: "periodicity",
			label: __("Periodicity"),
			fieldtype: "Select",
			options: ["Total", "Monthly", "Quarterly", "Yearly"].join("\n"),
			default: "Total",
		},
		{
			fieldname: "comparison",
			label: __("Compare With"),
			fieldtype: "Select",
			options: ["None", "Previous Period", "Same Period Last Year"].join("\n"),
			default: "None",
			description: __("Needs Periodicity Total and Columns By Period."),
		},
		{
			fieldname: "columns_by",
			label: __("Columns By"),
			fieldtype: "Select",
			options: ["Period", "Cost Center"].join("\n"),
			default: "Period",
		},
		{
			fieldname: "detail",
			label: __("Detail"),
			fieldtype: "Select",
			options: ["Sections Only", "Sections and Accounts"].join("\n"),
			default: "Sections and Accounts",
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
			fieldname: "show_percent",
			label: __("Show % of Revenue"),
			fieldtype: "Check",
			default: 0,
		},
		{
			fieldname: "show_zero_rows",
			label: __("Show Rows With No Movement"),
			fieldtype: "Check",
			default: 0,
		},
	],
	formatter: (value, row, column, data, default_formatter) =>
		sf_trading.mis_pl.formatter(value, row, column, data, default_formatter),
	onload: (report) => sf_trading.mis_pl.onload(report),
	tree: true,
	name_field: "label",
	initial_depth: 1,
};
