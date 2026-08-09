// Copyright (c) 2026, Enfono Technologies and contributors
// For license information, please see license.txt

frappe.query_reports["Purchase Register Extended"] = {
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
		{
			fieldname: "to_date",
			label: __("To Date"),
			fieldtype: "Date",
			default: frappe.datetime.month_end(),
			reqd: 1,
		},
		{
			fieldname: "supplier",
			label: __("Supplier"),
			fieldtype: "Link",
			options: "Supplier",
		},
		{
			fieldname: "supplier_group",
			label: __("Supplier Group"),
			fieldtype: "Link",
			options: "Supplier Group",
		},
		{
			fieldname: "cost_center",
			label: __("Branch (Cost Center)"),
			fieldtype: "Link",
			options: "Cost Center",
			get_query: () => {
				return { filters: { company: frappe.query_report.get_filter_value("company") } };
			},
		},
		{
			fieldname: "mode_of_payment",
			label: __("Mode of Payment"),
			fieldtype: "Link",
			options: "Mode of Payment",
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
		{
			fieldname: "item_group",
			label: __("Item Group"),
			fieldtype: "Link",
			options: "Item Group",
		},
		{
			// goods by default: purchases in the trading account means stock. All
			// Items widens BOTH halves together and lines the invoice half back up
			// with core's Purchase Register, which counts service bills too.
			fieldname: "item_type",
			label: __("Item Type"),
			fieldtype: "Select",
			options: ["Stock Items Only", "All Items"].join("\n"),
			default: "Stock Items Only",
			reqd: 1,
		},
		{
			// a Select, not a checkbox: query_report.js drops filters whose value is
			// falsy, so an unticked Check never reaches the server and the receipts
			// could not be switched off
			fieldname: "scope",
			label: __("Show"),
			fieldtype: "Select",
			options: [
				"Invoices and Unbilled Receipts",
				"Invoices Only",
				"Unbilled Receipts Only",
			].join("\n"),
			default: "Invoices and Unbilled Receipts",
			reqd: 1,
		},
	],

	formatter: function (value, row, column, data, default_formatter) {
		value = default_formatter(value, row, column, data);
		// the receipt lines are the ones core never showed - mark them so the
		// reader can see at a glance what this report adds
		if (data && data.voucher_type === "Purchase Receipt") {
			// returns read as reductions, so colour them apart from receipts
			if (["voucher_type", "voucher_no", "status", "net_amount", "total_amount"].includes(column.fieldname)) {
				const tone = flt(data.net_amount) < 0 ? "var(--red-600)" : "var(--orange-600)";
				value = `<span style="color: ${tone}">${value}</span>`;
			}
		}
		return value;
	},
};
