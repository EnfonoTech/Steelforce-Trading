// sf_trading/sf_trading/report/landed_cost_reconciliation/landed_cost_reconciliation.js
// Landed costs: the expense entries that booked them against the charges that put them into stock.

frappe.query_reports["Landed Cost Reconciliation"] = {
	filters: [
		{
			fieldname: "company",
			label: __("Company"),
			fieldtype: "Link",
			options: "Company",
			default: frappe.defaults.get_user_default("Company"),
			reqd: 1,
		},
		{
			fieldname: "from_date",
			label: __("From Date"),
			fieldtype: "Date",
			default: frappe.datetime.add_months(frappe.datetime.get_today(), -6),
		},
		{ fieldname: "to_date", label: __("To Date"), fieldtype: "Date", default: frappe.datetime.get_today() },
		{
			fieldname: "view",
			label: __("View"),
			fieldtype: "Select",
			options: ["Expense Entries", "Landed Cost Charges", "Account Summary"].join("\n"),
			default: "Expense Entries",
		},
		{
			fieldname: "status",
			label: __("Status"),
			fieldtype: "Select",
			options: [
				"",
				"Unallocated",
				"Partly Allocated",
				"Pending",
				"Allocated",
				"Over-allocated",
				"Unmatched",
				"Account Mismatch",
				"Expense Cancelled",
				"Matched",
			].join("\n"),
		},
		{
			fieldname: "account",
			label: __("Landed Cost Account"),
			fieldtype: "Link",
			options: "Account",
			get_query: () => ({
				filters: { company: frappe.query_report.get_filter_value("company"), is_group: 0 },
			}),
		},
		{
			fieldname: "expense_doctype",
			label: __("Expense Type"),
			fieldtype: "Select",
			options: ["", "Purchase Invoice", "Journal Entry", "Payment Entry"].join("\n"),
		},
		{ fieldname: "supplier", label: __("Supplier"), fieldtype: "Link", options: "Supplier" },
		{ fieldname: "branch", label: __("Branch"), fieldtype: "Link", options: "Branch" },
		{
			fieldname: "include_excluded",
			label: __("Include entries marked Not a Landed Cost"),
			fieldtype: "Check",
			default: 0,
		},
	],

	formatter(value, row, column, data, default_formatter) {
		value = default_formatter(value, row, column, data);
		if (!data || column.fieldname !== "status") return value;
		const colour = {
			Allocated: "green-600",
			Matched: "green-600",
			"Partly Allocated": "blue-500",
			Pending: "grey-600",
			Unallocated: "orange-500",
			Unmatched: "orange-500",
			"Over-allocated": "red-500",
			"Account Mismatch": "red-500",
			"Expense Cancelled": "red-500",
		}[data.status];
		return colour ? `<span style="color:var(--${colour})"><b>${value}</b></span>` : value;
	},
};
