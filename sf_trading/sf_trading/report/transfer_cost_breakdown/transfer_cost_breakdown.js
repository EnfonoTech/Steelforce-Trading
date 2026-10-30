// Copyright (c) 2026, Enfono Technologies and contributors
// For license information, please see license.txt

// How a Material Transfer's rate was reached in its source warehouse: the transfer line, then the
// documents the source balance was made of. An earlier transfer links to its own breakdown.
frappe.query_reports["Transfer Cost Breakdown"] = {
	filters: [
		{
			fieldname: "company", label: __("Company"), fieldtype: "Link", options: "Company",
			default: frappe.defaults.get_user_default("Company"), reqd: 1,
		},
		{ fieldname: "from_date", label: __("From Date"), fieldtype: "Date", default: frappe.datetime.add_days(frappe.datetime.get_today(), -30) },
		{ fieldname: "to_date", label: __("To Date"), fieldtype: "Date", default: frappe.datetime.get_today() },
		{
			fieldname: "stock_entry", label: __("Stock Entry"), fieldtype: "Link", options: "Stock Entry",
			get_query: () => ({ filters: { purpose: "Material Transfer", docstatus: 1 } }),
			description: __("Dates are ignored when a Stock Entry is picked."),
		},
		{ fieldname: "item_code", label: __("Item"), fieldtype: "Link", options: "Item" },
		{ fieldname: "item_group", label: __("Item Group"), fieldtype: "Link", options: "Item Group" },
		{ fieldname: "source_warehouse", label: __("Source Warehouse"), fieldtype: "Link", options: "Warehouse" },
		{ fieldname: "target_warehouse", label: __("Target Warehouse"), fieldtype: "Link", options: "Warehouse" },
	],

	formatter(value, row, column, data, default_formatter) {
		// a column a row does not carry (a source row has no balance, a line no GL figure) stays blank, not 0
		if (data && data[column.fieldname] === undefined) return "";
		value = default_formatter(value, row, column, data);
		if (!data) return value;
		if (column.fieldname === "breakdown") {
			if (!data.breakdown) return "";
			return sf_transfer_breakdown_link(data);
		}
		if (column.fieldname === "doc_type" && data.indent) {
			value = `<span style="padding-left:${data.indent * 14}px">${value}</span>`;
		}
		if (!data.indent) value = `<b>${value}</b>`;
		return value;
	},
};

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
