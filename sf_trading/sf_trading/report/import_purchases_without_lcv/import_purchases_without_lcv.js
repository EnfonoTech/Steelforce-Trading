// sf_trading/report/import_purchases_without_lcv/import_purchases_without_lcv.js
frappe.query_reports["Import Purchases Without LCV"] = {
	filters: [
		{ fieldname: "company", label: __("Company"), fieldtype: "Link", options: "Company", default: frappe.defaults.get_user_default("Company") },
		{ fieldname: "from_date", label: __("From Date"), fieldtype: "Date", default: frappe.datetime.add_months(frappe.datetime.get_today(), -3) },
		{ fieldname: "to_date", label: __("To Date"), fieldtype: "Date", default: frappe.datetime.get_today() },
		{
			fieldname: "gap",
			label: __("Gap"),
			fieldtype: "Select",
			options: ["", "No Landed Cost Voucher", "Voucher Draft On Submitted", "Voucher Out Of Step"].join("\n"),
		},
	],
};
