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
		{ fieldname: "delivery_note", label: __("Delivery Note"), fieldtype: "Link", options: "Delivery Note" },
		{ fieldname: "invoice", label: __("Invoice"), fieldtype: "Link", options: "Sales Invoice" },
		{ fieldname: "credit_note", label: __("Credit Note"), fieldtype: "Link", options: "Sales Invoice" },
		{ fieldname: "item_code", label: __("Item"), fieldtype: "Link", options: "Item" },
		{ fieldname: "item_group", label: __("Item Group"), fieldtype: "Link", options: "Item Group" },
		{
			fieldname: "show",
			label: __("Show"),
			fieldtype: "Select",
			options: ["Pending Return", "All Credited"],
			default: "Pending Return",
		},
		{
			fieldname: "view",
			label: __("View"),
			fieldtype: "Select",
			options: ["Line Wise", "Document Wise", "Item Wise"],
			default: "Line Wise",
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
