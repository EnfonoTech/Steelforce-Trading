// sf_trading/sf_trading/report/sales_target_scorecard/sales_target_scorecard.js
frappe.query_reports["Sales Target Scorecard"] = {
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
			fieldname: "dimension_type", label: __("Target For"), fieldtype: "Select",
			options: ["Branch", "Sales Person"].join("\n"), default: "Branch",
		},
		{
			fieldname: "basis", label: __("Measured On"), fieldtype: "Select",
			options: ["Net of VAT", "Gross"].join("\n"), default: "Net of VAT",
		},
		{ fieldname: "as_on", label: __("As On"), fieldtype: "Date", default: frappe.datetime.get_today() },
		{
			fieldname: "from_date", label: __("From Date"), fieldtype: "Date",
			description: __("Optional. Narrows the fiscal year; a part month's target is prorated."),
		},
		{ fieldname: "to_date", label: __("To Date"), fieldtype: "Date" },
		{ fieldname: "branch", label: __("Branch"), fieldtype: "Link", options: "Branch" },
	],

	formatter(value, row, column, data, default_formatter) {
		value = default_formatter(value, row, column, data);
		if (!data) return value;
		for (const field of ["mtd_pct", "ytd_pct"]) {
			if (column.fieldname === field) {
				const pct = flt(data[field]);
				const colour = pct >= 100 ? "var(--green-600)" : pct >= 80 ? "var(--orange-500)" : "var(--red-500)";
				value = `<span style="color:${colour}"><b>${value}</b></span>`;
			}
		}
		if (column.fieldname === "variance" && flt(data.variance) < 0) {
			value = `<span style="color:var(--red-500)">${value}</span>`;
		}
		return value;
	},
};
