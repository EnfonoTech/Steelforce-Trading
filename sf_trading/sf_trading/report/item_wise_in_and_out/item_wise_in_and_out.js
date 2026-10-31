// Copyright (c) 2026, Enfono Technologies and contributors
// For license information, please see license.txt

// What came in (Purchase Invoices) and went out (Sales Invoices) per item, Cash or Credit, and how
// many days the stock sold took from purchase to sale (turn around time, first in first out).
// A row opens its invoices in Item-wise In and Out Detail, as the DCR opens DCR Detail: the item, or
// the figure clicked, with this report's filters carried over.

// fieldname -> what the figure counts, as Detail filters
var SF_IN_OUT_DRILL = {
	item_code: {},
	item_name: {},
	in_qty: { side: "In" },
	in_amount: { side: "In" },
	out_qty: { side: "Out" },
	out_amount: { side: "Out" },
	tat_days: { side: "Out" },
	unmatched_qty: { side: "Out" },
	cash_in: { side: "In", payment_mode: "Cash" },
	credit_in: { side: "In", payment_mode: "Credit" },
	cash_out: { side: "Out", payment_mode: "Cash" },
	cheque_out: { side: "Out", payment_mode: "Cheque" },
	credit_out: { side: "Out", payment_mode: "Credit" },
};

// The Detail report's address for one cell, every filter in the query string -- the way the DCR links
// its figures to DCR Detail. A multi-select (Branch) travels as JSON, which the report reads back.
function sf_in_out_detail_url(item_code, fieldname) {
	const filters = Object.assign({}, frappe.query_report.get_filter_values(), SF_IN_OUT_DRILL[fieldname] || {});
	filters.item_code = item_code;
	const params = new URLSearchParams();
	Object.entries(filters).forEach(([key, value]) => {
		if (value === undefined || value === null || value === "" || (Array.isArray(value) && !value.length)) return;
		params.set(key, Array.isArray(value) ? JSON.stringify(value) : value);
	});
	return `/app/query-report/${encodeURIComponent("Item-wise In and Out Detail")}?${params.toString()}`;
}

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

frappe.query_reports["Item-wise In and Out"] = {
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
		if (data && data[column.fieldname] === undefined) return "";
		const drill = data && data.item_code && column.fieldname in SF_IN_OUT_DRILL;
		// an empty figure opens nothing worth seeing
		if (drill && column.fieldname !== "item_code" && column.fieldname !== "item_name" && !data[column.fieldname]) {
			return default_formatter(value, row, column, data);
		}
		if (!drill) return default_formatter(value, row, column, data);
		// the Item column's own link would open the Item master; here it opens the item's invoices
		const shown = column.fieldname === "item_code"
			? frappe.utils.escape_html(data.item_code)
			: default_formatter(value, row, column, data);
		const url = frappe.utils.escape_html(sf_in_out_detail_url(data.item_code, column.fieldname));
		return `<a href="${url}" title="${__("Open the invoices")}">${shown}</a>`;
	},
};
