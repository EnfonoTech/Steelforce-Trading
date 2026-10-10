// sf_trading/public/js/cash_flow_filters.js
// The filters and formatting Cash Flow Detail and Cash Flow Money In vs Money Out share, so a period
// clicked on the summary opens the detail with exactly the same filters. Loaded on every desk page
// (app_include_js) because a query report's own script cannot import another's.

frappe.provide("sf_trading.cash_flow");

sf_trading.cash_flow.filters = function (first) {
	return [
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
			default: frappe.datetime.add_months(frappe.datetime.get_today(), first.months_back),
		},
		{
			fieldname: "to_date",
			label: __("To Date"),
			fieldtype: "Date",
			reqd: 1,
			default: frappe.datetime.get_today(),
		},
		first.view,
		{
			fieldname: "account",
			label: __("Cash / Bank Account"),
			fieldtype: "Link",
			options: "Account",
			get_query: () => ({
				filters: {
					company: frappe.query_report.get_filter_value("company"),
					is_group: 0,
					account_type: ["in", ["Cash", "Bank"]],
				},
			}),
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
			fieldname: "direction",
			label: __("Direction"),
			fieldtype: "Select",
			options: ["", "Money In", "Money Out"].join("\n"),
		},
		{
			fieldname: "category",
			label: __("Category"),
			fieldtype: "Select",
			options: [
				"",
				"Customers",
				"Suppliers",
				"Employees",
				"Expenses",
				"Income",
				"Taxes",
				"Assets",
				"Liabilities",
				"Equity",
				"Cheques Banked",
				"Internal Transfers",
				"Other",
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
			fieldname: "voucher_type",
			label: __("Voucher Type"),
			fieldtype: "Select",
			options: ["", "Payment Entry", "Journal Entry", "Sales Invoice", "Purchase Invoice", "Expense Claim"].join(
				"\n"
			),
		},
		{
			fieldname: "mode_of_payment",
			label: __("Mode of Payment"),
			fieldtype: "Link",
			options: "Mode of Payment",
		},
		{
			fieldname: "exclude_internal_transfers",
			label: __("Exclude internal transfers"),
			fieldtype: "Check",
			default: 0,
		},
		{
			fieldname: "exclude_pdc_accounts",
			label: __("Leave out cheques not yet banked (PDC accounts)"),
			fieldtype: "Check",
			default: 0,
		},
	];
};

sf_trading.cash_flow.formatter = function (value, row, column, data, default_formatter) {
	value = default_formatter(value, row, column, data);
	if (!data) return value;
	if (data.is_total || data.is_opening || data.bold || data.is_header || data.indent === 0) {
		value = `<b>${value}</b>`;
	}
	if (column.fieldname === "money_in" && flt(data.money_in)) {
		value = `<span style="color:#2e7d32">${value}</span>`;
	}
	if (column.fieldname === "money_out" && flt(data.money_out)) {
		value = `<span style="color:#c62828">${value}</span>`;
	}
	if (["net", "amount"].includes(column.fieldname) && flt(data[column.fieldname]) < 0) {
		value = `<span style="color:#c62828">${value}</span>`;
	}
	if (column.fieldname === "running" && flt(data.running) < 0) {
		value = `<span style="color:#c62828;font-weight:600">${value}</span>`;
	}
	return value;
};
