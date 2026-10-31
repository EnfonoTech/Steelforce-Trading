// Copyright (c) 2026, Enfono Technologies and contributors
// For license information, please see license.txt

// The invoice lines behind Item-wise In and Out -- usually opened from one of its rows, with the
// item and the figure clicked already filled in.
// A Link filter that shows a title (Item here shows its name) is filled in from the address only once
// that title has been fetched -- after the page's first run, which then went out without it. Run once
// more as soon as every Link value in the address is in its filter.
function sf_in_out_rerun_with_route_links(report) {
	const params = new URLSearchParams(window.location.search);
	const links = [...params.keys()].filter((key) => {
		const filter = report.get_filter(key);
		return filter && filter.df.fieldtype === "Link";
	});
	if (!links.length) return;
	let tries = 25;
	const check = () => {
		if (links.every((key) => report.get_filter_value(key) === params.get(key))) {
			report.refresh();
		} else if (--tries > 0) {
			setTimeout(check, 200);
		}
	};
	setTimeout(check, 200);
}

frappe.query_reports["Item-wise In and Out Detail"] = {
	onload: sf_in_out_rerun_with_route_links,
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
		{ fieldname: "item_code", label: __("Item"), fieldtype: "Link", options: "Item" },
		{ fieldname: "item_group", label: __("Item Group"), fieldtype: "Link", options: "Item Group" },
		{
			fieldname: "side",
			label: __("In / Out"),
			fieldtype: "Select",
			options: ["", "In", "Out"],
		},
		{
			fieldname: "payment_mode",
			label: __("Payment Mode"),
			fieldtype: "Select",
			options: ["", "Cash", "Credit", "Cheque"],
		},
		{
			fieldname: "branch",
			label: __("Branch"),
			fieldtype: "MultiSelectList",
			get_data: (txt) => frappe.db.get_link_options("Branch", txt),
		},
		{ fieldname: "customer", label: __("Customer"), fieldtype: "Link", options: "Customer" },
		{ fieldname: "supplier", label: __("Supplier"), fieldtype: "Link", options: "Supplier" },
	],
	formatter(value, row, column, data, default_formatter) {
		// a purchase has no Out columns, a sale no In columns: leave the other side blank
		if (data && data[column.fieldname] === undefined) return "";
		return default_formatter(value, row, column, data);
	},
};
