// apps/sf_trading/sf_trading/report/cash_flow_money_in_vs_money_out/cash_flow_money_in_vs_money_out.js
//
// Filters and formatting are shared with Cash Flow Detail (public/js/cash_flow_filters.js), so a
// period clicked here opens the detail with exactly the same filters.

frappe.query_reports["Cash Flow Money In vs Money Out"] = {
	filters: sf_trading.cash_flow.filters({
		months_back: -6,
		view: {
			fieldname: "periodicity",
			label: __("Periodicity"),
			fieldtype: "Select",
			options: ["Daily", "Weekly", "Monthly", "Quarterly", "Yearly"].join("\n"),
			default: "Monthly",
		},
	}),
	formatter: sf_trading.cash_flow.formatter,
};
