// Copyright (c) 2026, Enfono Technologies and contributors
// For license information, please see license.txt

frappe.query_reports["Branch Bonus Eligibility"] = {
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
			reqd: 1,
		},
		{ fieldname: "to_date", label: __("To Date"), fieldtype: "Date", default: frappe.datetime.month_end(), reqd: 1 },
		{ fieldname: "branch", label: __("Branch"), fieldtype: "Link", options: "Branch" },
		{
			fieldname: "outdoor_sales_persons",
			label: __("Outdoor Sales Persons"),
			fieldtype: "MultiSelectList",
			description: __("Their sales are outdoor sales: taken out of eligible sales and revenue allowed."),
			get_data(txt) {
				return frappe.db.get_link_options("Sales Person", txt, { is_group: 0 });
			},
		},
		{
			fieldname: "collection_days",
			label: __("Collected Within (Days)"),
			fieldtype: "Int",
			default: 60,
			description: __("A credit sale counts only for the money collected within this many days of the invoice."),
		},
		{
			// a Select, not a Check: an unticked Check never reaches the server
			fieldname: "view",
			label: __("View"),
			fieldtype: "Select",
			options: ["Branch Summary", "Calculation Sheet"].join("\n"),
			default: "Branch Summary",
			reqd: 1,
			description: __("Calculation Sheet: one branch, line by line. Pick a Branch."),
		},
	],
	formatter(value, row, column, data, default_formatter) {
		value = default_formatter(value, row, column, data);
		if (data && data.bold) value = `<b>${value}</b>`;
		return value;
	},
};
