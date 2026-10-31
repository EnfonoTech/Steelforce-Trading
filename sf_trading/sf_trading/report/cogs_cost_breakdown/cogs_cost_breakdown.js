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
		{ fieldname: "branch", label: __("Branch"), fieldtype: "Link", options: "Branch" },
		{
			fieldname: "cost_center",
			label: __("Cost Center"),
			fieldtype: "Link",
			options: "Cost Center",
			get_query: () => ({
				filters: { company: frappe.query_report.get_filter_value("company") },
			}),
		},
		{
			fieldname: "item_group",
			label: __("Item Group"),
			fieldtype: "Link",
			options: "Item Group",
		},
		{
			fieldname: "customer_group",
			label: __("Customer Group"),
			fieldtype: "Link",
			options: "Customer Group",
		},
		{
			fieldname: "sales_person",
			label: __("Sales Person"),
			fieldtype: "Link",
			options: "Sales Person",
		},
		{
			fieldname: "returns",
			label: __("Returns"),
			fieldtype: "Select",
			options: ["Include", "Exclude", "Only"],
			default: "Include",
			description: __(
				"Credit notes with Update Stock; ERPNext's Gross Profit leaves them out."
			),
		},
		{
			fieldname: "margin",
			label: __("Margin"),
			fieldtype: "Select",
			options: ["", "Below cost", "Over 5x cost"],
			description: __(
				"Sale lines sold below what the stock cost, or at over five times it."
			),
		},
		{
			fieldname: "only_differences",
			label: __("Only Differences"),
			fieldtype: "Check",
			description: __("Vouchers where the ledger cost and the GL do not agree."),
		},
		{
			fieldname: "only_review",
			label: __("Only Lines To Review"),
			fieldtype: "Check",
			description: __("Lines the Check column does not call a match."),
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
	if (column.fieldname === "difference" && data.differs) value = red(value);
	if (column.fieldname === "margin" && flt(data.margin) < 0) value = red(value);
	if (column.fieldname === "check" && sf_needs_review(data.check_flag)) value = red(value);
	if (column.fieldname === "original_rate" && data.check_flag === "return_differs")
		value = red(value);
	if (data.gap && ["source_type", "qty", "rate", "value"].includes(column.fieldname))
		value = red(value);
	if (data.component) value = `<i style="color:var(--text-muted)">${value}</i>`;
	return value;
}

// the flags the engine puts on the "lines to review" list (a standalone return is routine, so it is not one)
function sf_needs_review(flag) {
	return ["negative", "differs", "return_differs", "gap"].includes(flag);
}

// Opening Transfer Cost Breakdown from inside itself is the same route with other filters, which the
// router does not reload -- so there the filter is changed in place, and anywhere else the route is set.
window.sf_open_transfer_breakdown = function (company, stock_entry, item_code) {
	// a transfer may move several items: open it on the one this row is about
	const filters = { company, stock_entry, item_code: item_code || "" };
	const report = frappe.query_report;
	if (report && report.report_name === "Transfer Cost Breakdown") {
		report.set_filter_value(filters);
		return;
	}
	// the Item filter shows the item's name, fetched after the report's first run -- which then went out
	// without it; Transfer Cost Breakdown's onload runs it again once the item is in (sf_rerun_with_item)
	window.sf_transfer_wanted_item = filters.item_code;
	frappe.set_route("query-report", "Transfer Cost Breakdown", filters);
};

// Transfer Cost Breakdown onload: the item a link asked for is in the filter only after the first run
window.sf_rerun_with_item = function (report) {
	const wanted = window.sf_transfer_wanted_item;
	window.sf_transfer_wanted_item = null;
	if (!wanted) return;
	let tries = 25;
	const check = () => {
		if (report.get_filter_value("item_code") === wanted) report.refresh();
		else if (--tries > 0) setTimeout(check, 200);
	};
	setTimeout(check, 200);
};

// the names go through JSON so a quote in a company name is a character in a string, not the end of it
function sf_transfer_breakdown_link(data) {
	const args = [data.company || "", data.document || "", data.breakdown_item || ""]
		.map((v) => frappe.utils.escape_html(JSON.stringify(v)))
		.join(", ");
	return `<a href="#" onclick="sf_open_transfer_breakdown(${args}); return false;">${__(
		"Transfer Cost Breakdown"
	)}</a>`;
}
