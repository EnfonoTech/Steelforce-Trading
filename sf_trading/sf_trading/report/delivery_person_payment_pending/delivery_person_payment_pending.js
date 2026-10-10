// Copyright (c) 2026, Enfono Technologies and contributors
// For license information, please see license.txt

// Cash each delivery person still has to hand in: by invoice, or person by person with the two
// rules that block their next cash sale (Payment Collection Days and Cash Collection Limit).
frappe.query_reports["Delivery Person Payment Pending"] = {
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
			fieldname: "view",
			label: __("View"),
			fieldtype: "Select",
			options: ["Invoice Wise", "Delivery Person Summary"],
			default: "Invoice Wise",
		},
		{
			fieldname: "status",
			label: __("Status"),
			fieldtype: "Select",
			options: ["Pending", "Overdue", "Paid", "All"],
			default: "Pending",
		},
		{ fieldname: "driver", label: __("Delivery Person"), fieldtype: "Link", options: "Driver" },
		{
			fieldname: "branch",
			label: __("Branch"),
			fieldtype: "MultiSelectList",
			get_data: (txt) => frappe.db.get_link_options("Branch", txt),
		},
		{ fieldname: "customer", label: __("Customer"), fieldtype: "Link", options: "Customer" },
		{ fieldname: "from_date", label: __("Invoice From"), fieldtype: "Date" },
		{ fieldname: "to_date", label: __("Invoice To"), fieldtype: "Date" },
	],

	formatter(value, row, column, data, default_formatter) {
		value = default_formatter(value, row, column, data);
		if (!data) return value;
		if (column.fieldname === "status" && data.status) {
			const colour = { Overdue: "red", "Within Days": "orange", Paid: "green" }[data.status] || "gray";
			value = `<span class="indicator-pill ${colour}">${frappe.utils.escape_html(__(data.status))}</span>`;
		}
		if (column.fieldname === "blocked" && data.blocked) {
			value = `<span class="text-danger bold">${value}</span>`;
		}
		if (["days_overdue", "overdue"].includes(column.fieldname) && data[column.fieldname] > 0) {
			value = `<span class="text-danger">${value}</span>`;
		}
		return value;
	},
};
