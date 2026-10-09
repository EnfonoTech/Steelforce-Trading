// Copyright (c) 2026, Enfono Technologies and contributors
// For license information, please see license.txt

// A Purchase Return raised on the INVOICE (a debit note) reduces what is owed to the supplier but
// moves no stock; only a return of the Purchase Receipt sends the goods back out of the warehouse.
// This lists the receipts where the first happened and the second did not.
frappe.query_reports["Purchase Receipts Pending Return"] = {
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
		{ fieldname: "supplier", label: __("Supplier"), fieldtype: "Link", options: "Supplier" },
		{ fieldname: "purchase_receipt", label: __("Purchase Receipt"), fieldtype: "Link", options: "Purchase Receipt" },
		{ fieldname: "invoice", label: __("Invoice"), fieldtype: "Link", options: "Purchase Invoice" },
		{ fieldname: "debit_note", label: __("Debit Note"), fieldtype: "Link", options: "Purchase Invoice" },
		{ fieldname: "item_code", label: __("Item"), fieldtype: "Link", options: "Item" },
		{ fieldname: "item_group", label: __("Item Group"), fieldtype: "Link", options: "Item Group" },
		{
			fieldname: "show",
			label: __("Show"),
			fieldtype: "Select",
			options: ["Pending Return", "All Debited"],
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
