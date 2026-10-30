// Copyright (c) 2026, Enfono Technologies and contributors
// For license information, please see license.txt

// How a Delivery Note's or Sales Invoice's COGS was reached: voucher, then its item lines, then the
// documents the warehouse stock was made of. A transfer row links to Transfer Cost Breakdown, or is
// followed back in place when Expand Transfer Chain is ticked.
frappe.query_reports["COGS Cost Breakdown"] = {
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
		},
		{
			fieldname: "to_date",
			label: __("To Date"),
			fieldtype: "Date",
			default: frappe.datetime.get_today(),
		},
		{
			fieldname: "voucher_type",
			label: __("Voucher Type"),
			fieldtype: "Select",
			options: ["", "Delivery Note", "Sales Invoice"],
		},
		{
			fieldname: "voucher_no",
			label: __("Voucher No"),
			fieldtype: "Dynamic Link",
			get_options: () => frappe.query_report.get_filter_value("voucher_type"),
		},
		{ fieldname: "customer", label: __("Customer"), fieldtype: "Link", options: "Customer" },
		{ fieldname: "item_code", label: __("Item"), fieldtype: "Link", options: "Item" },
		{
			fieldname: "warehouse",
			label: __("Warehouse"),
			fieldtype: "Link",
			options: "Warehouse",
		},
		{
			fieldname: "expand_chain",
			label: __("Expand Transfer Chain"),
			fieldtype: "Check",
			description: __("Follow a transfer back to the documents its source warehouse held."),
		},
	],

	formatter(value, row, column, data, default_formatter) {
		return sf_cost_breakdown_format(value, row, column, data, default_formatter);
	},
};

// shared with Transfer Cost Breakdown: indent the tree, flag what does not add up, and link a transfer
// to its own breakdown
function sf_cost_breakdown_format(value, row, column, data, default_formatter) {
	// a column a row does not carry (a source row has no balance, a line no GL figure) stays blank, not 0
	if (data && (data[column.fieldname] === undefined || data[column.fieldname] === null))
		return "";
	value = default_formatter(value, row, column, data);
	if (!data) return value;
	if (column.fieldname === "breakdown") {
		if (!data.breakdown) return "";
		return sf_transfer_breakdown_link(data);
	}

	const pad = (data.indent || 0) * 14;
	const first_cell =
		column.fieldname === "doc_type" ||
		column.fieldname === "item_code" ||
		(column.fieldname === "source_type" && !data.doc_type);
	if (pad && first_cell) value = `<span style="padding-left:${pad}px">${value}</span>`;
	if (!data.indent) value = `<b>${value}</b>`;

	const red = (html) => `<span style="color:var(--red-500)">${html}</span>`;
	if (column.fieldname === "difference" && Math.abs(flt(data.difference)) >= 0.01)
		value = red(value);
	if (column.fieldname === "margin" && flt(data.margin) < 0) value = red(value);
	if (column.fieldname === "check" && data.check_flag && data.check_flag !== "ok")
		value = red(value);
	if (data.gap && ["source_type", "qty", "rate", "value"].includes(column.fieldname))
		value = red(value);
	if (data.component) value = `<i style="color:var(--text-muted)">${value}</i>`;
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

// the names go through JSON so a quote in a company name is a character in a string, not the end of it
function sf_transfer_breakdown_link(data) {
	const args = [data.company || "", data.document || ""]
		.map((v) => frappe.utils.escape_html(JSON.stringify(v)))
		.join(", ");
	return `<a href="#" onclick="sf_open_transfer_breakdown(${args}); return false;">${__(
		"Transfer Cost Breakdown"
	)}</a>`;
}
