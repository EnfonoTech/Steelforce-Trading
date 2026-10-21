// Copyright (c) 2026, Enfono Technologies and contributors
// For license information, please see license.txt

frappe.query_reports["Ticket Closing Efficiency"] = {
	filters: [
		{
			fieldname: "view",
			label: __("Rank"),
			fieldtype: "Select",
			options: ["Resolvers", "Closers"].join("\n"),
			default: "Resolvers",
			description: __("Resolvers: who works the ticket and marks it Resolved. Closers: who confirmed it Closed, or the system after the wait."),
		},
		{
			fieldname: "from_date",
			label: __("Finished From"),
			fieldtype: "Date",
			default: frappe.datetime.add_months(frappe.datetime.get_today(), -3),
			description: __("A ticket counts on the day it was resolved (or closed, if it never was)."),
		},
		{ fieldname: "to_date", label: __("Finished To"), fieldtype: "Date", default: frappe.datetime.get_today() },
		{
			fieldname: "ticket_type",
			label: __("Type"),
			fieldtype: "Select",
			options: ["", "Error / Bug", "How To / Question", "Data Correction", "Access / Permission", "Feature Request", "Other"].join("\n"),
		},
		{
			fieldname: "module_area",
			label: __("Area"),
			fieldtype: "Select",
			options: ["", "Selling", "Buying", "Stock", "Accounts", "HR & Payroll", "Reports & Printing", "Settings & Permissions", "Other"].join("\n"),
		},
		{ fieldname: "priority", label: __("Priority"), fieldtype: "Select", options: ["", "Low", "Medium", "High", "Urgent"].join("\n") },
		{ fieldname: "user", label: __("User"), fieldtype: "Link", options: "User" },
	],
	formatter(value, row, column, data, default_formatter) {
		value = default_formatter(value, row, column, data);
		// the leader stands out; a high reopen rate is a warning
		if (column.fieldname === "rank" && data && data.rank === 1) {
			value = `<b style="color: var(--green-600)">${value}</b>`;
		}
		if (column.fieldname === "reopen_pct" && data && data.reopen_pct >= 25) {
			value = `<span style="color: var(--red-600)">${value}</span>`;
		}
		return value;
	},
};
