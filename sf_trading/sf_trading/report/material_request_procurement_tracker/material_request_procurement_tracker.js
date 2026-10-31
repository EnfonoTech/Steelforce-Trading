// Copyright (c) 2026, Enfono Technologies and contributors
// For license information, please see license.txt

frappe.query_reports["Material Request Procurement Tracker"] = {
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
			default: frappe.datetime.add_months(frappe.datetime.get_today(), -3),
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
			// a Select with a word for every choice: query_report.js drops a filter whose value is empty
			fieldname: "view",
			label: __("View"),
			fieldtype: "Select",
			options: ["Material Request-wise", "RFQ-wise", "Item-wise"].join("\n"),
			default: "Material Request-wise",
			reqd: 1,
		},
		{
			fieldname: "stage",
			label: __("Stage"),
			fieldtype: "Select",
			options: [
				"All",
				"No RFQ",
				"RFQ Not Submitted",
				"Awaiting Quotations",
				"Quotations Partly Received",
				"All Quotations Received",
				"Partly Ordered",
				"Ordered",
			].join("\n"),
			default: "All",
		},
		{
			fieldname: "status",
			label: __("Material Request Status"),
			fieldtype: "Select",
			options: ["All", "Pending", "Partially Ordered", "Ordered", "Stopped", "Draft"].join("\n"),
			default: "All",
		},
		{
			fieldname: "material_request",
			label: __("Material Request"),
			fieldtype: "Link",
			options: "Material Request",
			get_query: () => {
				return {
					filters: {
						docstatus: ["<", 2],
						material_request_type: "Purchase",
						company: frappe.query_report.get_filter_value("company"),
					},
				};
			},
		},
		{
			fieldname: "request_for_quotation",
			label: __("Request for Quotation"),
			fieldtype: "Link",
			options: "Request for Quotation",
			get_query: () => {
				return {
					filters: { docstatus: ["<", 2], company: frappe.query_report.get_filter_value("company") },
				};
			},
		},
		{
			fieldname: "supplier",
			label: __("Supplier Invited"),
			fieldtype: "Link",
			options: "Supplier",
		},
		{
			fieldname: "raised_by",
			label: __("Raised By"),
			fieldtype: "Link",
			options: "User",
		},
		{
			fieldname: "item_code",
			label: __("Item"),
			fieldtype: "Link",
			options: "Item",
		},
		{
			fieldname: "item_group",
			label: __("Item Group"),
			fieldtype: "Link",
			options: "Item Group",
		},
		{
			fieldname: "warehouse",
			label: __("Warehouse"),
			fieldtype: "Link",
			options: "Warehouse",
			get_query: () => {
				return { filters: { company: frappe.query_report.get_filter_value("company") } };
			},
		},
	],

	formatter: function (value, row, column, data, default_formatter) {
		value = default_formatter(value, row, column, data);
		if (!data) return value;

		if (column.fieldname === "stage") {
			const colour = {
				"No RFQ": "var(--red-600)",
				"RFQ Not Submitted": "var(--red-500)",
				"Awaiting Quotations": "var(--orange-600)",
				"Quotations Partly Received": "var(--orange-500)",
				"All Quotations Received": "var(--green-600)",
				"Partly Ordered": "var(--blue-500)",
				Ordered: "var(--blue-600)",
			}[data.stage];
			if (colour) return `<span style="color: ${colour}; font-weight: 600">${value}</span>`;
		}

		if (column.fieldname === "pending_names" && data.pending_names) {
			return `<span style="color: var(--orange-600)">${value}</span>`;
		}

		if (column.fieldname === "compare" && data.compare) {
			// the comparison of the quotations on this request, already filtered to it
			const params = {
				company: frappe.query_report.get_filter_value("company"),
				from_date: data.transaction_date || frappe.query_report.get_filter_value("from_date"),
				to_date: frappe.datetime.get_today(),
			};
			if (data.request_for_quotation) {
				params.request_for_quotation = data.request_for_quotation;
			} else {
				params.material_request = data.material_request;
			}
			const href = "/app/query-report/Supplier%20Quotation%20Comparison%20Extended?" + new URLSearchParams(params).toString();
			return `<a href="${href}">${frappe.utils.escape_html(data.compare)}</a>`;
		}
		return value;
	},
};
