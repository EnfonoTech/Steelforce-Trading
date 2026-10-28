// sf_trading/sf_trading/report/sales_target_monthly_trend/sales_target_monthly_trend.js
// The Sales Person picker follows the Sales Person Type filter, so an Outdoor report only offers
// outdoor sellers; a seller already picked is cleared when the type changes.
function sf_person_query() {
	const type = frappe.query_report && frappe.query_report.get_filter_value("sales_person_type");
	const filters = { is_group: 0 };
	if (type) filters.custom_sales_person_type = type;
	return { filters };
}

function sf_person_type_changed() {
	const report = frappe.query_report;
	if (report.get_filter_value("sales_person")) report.set_filter_value("sales_person", "");
	else report.refresh();
}

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
		{
			fieldname: "from_date", label: __("From Date"), fieldtype: "Date",
			description: __("Optional. Narrows the fiscal year; a part month's target is prorated."),
		},
		{ fieldname: "to_date", label: __("To Date"), fieldtype: "Date" },
		{ fieldname: "branch", label: __("Branch"), fieldtype: "Link", options: "Branch" },
		{
			fieldname: "sales_person_type", label: __("Sales Person Type"), fieldtype: "Link",
			options: "Sales Person Type",
			description: __("A branch never counts Outdoor sales. Pick Outdoor to see them apart."),
			on_change: sf_person_type_changed,
		},
		{
			fieldname: "sales_person", label: __("Sales Person"), fieldtype: "Link",
			options: "Sales Person", get_query: sf_person_query,
		},
	],
};
