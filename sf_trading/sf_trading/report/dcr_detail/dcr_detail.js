// Copyright (c) 2025, sf_trading and contributors
// For license information, please see license.txt

frappe.query_reports["DCR Detail"] = {
	filters: [
		{
			fieldname: "report_type",
			label: __("Type"),
			fieldtype: "Select",
			options: [
				"Cash Sales",
				"Cheque Sales",
				"Credit Sales",
				"Home Credit (Delivery)",
				"Sales Return - Cash",
				"VAT Collected on Cash Sales",
				"VAT Collected on Cheque Sales",
				"VAT Applied on Credit Sales",
				"VAT Applied on Home Credit",
				"VAT Refund on Sales Return",
				"Credit Purchase - DIRECT PURCHASE",
				"Cash Received : Credit Sales",
				"Payments-Petty Cash (Total Payments)",
				"Cash Receipts (Cash Sales)",
				"Bank Sales Receipts",
				"Bank Sales Payments",
				"Internal Transfer (Cash Out)",
				"Internal Transfer (Cash In)",
			],
			default: "Cash Sales",
			reqd: 1,
		},
		{
			fieldname: "from_date",
			label: __("From Date"),
			fieldtype: "Date",
			default: frappe.datetime.get_today(),
			reqd: 1,
		},
		{
			fieldname: "to_date",
			label: __("To Date"),
			fieldtype: "Date",
			default: frappe.datetime.get_today(),
			reqd: 1,
		},
		{
			fieldname: "company",
			label: __("Company"),
			fieldtype: "Link",
			options: "Company",
			default: frappe.defaults.get_user_default("Company"),
			reqd: 1,
		},
		{
			fieldname: "cost_center",
			label: __("Cost Center"),
			fieldtype: "Link",
			options: "Cost Center",
		},
	],
};
