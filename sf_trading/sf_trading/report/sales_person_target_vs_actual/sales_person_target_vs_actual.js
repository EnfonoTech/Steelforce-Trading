// sf_trading/sf_trading/report/sales_person_target_vs_actual/sales_person_target_vs_actual.js
const SF_REPORT_NAME = "Sales Person Target vs Actual";

// ── Drill-down ────────────────────────────────────────────────────────────────
// The same report, reopened in Invoice-wise mode and scoped to the row (and, on a period cell,
// to that period's own dates). No second report to keep in step with this one -- DCR Detailed is
// a separate document because it lists a different shape; here the detail IS this report's other
// view, so the figures can never disagree.
function sf_drill_link(value, row_value, dimension_filter, from_date, to_date) {
	const f = (frappe.query_report && frappe.query_report.get_filter_values(false)) || {};
	const params = {
		view: "Invoice-wise",
		company: f.company || "",
		fiscal_year: f.fiscal_year || "",
		basis: f.basis || "Net of VAT",
		period: f.period || "Monthly",
		from_date: from_date || f.from_date || "",
		to_date: to_date || f.to_date || "",
	};
	if (f.branch) params.branch = f.branch;
	params[dimension_filter] = row_value;

	const qs = Object.keys(params)
		.filter((k) => params[k] !== "" && params[k] != null)
		.map((k) => encodeURIComponent(k) + "=" + encodeURIComponent(params[k]))
		.join("&");
	const route = "/app/query-report/" + encodeURIComponent(SF_REPORT_NAME) + "?" + qs;
	return `<a href="${route}" title="${__("Show the invoices behind this figure")}">${value}</a>`;
}

function sf_add_drill(value, row, column, data, dimension_filter) {
	// no drilling from the detail view itself, and never from the Unassigned bucket -- it has no
	// dimension value to filter on
	const f = (frappe.query_report && frappe.query_report.get_filter_values(false)) || {};
	if ((f.view || "Summary") === "Invoice-wise") return value;
	if (!data || !data.dimension_value || data.dimension_value === "Unassigned") return value;

	if (column.fieldname === "dimension_value") {
		return sf_drill_link(value, data.dimension_value, dimension_filter,
			data.window_from, data.window_to);
	}
	if (column.fieldname === "total_actual") {
		return sf_drill_link(value, data.dimension_value, dimension_filter,
			data.window_from, data.window_to);
	}
	const bucket = /^(b\d+)_actual$/.exec(column.fieldname);
	if (bucket) {
		const key = bucket[1];
		return sf_drill_link(value, data.dimension_value, dimension_filter,
			data[key + "_from"], data[key + "_to"]);
	}
	return value;
}

frappe.query_reports["Sales Person Target vs Actual"] = {
	filters: [
		{
			fieldname: "company", label: __("Company"), fieldtype: "Link", options: "Company",
			reqd: 1, default: frappe.defaults.get_user_default("Company"),
		},
		{
			fieldname: "fiscal_year", label: __("Fiscal Year"), fieldtype: "Link",
			options: "Fiscal Year", reqd: 1,
			default: frappe.defaults.get_user_default("fiscal_year"),
		},
		{
			fieldname: "basis", label: __("Measured On"), fieldtype: "Select",
			options: ["Net of VAT", "Gross"].join("\n"), default: "Net of VAT",
		},
		{
			fieldname: "view", label: __("View"), fieldtype: "Select",
			options: ["Summary", "Invoice-wise"].join("\n"), default: "Summary",
			description: __("Invoice-wise lists the invoices behind the figures, one row each."),
		},
		{
			fieldname: "from_date", label: __("From Date"), fieldtype: "Date",
			description: __("Optional. Narrows the fiscal year; a part month's target is prorated."),
		},
		{ fieldname: "to_date", label: __("To Date"), fieldtype: "Date" },
		{
			fieldname: "period", label: __("Period"), fieldtype: "Select",
			options: ["Monthly", "Quarterly", "Half-Yearly", "Yearly"].join("\n"),
			default: "Monthly",
		},
		{
			fieldname: "branch", label: __("Branch"), fieldtype: "Link", options: "Branch",
			description: __("One branch at a time reads a cross-branch seller correctly."),
		},
		{
			fieldname: "sales_person", label: __("Sales Person"), fieldtype: "Link",
			options: "Sales Person",
			description: __("Invoice-wise only. Narrows the list to one seller."),
			get_query: () => ({ filters: { is_group: 0 } }),
		},
		{
			fieldname: "row_limit", label: __("Row Limit"), fieldtype: "Int", default: 5000,
			description: __("Invoice-wise only."),
		},
	],

	formatter(value, row, column, data, default_formatter) {
		value = default_formatter(value, row, column, data);
		if (!data) return value;
		if (data.dimension_value === "Unassigned") {
			value = `<span style="color:var(--gray-600)">${value}</span>`;
		}
		if (column.fieldname.endsWith("_variance") || column.fieldname === "total_variance") {
			const raw = flt(data[column.fieldname]);
			if (raw < 0) value = `<span style="color:var(--red-500)">${value}</span>`;
			else if (raw > 0) value = `<span style="color:var(--green-600)">${value}</span>`;
		}
		if (data.is_return) {
			value = `<span style="color:var(--red-500)">${value}</span>`;
		}
		if (column.fieldname === "achieved_pct") {
			const pct = flt(data.achieved_pct);
			const colour = pct >= 100 ? "var(--green-600)" : pct >= 80 ? "var(--orange-500)" : "var(--red-500)";
			value = `<span style="color:${colour}"><b>${value}</b></span>`;
		}
		return sf_add_drill(value, row, column, data, "sales_person");
	},
};
