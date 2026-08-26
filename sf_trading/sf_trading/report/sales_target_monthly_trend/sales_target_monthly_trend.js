// sf_trading/sf_trading/report/sales_target_monthly_trend/sales_target_monthly_trend.js
frappe.query_reports["Sales Target Monthly Trend"] = {
	filters: [
		{
			fieldname: "company", label: __("Company"), fieldtype: "Link", options: "Company",
			reqd: 1, default: frappe.defaults.get_user_default("Company"),
		},
		{
			fieldname: "fiscal_year", label: __("Fiscal Year"), fieldtype: "Link",
			options: "Fiscal Year", reqd: 1,
			default: frappe.defaults.get_user_default("fiscal_year"),
		},
		{
			fieldname: "basis", label: __("Measured On"), fieldtype: "Select",
			options: ["Net of VAT", "Gross"].join("\n"), default: "Net of VAT",
		},
		{ fieldname: "branch", label: __("Branch"), fieldtype: "Link", options: "Branch" },
		{
			fieldname: "sales_person", label: __("Sales Person"), fieldtype: "Link",
			options: "Sales Person", get_query: () => ({ filters: { is_group: 0 } }),
		},
	],
};
