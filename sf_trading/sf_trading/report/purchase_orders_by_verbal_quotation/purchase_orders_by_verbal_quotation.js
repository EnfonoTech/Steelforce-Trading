// Copyright (c) 2026, Enfono Technologies and contributors
// For license information, please see license.txt

frappe.query_reports["Purchase Orders by Verbal Quotation"] = {
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
			default: frappe.datetime.add_months(frappe.datetime.get_today(), -1),
		},
		{ fieldname: "to_date", label: __("To Date"), fieldtype: "Date", default: frappe.datetime.get_today() },
		{
			fieldname: "quotation_type",
			label: __("Quotation Type"),
			fieldtype: "Select",
			options: ["All", "With Verbal Quotation", "Without Verbal Quotation"].join("\n"),
			default: "All",
			description: __("With Verbal Quotation: Purchase Orders ticked Verbal (No Quotation)."),
		},
		{ fieldname: "supplier", label: __("Supplier"), fieldtype: "Link", options: "Supplier" },
		{ fieldname: "branch", label: __("Branch"), fieldtype: "Link", options: "Branch" },
		{
			fieldname: "purchase_type",
			label: __("Purchase Type"),
			fieldtype: "Select",
			options: ["", "Local", "Import"].join("\n"),
			description: __("Import: an order in a currency other than the company's."),
		},
		{
			fieldname: "docstatus",
			label: __("Document Status"),
			fieldtype: "Select",
			options: ["", "Draft", "Submitted", "Cancelled"].join("\n"),
			description: __("Blank: Draft and Submitted."),
		},
		{ fieldname: "user", label: __("Created By"), fieldtype: "Link", options: "User" },
	],
	formatter(value, row, column, data, default_formatter) {
		// a block heading and the total: bold text, never a link to an order that does not exist
		if (data && data.is_group) {
			if (column.fieldname === "purchase_order") {
				return `<b>${frappe.utils.escape_html(value || "")}</b>`;
			}
			value = default_formatter(value, row, column, data);
			return value ? `<b>${value}</b>` : value;
		}
		value = default_formatter(value, row, column, data);
		// a verbal order stands out
		if (column.fieldname === "verbal_comment" && data && data.verbal && value) {
			value = `<span style="color: var(--orange-600)">${value}</span>`;
		}
		return value;
	},
};
