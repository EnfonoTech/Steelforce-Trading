// sf_trading/sf_trading/report/loyalty_rewards_report/loyalty_rewards_report.js
frappe.query_reports["Loyalty Rewards Report"] = {
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
			default: frappe.datetime.add_months(frappe.datetime.get_today(), -3),
			reqd: 1,
		},
		{
			fieldname: "to_date",
			label: __("To Date"),
			fieldtype: "Date",
			default: frappe.datetime.get_today(),
			reqd: 1,
		},
		{
			fieldname: "journal_template",
			label: __("Journal Template"),
			fieldtype: "Link",
			options: "Journal Entry Template",
			default: "Loyalty Reward Entry",
		},
		{ fieldname: "customer", label: __("Customer"), fieldtype: "Link", options: "Customer" },
		{
			fieldname: "sales_invoice",
			label: __("Sales Invoice"),
			fieldtype: "Link",
			options: "Sales Invoice",
		},
		{
			fieldname: "status",
			label: __("JE Status"),
			fieldtype: "Select",
			options: ["Draft + Submitted", "Submitted", "Draft", "Cancelled", "All"].join("\n"),
			default: "Draft + Submitted",
		},
		{
			fieldname: "cost_center",
			label: __("Cost Center"),
			fieldtype: "Link",
			options: "Cost Center",
		},
		{ fieldname: "min_amount", label: __("Min Reward Amount"), fieldtype: "Currency" },
		{
			fieldname: "only_unlinked",
			label: __("Only Unlinked (no Sales Invoice)"),
			fieldtype: "Check",
			default: 0,
		},
		{
			fieldname: "summarise_by_customer",
			label: __("Summarise by Customer"),
			fieldtype: "Check",
			default: 0,
		},
	],

	formatter: function (value, row, column, data, default_formatter) {
		value = default_formatter(value, row, column, data);
		if (!data) return value;

		// unlinked journals are the ones needing attention
		if (column.fieldname === "sales_invoice" && !data.sales_invoice) {
			value = '<span style="color:var(--red-500)"><b>' + __("Not linked") + "</b></span>";
		}

		// a reward far above the usual share of the invoice is worth a second look
		if (column.fieldname === "reward_pct" && data.reward_pct) {
			const colour =
				data.reward_pct >= 10
					? "var(--red-500)"
					: data.reward_pct >= 5
					? "var(--orange-500)"
					: "var(--text-muted)";
			value = '<span style="color:' + colour + '">' + value + "</span>";
		}

		if (column.fieldname === "status" && data.status === "Draft") {
			value = '<span style="color:var(--orange-500)">' + value + "</span>";
		}

		return value;
	},

	onload: function (report) {
		report.page.add_inner_button(__("Show Unlinked Only"), function () {
			report.set_filter_value("only_unlinked", 1);
			report.set_filter_value("summarise_by_customer", 0);
		});
		report.page.add_inner_button(__("Customer Summary"), function () {
			report.set_filter_value("summarise_by_customer", 1);
		});
	},
};
