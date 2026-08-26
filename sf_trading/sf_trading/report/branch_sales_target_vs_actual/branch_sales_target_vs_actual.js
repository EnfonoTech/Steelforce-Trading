// sf_trading/sf_trading/report/branch_sales_target_vs_actual/branch_sales_target_vs_actual.js
frappe.query_reports["Branch Sales Target vs Actual"] = {
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
			fieldname: "period", label: __("Period"), fieldtype: "Select",
			options: ["Monthly", "Quarterly", "Half-Yearly", "Yearly"].join("\n"),
			default: "Monthly",
		},
		{
			fieldname: "only_target_months", label: __("Only months with a target"),
			fieldtype: "Check", default: 1,
			description: __("Targets need not start in January. Untick to see the whole year."),
		},
		{ fieldname: "branch", label: __("Branch"), fieldtype: "Link", options: "Branch" },
	],

	formatter(value, row, column, data, default_formatter) {
		value = default_formatter(value, row, column, data);
		if (!data) return value;
		// a shortfall is the whole point of the report — colour it, in every period column
		if (column.fieldname.endsWith("_variance") || column.fieldname === "total_variance") {
			const raw = flt(data[column.fieldname]);
			if (raw < 0) value = `<span style="color:var(--red-500)">${value}</span>`;
			else if (raw > 0) value = `<span style="color:var(--green-600)">${value}</span>`;
		}
		if (column.fieldname === "achieved_pct") {
			const pct = flt(data.achieved_pct);
			const colour = pct >= 100 ? "var(--green-600)" : pct >= 80 ? "var(--orange-500)" : "var(--red-500)";
			value = `<span style="color:${colour}"><b>${value}</b></span>`;
		}
		return value;
	},
};
