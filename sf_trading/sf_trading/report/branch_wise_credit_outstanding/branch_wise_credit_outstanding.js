// Copyright (c) 2026, Enfono Technologies and contributors
// For license information, please see license.txt

// What each credit customer owes at each branch, aged, against the company-wide credit limit and
// the branch's own sub-limit. Outstanding comes from the Payment Ledger as on the chosen date.
frappe.query_reports["Branch-wise Credit Outstanding"] = {
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
		{ fieldname: "customer", label: __("Customer"), fieldtype: "Link", options: "Customer" },
		{ fieldname: "customer_group", label: __("Customer Group"), fieldtype: "Link", options: "Customer Group" },
		{ fieldname: "sales_person", label: __("Sales Person"), fieldtype: "Link", options: "Sales Person" },
		{
			fieldname: "status",
			label: __("Status"),
			fieldtype: "Select",
			options: ["All", "Over Limit", "Overdue", "Within Limit"],
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
		{
			fieldname: "credit_customers_only",
			label: __("Credit Customers Only"),
			fieldtype: "Check",
			default: 1,
		},
		{ fieldname: "show_zero", label: __("Include Nothing Outstanding"), fieldtype: "Check", default: 0 },
		{
			fieldname: "view",
			label: __("View"),
			fieldtype: "Select",
			options: ["Customer and Branch", "Branch Summary", "Document Detail"],
			default: "Customer and Branch",
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
			const colour = { "Over Limit": "red", Overdue: "orange", "Within Limit": "green" }[data.status] || "gray";
			value = `<span class="indicator-pill ${colour}">${frappe.utils.escape_html(__(data.status))}</span>`;
		}
		if (["overdue", "available"].includes(column.fieldname) && data[column.fieldname] < 0) {
			value = `<span class="text-danger">${value}</span>`;
		}
		if (column.fieldname === "overdue" && data.overdue > 0) {
			value = `<span class="text-danger bold">${value}</span>`;
		}
		return value;
	},
};
