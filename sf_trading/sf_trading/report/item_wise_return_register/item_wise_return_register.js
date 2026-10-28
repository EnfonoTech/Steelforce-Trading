// Copyright (c) 2026, Enfono Technologies and contributors
// For license information, please see license.txt

// Every returned item line, sales or purchase -- read through one kind of return document at a
// time (Credit / Debit Note, or Delivery Note / Purchase Receipt), so nothing is counted twice.
const SF_RETURN_IS_SALES = "eval:(doc.return_type || 'Sales') == 'Sales'";
const SF_RETURN_IS_PURCHASE = "eval:doc.return_type == 'Purchase'";

frappe.query_reports["Item Wise Return Register"] = {
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
			fieldname: "return_type",
			label: __("Return Type"),
			fieldtype: "Select",
			options: ["Sales", "Purchase"],
			default: "Sales",
			reqd: 1,
		},
		{
			fieldname: "return_through",
			label: __("Return Through"),
			fieldtype: "Select",
			options: ["Credit / Debit Note", "Delivery Note / Purchase Receipt"],
			default: "Credit / Debit Note",
			reqd: 1,
		},
		{
			fieldname: "from_date",
			label: __("From Date"),
			fieldtype: "Date",
			default: frappe.datetime.month_start(),
		},
		{
			fieldname: "to_date",
			label: __("To Date"),
			fieldtype: "Date",
			default: frappe.datetime.get_today(),
		},
		{ fieldname: "branch", label: __("Branch"), fieldtype: "Link", options: "Branch" },
		{
			fieldname: "customer",
			label: __("Customer"),
			fieldtype: "Link",
			options: "Customer",
			depends_on: SF_RETURN_IS_SALES,
		},
		{
			fieldname: "supplier",
			label: __("Supplier"),
			fieldtype: "Link",
			options: "Supplier",
			depends_on: SF_RETURN_IS_PURCHASE,
		},
		{ fieldname: "item_code", label: __("Item"), fieldtype: "Link", options: "Item" },
		{ fieldname: "item_group", label: __("Item Group"), fieldtype: "Link", options: "Item Group" },
		{ fieldname: "warehouse", label: __("Warehouse"), fieldtype: "Link", options: "Warehouse" },
		{
			fieldname: "sales_return_reason",
			label: __("Return Reason"),
			fieldtype: "Link",
			options: "Sales Return Reason",
			depends_on: SF_RETURN_IS_SALES,
		},
		{
			fieldname: "purchase_return_reason",
			label: __("Return Reason"),
			fieldtype: "Link",
			options: "Purchase Return Reason",
			depends_on: SF_RETURN_IS_PURCHASE,
		},
		{
			fieldname: "sales_person",
			label: __("Sales Person"),
			fieldtype: "Link",
			options: "Sales Person",
			depends_on: SF_RETURN_IS_SALES,
		},
		{
			fieldname: "view",
			label: __("View"),
			fieldtype: "Select",
			options: ["Line Wise", "Item Wise"],
			default: "Line Wise",
		},
	],

	formatter(value, row, column, data, default_formatter) {
		// the total row has no data; a column that does not add up (disable_total) shows nothing there,
		// not a formatted zero
		if (!data && column.disable_total) return "";
		return default_formatter(value, row, column, data);
	},
};
