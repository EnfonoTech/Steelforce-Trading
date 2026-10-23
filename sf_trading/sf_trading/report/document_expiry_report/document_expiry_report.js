// Copyright (c) 2026, Enfono Technologies and contributors
// For license information, please see license.txt

frappe.query_reports["Document Expiry Report"] = {
	filters: [
		{
			fieldname: "period",
			label: __("Period"),
			fieldtype: "Select",
			options: ["", "Daily", "Weekly", "Monthly", "Date Range"].join("\n"),
			description: __("Daily: expiring today. Weekly: today and the next 7 days. Monthly: today and the next 30 days."),
		},
		{ fieldname: "from_date", label: __("From Date"), fieldtype: "Date", depends_on: "eval:doc.period=='Date Range'" },
		{ fieldname: "to_date", label: __("To Date"), fieldtype: "Date", depends_on: "eval:doc.period=='Date Range'" },
		{
			fieldname: "party_type",
			label: __("Master"),
			fieldtype: "Select",
			options: ["", "Customer", "Supplier"].join("\n"),
			on_change() {
				// the party filter is named after the master it searches ("Customer" / "Supplier")
				const master = frappe.query_report.get_filter_value("party_type");
				const party = frappe.query_report.get_filter("party");
				party.df.label = party.df.placeholder = master ? __(master) : __("Party");
				party.$input && party.$input.attr("placeholder", party.df.label);
				// a party picked for the other master no longer applies; an on_change of its own means the
				// report is not refreshed for us
				frappe.query_report.set_filter_value("party", "");
				frappe.query_report.refresh();
			},
		},
		{
			fieldname: "party",
			label: __("Party"),
			fieldtype: "Dynamic Link",
			options: "party_type",
			// shown once a Master is picked: a party is always a Customer or a Supplier
			depends_on: "eval:doc.party_type",
			get_options() {
				return frappe.query_report.get_filter_value("party_type");
			},
		},
		{ fieldname: "document_type", label: __("Document Type"), fieldtype: "Link", options: "Supporting Document Type" },
		{
			fieldname: "status",
			label: __("Status"),
			fieldtype: "Select",
			options: ["", "Valid", "Expiring Soon", "In Grace Period", "Expired"].join("\n"),
		},
	],
	formatter(value, row, column, data, default_formatter) {
		value = default_formatter(value, row, column, data);
		if (column.fieldname === "status" && data) {
			const colour = { [__("Expired")]: "red", [__("In Grace Period")]: "orange", [__("Expiring Soon")]: "yellow", [__("Valid")]: "green" }[data.status];
			if (colour) value = `<span class="indicator-pill ${colour}">${value}</span>`;
		}
		return value;
	},
};
