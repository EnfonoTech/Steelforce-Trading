// Copyright (c) 2026, Enfono Technologies and contributors
// For license information, please see license.txt

// Money in and out per item: each invoice's collection (and each bill's payment) spread over its
// lines by net amount, with every item's share of the total and its own collection ratio.
frappe.query_reports["Item-wise Cash Flow"] = {
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
		{ fieldname: "to_date", label: __("To Date"), fieldtype: "Date", default: frappe.datetime.get_today(), reqd: 1 },
		{
			fieldname: "group_by",
			label: __("Group By"),
			fieldtype: "Select",
			options: ["Item", "Item Group"],
			default: "Item",
		},
		{
			fieldname: "branch",
			label: __("Branch"),
			fieldtype: "MultiSelectList",
			get_data: (txt) => frappe.db.get_link_options("Branch", txt),
		},
		{ fieldname: "item_code", label: __("Item"), fieldtype: "Link", options: "Item" },
		{ fieldname: "item_group", label: __("Item Group"), fieldtype: "Link", options: "Item Group" },
		{ fieldname: "customer", label: __("Customer"), fieldtype: "Link", options: "Customer" },
		{ fieldname: "supplier", label: __("Supplier"), fieldtype: "Link", options: "Supplier" },
		{ fieldname: "sales_person", label: __("Sales Person"), fieldtype: "Link", options: "Sales Person" },
		{
			fieldname: "show",
			label: __("Show"),
			fieldtype: "Select",
			options: ["All", "With Sales", "With Purchases"],
			default: "All",
		},
		{ fieldname: "include_purchases", label: __("Include Purchases"), fieldtype: "Check", default: 1 },
	],

	formatter(value, row, column, data, default_formatter) {
		value = default_formatter(value, row, column, data);
		if (!data) return value;
		if (["net_cash", "to_collect"].includes(column.fieldname) && flt(data[column.fieldname]) < 0) {
			value = `<span style="color:#c62828">${value}</span>`;
		}
		if (column.fieldname === "collection_ratio" && data.collection_ratio !== null && flt(data.collection_ratio) < 50) {
			value = `<span style="color:#c62828">${value}</span>`;
		}
		return value;
	},
};
