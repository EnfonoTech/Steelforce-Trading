// apps/sf_trading/sf_trading/report/cash_flow_statement_detail/cash_flow_statement_detail.js
//
// The vouchers behind a figure of the Cash Flow Statement (sf_trading/cash_flow_statement_detail.py). Opened
// by clicking a figure on the statement, which fills in the line, the dates, the branch / cost centre and,
// for an account row, the account; or run on its own by choosing a line. A voucher's number opens it.

frappe.query_reports["Cash Flow Statement Detail"] = {
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
			fieldname: "line",
			label: __("Statement Line"),
			fieldtype: "Data",
			description: __("A line, group or total of the statement, as it is named there. Blank: the whole statement."),
		},
		{
			fieldname: "group_by",
			label: __("Group By"),
			fieldtype: "Select",
			options: ["Voucher Type", "Account", "Party", "Month", "Vouchers", "Transactions"].join("\n"),
			default: "Voucher Type",
		},
		{
			fieldname: "account",
			label: __("Account"),
			fieldtype: "MultiSelectList",
			get_data: (txt) =>
				frappe.db.get_link_options("Account", txt, {
					company: frappe.query_report.get_filter_value("company"),
					is_group: 0,
				}),
		},
		{
			fieldname: "voucher_type",
			label: __("Voucher Type"),
			fieldtype: "Select",
			options: [
				"",
				"Sales Invoice",
				"Purchase Invoice",
				"Payment Entry",
				"Journal Entry",
				"Stock Entry",
				"Purchase Receipt",
				"Delivery Note",
				"Stock Reconciliation",
				"Expense Claim",
				"Landed Cost Voucher",
			].join("\n"),
		},
		{
			fieldname: "party_type",
			label: __("Party Type"),
			fieldtype: "Select",
			options: ["", "Customer", "Supplier", "Employee", "Shareholder"].join("\n"),
			on_change: () => frappe.query_report.set_filter_value("party", ""),
		},
		{
			fieldname: "party",
			label: __("Party"),
			fieldtype: "Dynamic Link",
			options: "party_type",
			depends_on: "eval:doc.party_type",
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
	],
	formatter: (value, row, column, data, default_formatter) =>
		sf_trading.cash_flow_statement.detail_formatter(value, row, column, data, default_formatter),
	onload: (report) => sf_trading.cash_flow_statement.detail_onload(report),
	tree: true,
	name_field: "label",
	initial_depth: 0,
};
