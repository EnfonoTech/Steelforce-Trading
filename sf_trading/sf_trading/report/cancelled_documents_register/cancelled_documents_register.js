// Copyright (c) 2026, Enfono Technologies and contributors
// For license information, please see license.txt

// Every cancelled document across selling, buying, stock and accounts: who cancelled it and when,
// and -- where it went through a cancellation request -- who initiated it. Dates are cancel dates.
frappe.query_reports["Cancelled Documents Register"] = {
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
			default: frappe.datetime.month_start(),
			description: __("Cancelled on or after."),
		},
		{
			fieldname: "to_date",
			label: __("To Date"),
			fieldtype: "Date",
			default: frappe.datetime.get_today(),
		},
		{
			fieldname: "document_type",
			label: __("Document Type"),
			fieldtype: "Select",
			options: [
				"",
				"Quotation",
				"Sales Order",
				"Delivery Note",
				"Sales Invoice",
				"Material Request",
				"Purchase Order",
				"Purchase Receipt",
				"Purchase Invoice",
				"Payment Request",
				"Payment Advice",
				"Payment Entry",
				"Journal Entry",
				"Stock Entry",
			],
		},
		{ fieldname: "branch", label: __("Branch"), fieldtype: "Link", options: "Branch" },
		{ fieldname: "cancelled_by", label: __("Cancelled By"), fieldtype: "Link", options: "User" },
	],
};
