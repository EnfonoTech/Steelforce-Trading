// Copyright (c) 2026, Enfono Technologies and contributors
// For license information, please see license.txt

frappe.query_reports["Mandatory Attachment Missing"] = {
	filters: [
		{
			fieldname: "company",
			label: __("Company"),
			fieldtype: "Link",
			options: "Company",
			default: frappe.defaults.get_user_default("Company"),
		},
		{
			fieldname: "from_date",
			label: __("From Date"),
			fieldtype: "Date",
			default: erpnext.utils.get_fiscal_year(frappe.datetime.get_today(), true)[1],
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
			fieldtype: "Link",
			options: "DocType",
			// only the vouchers whose PM Workflow requires an attachment somewhere
			get_query: () => ({
				query: "sf_trading.sf_trading.report.mandatory_attachment_missing.mandatory_attachment_missing.document_type_query",
			}),
		},
		{
			fieldname: "moved_by",
			label: __("Moved By"),
			fieldtype: "Link",
			options: "User",
		},
	],
};
