// Copyright (c) 2026, Enfono Technologies and contributors
// For license information, please see license.txt

// Advances paid to suppliers and received from customers against Purchase and Sales Orders, with the
// advances that carry no supporting voucher shown in red. An advance's date is its Payment Entry's
// posting date: the advance ledger itself has none.
frappe.query_reports["Supplier and Customer Advance Report"] = {
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
			fieldname: "party_type",
			label: __("Side"),
			fieldtype: "Select",
			options: ["All", "Supplier", "Customer"],
			default: "All",
		},
		{ fieldname: "supplier", label: __("Supplier"), fieldtype: "Link", options: "Supplier" },
		{ fieldname: "customer", label: __("Customer"), fieldtype: "Link", options: "Customer" },
		{
			fieldname: "branch",
			label: __("Branch"),
			fieldtype: "MultiSelectList",
			get_data: (txt) => frappe.db.get_link_options("Branch", txt),
		},
		{ fieldname: "from_date", label: __("Advance From Date"), fieldtype: "Date" },
		{ fieldname: "to_date", label: __("Advance To Date"), fieldtype: "Date" },
		{ fieldname: "order_from_date", label: __("Order From Date"), fieldtype: "Date" },
		{ fieldname: "order_to_date", label: __("Order To Date"), fieldtype: "Date" },
		{ fieldname: "mode_of_payment", label: __("Mode of Payment"), fieldtype: "Link", options: "Mode of Payment" },
		{ fieldname: "order_no", label: __("Order No (contains)"), fieldtype: "Data" },
		{
			fieldname: "status",
			label: __("Status"),
			fieldtype: "Select",
			options: ["All", "Unapplied", "Part Adjusted", "Adjusted", "No Supporting Voucher"],
			default: "All",
		},
		{ fieldname: "older_than_days", label: __("Unapplied Older Than (Days)"), fieldtype: "Int" },
		{
			fieldname: "view",
			label: __("View"),
			fieldtype: "Select",
			options: ["Advance Entries", "Order Summary", "Party Summary"],
			default: "Advance Entries",
		},
	],

	formatter(value, row, column, data, default_formatter) {
		value = default_formatter(value, row, column, data);
		if (!data) return value;
		const flagged = cint(data.flagged);
		if (column.fieldname === "status" && data.status) {
			const colour =
				{
					"No Supporting Voucher": "red",
					Unapplied: "orange",
					"Part Adjusted": "yellow",
					Adjusted: "green",
				}[data.status] || "gray";
			value = `<span class="indicator-pill ${colour}">${frappe.utils.escape_html(__(data.status))}</span>`;
		} else if (column.fieldname === "supporting" && flagged) {
			value = `<span class="text-danger bold">${__("NONE")}</span>`;
		} else if (flagged && ["party_name", "kind", "advance", "unapplied", "no_voucher", "remarks"].includes(column.fieldname)) {
			value = `<span class="text-danger">${value}</span>`;
		}
		if (column.fieldname === "remarks" && data.remarks && !flagged) {
			value = `<span style="color: var(--orange-600)">${value}</span>`;
		}
		return value;
	},
};
