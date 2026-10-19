// apps/sf_trading/sf_trading/report/mis_profit_and_loss_detail/mis_profit_and_loss_detail.js
//
// The vouchers behind a figure of the MIS Profit and Loss (sf_trading/mis_profit_loss_detail.py). Opened by
// clicking a figure, which fills in the section, the dates, the origin, the account and the cost centre; or run
// on its own. A voucher's number opens the voucher.

frappe.query_reports["MIS Profit and Loss Detail"] = {
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
			fieldname: "line",
			label: __("Section or Subtotal"),
			fieldtype: "Data",
			description: __("As named on the profit and loss, for example Operating expenses or Gross profit. Blank: the whole profit."),
		},
		{
			fieldname: "origin",
			label: __("Origin"),
			fieldtype: "Select",
			options: ["", "Own books", "Charged in", "Charged out"].join("\n"),
		},
		{
			fieldname: "source_company",
			label: __("Source Company"),
			fieldtype: "Link",
			options: "Company",
			description: __("For charged in: the company that posted it. For charged out: the company it was charged to."),
		},
		{
			fieldname: "group_by",
			label: __("Group By"),
			fieldtype: "Select",
			options: ["Voucher Type", "Account", "Company", "Origin", "Party", "Month", "Vouchers", "Transactions"].join("\n"),
			default: "Voucher Type",
		},
		{
			fieldname: "account",
			label: __("Account"),
			fieldtype: "MultiSelectList",
			get_data: (txt) => frappe.db.get_link_options("Account", txt, { is_group: 0 }),
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
		sf_trading.mis_pl.detail_formatter(value, row, column, data, default_formatter),
	onload: (report) => sf_trading.mis_pl.detail_onload(report),
	tree: true,
	name_field: "label",
	initial_depth: 0,
};
