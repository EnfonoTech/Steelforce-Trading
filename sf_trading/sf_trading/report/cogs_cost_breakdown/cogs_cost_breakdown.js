// Copyright (c) 2026, Enfono Technologies and contributors
// For license information, please see license.txt

// How a Delivery Note's or Sales Invoice's COGS was reached: voucher, then its item lines, then the
// documents the warehouse stock was made of. A transfer row links to Transfer Cost Breakdown.
frappe.query_reports["COGS Cost Breakdown"] = {
	filters: [
		{
			fieldname: "company", label: __("Company"), fieldtype: "Link", options: "Company",
			default: frappe.defaults.get_user_default("Company"), reqd: 1,
		},
		{ fieldname: "from_date", label: __("From Date"), fieldtype: "Date", default: frappe.datetime.add_days(frappe.datetime.get_today(), -30) },
		{ fieldname: "to_date", label: __("To Date"), fieldtype: "Date", default: frappe.datetime.get_today() },
		{
			fieldname: "voucher_type", label: __("Voucher Type"), fieldtype: "Select",
			options: ["", "Delivery Note", "Sales Invoice"],
		},
		{
			fieldname: "voucher_no", label: __("Voucher No"), fieldtype: "Dynamic Link",
			get_options: () => frappe.query_report.get_filter_value("voucher_type"),
		},
		{ fieldname: "customer", label: __("Customer"), fieldtype: "Link", options: "Customer" },
		{ fieldname: "item_code", label: __("Item"), fieldtype: "Link", options: "Item" },
		{ fieldname: "warehouse", label: __("Warehouse"), fieldtype: "Link", options: "Warehouse" },
	],

	formatter(value, row, column, data, default_formatter) {
		return sf_cost_breakdown_format(value, row, column, data, default_formatter);
	},
};

// shared with Transfer Cost Breakdown: indent the tree, and link a transfer to its own breakdown
function sf_cost_breakdown_format(value, row, column, data, default_formatter) {
	// a column a row does not carry (a source row has no balance, a line no GL figure) stays blank, not 0
	if (data && data[column.fieldname] === undefined) return "";
	value = default_formatter(value, row, column, data);
	if (!data) return value;
	if (column.fieldname === "breakdown") {
		if (!data.breakdown) return "";
		return sf_transfer_breakdown_link(data);
	}
	if (column.fieldname === "doc_type" || column.fieldname === "item_code") {
		const pad = (data.indent || 0) * 14;
		if (pad) value = `<span style="padding-left:${pad}px">${value}</span>`;
	}
	if (!data.indent) value = `<b>${value}</b>`;
	if (column.fieldname === "difference" && Math.abs(flt(data.difference)) >= 0.01) {
		value = `<span style="color:var(--red-500)">${value}</span>`;
	}
	return value;
}

// Opening Transfer Cost Breakdown from inside itself is the same route with other filters, which the
// router does not reload -- so there the filter is changed in place, and anywhere else the route is set.
window.sf_open_transfer_breakdown = function (company, stock_entry) {
	const report = frappe.query_report;
	if (report && report.report_name === "Transfer Cost Breakdown") {
		report.set_filter_value({ company, stock_entry });
		return;
	}
	frappe.set_route("query-report", "Transfer Cost Breakdown", { company, stock_entry });
};

function sf_transfer_breakdown_link(data) {
	const company = frappe.utils.escape_html(data.company || "");
	const entry = frappe.utils.escape_html(data.document || "");
	return `<a href="#" onclick="sf_open_transfer_breakdown('${company}', '${entry}'); return false;">${__("Transfer Cost Breakdown")}</a>`;
}
