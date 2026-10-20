// Copyright (c) 2026, Enfono Technologies and contributors
// For license information, please see license.txt

frappe.query_reports["Entries by User"] = {
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
			default: frappe.datetime.add_days(frappe.datetime.get_today(), -30),
			reqd: 1,
		},
		{ fieldname: "to_date", label: __("To Date"), fieldtype: "Date", default: frappe.datetime.get_today(), reqd: 1 },
		{ fieldname: "user", label: __("User"), fieldtype: "Link", options: "User" },
		{
			fieldname: "voucher_type",
			label: __("Voucher Type"),
			fieldtype: "Select",
			options: [
				"",
				"Sales Invoice",
				"Purchase Invoice",
				"Payment Entry",
				"Journal Entry",
				"Stock Entry",
				"Delivery Note",
				"Purchase Receipt",
				"Sales Order",
				"Purchase Order",
				"Quotation",
				"Stock Reconciliation",
				"Landed Cost Voucher",
				"Payment Advice",
				"Material Request",
			].join("\n"),
		},
		{
			fieldname: "view",
			label: __("View"),
			fieldtype: "Select",
			options: ["By User and Voucher Type", "Pivot (User x Voucher Type)"].join("\n"),
			default: "By User and Voucher Type",
			description: __("Pivot: one row per user, one column per voucher type."),
		},
	],
	formatter(value, row, column, data, default_formatter) {
		value = default_formatter(value, row, column, data);
		// the busiest user stands out
		if (column.fieldname === "rank" && data && data.rank === "1") {
			value = `<b style="color: var(--green-600)">${value}</b>`;
		}
		return value;
	},
};
