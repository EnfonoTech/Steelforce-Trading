// Copyright (c) 2026, Enfono Technologies and contributors
// For license information, please see license.txt

// What the company owes each supplier at each branch, aged, with the advances held and the orders
// still to be invoiced. Outstanding comes from the Payment Ledger as on the chosen date.
frappe.query_reports["Branch-wise Payable Outstanding"] = {
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
			fieldname: "as_on_date",
			label: __("As On"),
			fieldtype: "Date",
			default: frappe.datetime.get_today(),
			reqd: 1,
		},
		{
			fieldname: "branch",
			label: __("Branch"),
			fieldtype: "MultiSelectList",
			get_data: (txt) => frappe.db.get_link_options("Branch", txt),
		},
		{ fieldname: "supplier", label: __("Supplier"), fieldtype: "Link", options: "Supplier" },
		{ fieldname: "supplier_group", label: __("Supplier Group"), fieldtype: "Link", options: "Supplier Group" },
		{
			fieldname: "supplier_origin",
			label: __("Local / Import"),
			fieldtype: "Select",
			options: ["All", "Local", "Import"],
			default: "All",
		},
		{
			fieldname: "status",
			label: __("Status"),
			fieldtype: "Select",
			options: ["All", "Overdue", "Not Yet Due", "Advance Held"],
			default: "All",
		},
		{
			fieldname: "ageing_based_on",
			label: __("Ageing Based On"),
			fieldtype: "Select",
			options: ["Due Date", "Posting Date"],
			default: "Due Date",
		},
		{ fieldname: "range", label: __("Ageing Range"), fieldtype: "Data", default: "30, 60, 90, 120" },
		{ fieldname: "min_outstanding", label: __("Minimum Payable"), fieldtype: "Currency" },
		{
			fieldname: "credit_suppliers_only",
			label: __("Suppliers With Payment Terms Only"),
			fieldtype: "Check",
			default: 0,
		},
		{ fieldname: "show_zero", label: __("Include Nothing Outstanding"), fieldtype: "Check", default: 0 },
		{
			fieldname: "view",
			label: __("View"),
			fieldtype: "Select",
			options: ["Supplier and Branch", "Branch Summary", "Document Detail"],
			default: "Supplier and Branch",
		},
	],
	tree: true,
	name_field: "name",
	parent_field: "parent",
	initial_depth: 0,

	formatter(value, row, column, data, default_formatter) {
		value = default_formatter(value, row, column, data);
		if (!data) return value;
		if (data.indent === 0 && column.fieldname === "label") {
			value = `<b>${value}</b>`;
		}
		if (column.fieldname === "status" && data.status) {
			const colour = { Overdue: "red", "Not Yet Due": "blue", "Advance Held": "orange", Settled: "green" }[data.status] || "gray";
			value = `<span class="indicator-pill ${colour}">${frappe.utils.escape_html(__(data.status))}</span>`;
		}
		if (column.fieldname === "overdue" && data.overdue > 0) {
			value = `<span class="text-danger bold">${value}</span>`;
		}
		if (column.fieldname === "unallocated" && data.unallocated < 0) {
			value = `<span class="text-warning bold">${value}</span>`;
		}
		return value;
	},
};
