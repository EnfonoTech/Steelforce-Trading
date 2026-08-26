// sf_trading/sf_trading/report/sales_person_target_vs_actual/sales_person_target_vs_actual.js
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
		if (column.fieldname === "achieved_pct") {
			const pct = flt(data.achieved_pct);
			const colour = pct >= 100 ? "var(--green-600)" : pct >= 80 ? "var(--orange-500)" : "var(--red-500)";
			value = `<span style="color:${colour}"><b>${value}</b></span>`;
		}
		return value;
	},
};
