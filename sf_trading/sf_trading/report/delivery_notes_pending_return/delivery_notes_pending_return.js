// Copyright (c) 2026, Enfono Technologies and contributors
// For license information, please see license.txt

// A Sales Return raised on the INVOICE credits the customer but moves no stock; only a Delivery
// Note return puts the goods back. This lists the Delivery Notes where the first happened and
// the second did not.
frappe.query_reports["Delivery Notes Pending Return"] = {
	filters: [
		{
			fieldname: "company",
			label: __("Company"),
			fieldtype: "Link",
			options: "Company",
			default: frappe.defaults.get_user_default("Company"),
			reqd: 1,
		},
		{ fieldname: "from_date", label: __("From Date"), fieldtype: "Date" },
		{ fieldname: "to_date", label: __("To Date"), fieldtype: "Date" },
		{ fieldname: "customer", label: __("Customer"), fieldtype: "Link", options: "Customer" },
		{ fieldname: "item_code", label: __("Item"), fieldtype: "Link", options: "Item" },
		{
			fieldname: "show",
			label: __("Show"),
			fieldtype: "Select",
			options: ["Pending Return", "All Credited"],
			default: "Pending Return",
		},
	],

	formatter: function (value, row, column, data, default_formatter) {
		value = default_formatter(value, row, column, data);
		if (data && column.fieldname === "status") {
			const colour = data.status === "Pending Return" ? "var(--red-600)" : "var(--green-600)";
			value = `<span style="color: ${colour}; font-weight: 600">${value}</span>`;
		}
		return value;
	},
};
