// apps/sf_trading/sf_trading/report/cash_flow_detail/cash_flow_detail.js
//
// Opened by clicking a period on Cash Flow Money In vs Money Out (its dates and filters ride along
// in the URL), or by clicking any grouped row here, which opens the Transactions behind it.

frappe.query_reports["Cash Flow Detail"] = {
	filters: sf_trading.cash_flow.filters({
		months_back: -1,
		view: {
			fieldname: "group_by",
			label: __("Group By"),
			fieldtype: "Select",
			options: [
				"Transactions",
				"Category",
				"Category and Party",
				"Party",
				"Voucher Type",
				"Account",
				"Mode of Payment",
				"Branch",
				"Day",
				"Month",
				"Receivables and Payables",
			].join("\n"),
			default: "Transactions",
		},
	}),
	formatter: sf_trading.cash_flow.formatter,
	tree: true,
	name_field: "label",
	parent_field: "parent",
	initial_depth: 1,
};
