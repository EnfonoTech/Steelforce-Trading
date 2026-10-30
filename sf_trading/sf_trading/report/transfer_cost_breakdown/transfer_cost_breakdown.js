// Copyright (c) 2026, Enfono Technologies and contributors
// For license information, please see license.txt

// How a Material Transfer's rate was reached in its source warehouse: the transfer line, then the
// documents the source balance was made of. An earlier transfer links to its own breakdown, or is
// followed back in place when Expand Transfer Chain is ticked.
frappe.query_reports["Transfer Cost Breakdown"] = {
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
			fieldname: "stock_entry",
			label: __("Stock Entry"),
			fieldtype: "Link",
			options: "Stock Entry",
			get_query: () => ({ filters: { purpose: "Material Transfer", docstatus: 1 } }),
			description: __("Dates are ignored when a Stock Entry is picked."),
		},
		{ fieldname: "item_code", label: __("Item"), fieldtype: "Link", options: "Item" },
		{
			fieldname: "item_group",
			label: __("Item Group"),
			fieldtype: "Link",
			options: "Item Group",
		},
		{
			fieldname: "source_warehouse",
			label: __("Source Warehouse"),
			fieldtype: "Link",
			options: "Warehouse",
		},
		{
			fieldname: "target_warehouse",
			label: __("Target Warehouse"),
			fieldtype: "Link",
			options: "Warehouse",
		},
		{
			fieldname: "branch",
			label: __("Branch"),
			fieldtype: "Link",
			options: "Branch",
			description: __(
				"Transfers into or out of the warehouses this branch lists. A warehouse can sit under several branches."
			),
		},
		{
			fieldname: "only_gaps",
			label: __("Only Gaps"),
			fieldtype: "Check",
			description: __("Lines that arrived worth a different amount than they left."),
		},
		{
			fieldname: "only_review",
			label: __("Only Lines To Review"),
			fieldtype: "Check",
			description: __("Lines the Check column does not call a match, or with a value gap."),
		},
		{
			fieldname: "expand_chain",
			label: __("Expand Transfer Chain"),
			fieldtype: "Check",
			description: __("Follow a transfer back to the documents its source warehouse held."),
		},
	],

	formatter(value, row, column, data, default_formatter) {
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
			(column.fieldname === "source_type" && !data.doc_type);
		if (pad && first_cell) value = `<span style="padding-left:${pad}px">${value}</span>`;
		if (!data.indent) value = `<b>${value}</b>`;

		const red = (html) => `<span style="color:var(--red-500)">${html}</span>`;
		if (column.fieldname === "check" && sf_needs_review(data.check_flag)) value = red(value);
		if (column.fieldname === "value_gap" && data.differs) value = red(value);
		if (data.gap && ["source_type", "qty", "rate", "value"].includes(column.fieldname))
			value = red(value);
		if (data.component) value = `<i style="color:var(--text-muted)">${value}</i>`;
		return value;
	},
};

// the flags the engine puts on the "lines to review" list
function sf_needs_review(flag) {
	return ["negative", "differs", "return_differs", "gap"].includes(flag);
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
