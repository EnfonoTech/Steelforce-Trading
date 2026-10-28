// sf_trading/sf_trading/report/party_rule_set_diff/party_rule_set_diff.js
// Where the Party Master Rule Set and the code's own approval gate disagree about what a party lacks.

frappe.query_reports["Party Rule Set Diff"] = {
	filters: [
		{
			fieldname: "party_type",
			label: __("Party Type"),
			fieldtype: "Select",
			options: ["Customer and Supplier", "Customer", "Supplier"].join("\n"),
			default: "Customer and Supplier",
		},
		{
			fieldname: "customers",
			label: __("Customers"),
			fieldtype: "Select",
			options: ["Credit Customers", "All Customers"].join("\n"),
			default: "Credit Customers",
		},
		{ fieldname: "show_same", label: __("Include Matching Parties"), fieldtype: "Check", default: 0 },
		{ fieldname: "limit", label: __("Most Parties Read"), fieldtype: "Int", default: 1000 },
	],

	formatter(value, row, column, data, default_formatter) {
		value = default_formatter(value, row, column, data);
		if (data && ["only_legacy", "only_rule_set"].includes(column.fieldname) && data[column.fieldname]) {
			value = `<span style="color:var(--red-500)">${value}</span>`;
		}
		return value;
	},
};
