// Copyright (c) 2026, Enfono Technologies and contributors
// For license information, please see license.txt

frappe.query_reports["PO Stock Count Variance"] = {
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
			reqd: 1,
		},
		{ fieldname: "to_date", label: __("To Date"), fieldtype: "Date", default: frappe.datetime.get_today(), reqd: 1 },
		{ fieldname: "supplier", label: __("Supplier"), fieldtype: "Link", options: "Supplier" },
		{
			fieldname: "purchase_order",
			label: __("Purchase Order"),
			fieldtype: "Link",
			options: "Purchase Order",
			get_query() {
				return { filters: { company: frappe.query_report.get_filter_value("company"), docstatus: ["<", 2] } };
			},
		},
		{ fieldname: "item_code", label: __("Item"), fieldtype: "Link", options: "Item" },
		{ fieldname: "item_group", label: __("Item Group"), fieldtype: "Link", options: "Item Group" },
		{ fieldname: "warehouse", label: __("Warehouse"), fieldtype: "Link", options: "Warehouse" },
		{
			fieldname: "rows",
			label: __("Rows"),
			fieldtype: "Select",
			options: ["Counted", "Not Counted", "All"].join("\n"),
			default: "Counted",
			description: __("Not Counted: stock rows ordered without a physical count."),
		},
		{ fieldname: "only_variance", label: __("Only Rows With a Variance"), fieldtype: "Check" },
		{ fieldname: "include_drafts", label: __("Include Draft Orders"), fieldtype: "Check" },
		{
			// a Select, not a Check: an unticked Check never reaches the server, so the view could not be switched back
			fieldname: "view",
			label: __("View"),
			fieldtype: "Select",
			options: ["Item Rows", "Document Rows"].join("\n"),
			default: "Item Rows",
			reqd: 1,
		},
	],
	formatter(value, row, column, data, default_formatter) {
		value = default_formatter(value, row, column, data);
		// less on the shelf than the system says in red, more in orange
		if (data && ["variance", "variance_percent"].includes(column.fieldname) && flt(data.variance)) {
			const colour = flt(data.variance) < 0 ? "var(--red-600)" : "var(--orange-600)";
			value = `<span style="color: ${colour}; font-weight: 600">${value}</span>`;
		}
		// Document Rows: shortage red, excess orange
		if (data && column.fieldname === "shortage" && flt(data.shortage)) {
			value = `<span style="color: var(--red-600); font-weight: 600">${value}</span>`;
		}
		if (data && column.fieldname === "excess" && flt(data.excess)) {
			value = `<span style="color: var(--orange-600); font-weight: 600">${value}</span>`;
		}
		return value;
	},
};
