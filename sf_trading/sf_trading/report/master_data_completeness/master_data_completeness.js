// sf_trading/sf_trading/report/master_data_completeness/master_data_completeness.js
// Which B2B customers and suppliers still lack documents, payment terms or contact details.

frappe.query_reports["Master Data Completeness"] = {
	filters: [
		{
			fieldname: "party_type",
			label: __("Party Type"),
			fieldtype: "Select",
			options: ["Customer and Supplier", "Customer", "Supplier"].join("\n"),
			default: "Customer and Supplier",
		},
		{
			fieldname: "scope",
			label: __("Suppliers"),
			fieldtype: "Select",
			options: ["B2B Customers and Suppliers with Tax ID", "B2B Customers and All Suppliers"].join("\n"),
			default: "B2B Customers and Suppliers with Tax ID",
		},
		{
			fieldname: "missing",
			label: __("Missing"),
			fieldtype: "Select",
			options: [
				"",
				"Supporting Document",
				"Document Attachment",
				"Payment Terms",
				"Mobile",
				"Email",
				"Address",
				"Tax ID",
				"CR Number",
				"Expired Document",
			].join("\n"),
		},
		{ fieldname: "created_from", label: __("Created From"), fieldtype: "Date" },
		{ fieldname: "created_to", label: __("Created To"), fieldtype: "Date" },
		{ fieldname: "new_masters_only", label: __("New Masters Only"), fieldtype: "Check", default: 0 },
		{ fieldname: "show_complete", label: __("Include Complete Masters"), fieldtype: "Check", default: 0 },
		{ fieldname: "include_disabled", label: __("Include Disabled"), fieldtype: "Check", default: 0 },
		{
			fieldname: "view",
			label: __("View"),
			fieldtype: "Select",
			options: ["Detail", "Summary"].join("\n"),
			default: "Detail",
		},
	],

	formatter(value, row, column, data, default_formatter) {
		value = default_formatter(value, row, column, data);
		if (!data) return value;
		if (column.fieldname === "missing" && data.missing) {
			value = `<span style="color:var(--${data.new_master ? "red" : "orange"}-500)">${value}</span>`;
		}
		if (column.fieldname === "expired" && data.expired) {
			value = `<span style="color:var(--red-500)"><b>${value}</b></span>`;
		}
		return value;
	},
};
