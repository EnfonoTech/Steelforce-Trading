// sf_trading/sf_trading/report/pending_supporting_documents/pending_supporting_documents.js
// Accounting entries posted without their supporting document. Attach the document to the entry
// (paperclip on its form) and it leaves the list.

frappe.query_reports["Pending Supporting Documents"] = {
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
		},
		{ fieldname: "to_date", label: __("To Date"), fieldtype: "Date", default: frappe.datetime.get_today() },
		{
			fieldname: "document_type",
			label: __("Document Type"),
			fieldtype: "MultiSelectList",
			get_data: () =>
				[
					"Journal Entry",
					"Payment Entry",
					"Purchase Invoice",
					"Sales Invoice",
					"Purchase Receipt",
					"Landed Cost Voucher",
					"Expense Claim",
					"Stock Entry",
				].map((d) => ({ value: d, description: __(d) })),
		},
		{
			fieldname: "status",
			label: __("Status"),
			fieldtype: "Select",
			options: ["Pending", "Attached", "All"].join("\n"),
			default: "Pending",
		},
		{ fieldname: "branch", label: __("Branch"), fieldtype: "Link", options: "Branch" },
		{ fieldname: "posted_by", label: __("Posted By"), fieldtype: "Link", options: "User" },
		{ fieldname: "party", label: __("Party (contains)"), fieldtype: "Data" },
		{ fieldname: "min_amount", label: __("Minimum Amount"), fieldtype: "Currency" },
		{ fieldname: "min_days_pending", label: __("Pending At Least (days)"), fieldtype: "Int" },
		{
			fieldname: "view",
			label: __("View"),
			fieldtype: "Select",
			options: ["Detail", "By Document Type", "By Posted By", "By Branch", "By Party", "By Month"].join("\n"),
			default: "Detail",
		},
	],

	formatter(value, row, column, data, default_formatter) {
		value = default_formatter(value, row, column, data);
		if (!data) return value;
		if (column.fieldname === "status") {
			const colour = data.status === "Pending" ? "orange-500" : "green-600";
			value = `<span style="color:var(--${colour})"><b>${value}</b></span>`;
		}
		if (["days_pending", "oldest_days"].includes(column.fieldname)) {
			const days = data[column.fieldname] || 0;
			if (days > 30) value = `<span style="color:var(--red-500)"><b>${value}</b></span>`;
			else if (days > 7) value = `<span style="color:var(--orange-500)">${value}</span>`;
		}
		return value;
	},
};
