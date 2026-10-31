// Copyright (c) 2026, Enfono Technologies and contributors
// For license information, please see license.txt

frappe.query_reports["Assets Pending Depreciation"] = {
	filters: [
		{
			fieldname: "company",
			label: __("Company"),
			fieldtype: "Link",
			options: "Company",
			default: frappe.defaults.get_user_default("Company"),
		},
		{ fieldname: "asset_category", label: __("Asset Category"), fieldtype: "Link", options: "Asset Category" },
		{
			fieldname: "pending",
			label: __("Pending"),
			fieldtype: "Select",
			options: ["", "On Hold", "Scheduled"],
		},
		{ fieldname: "from_date", label: __("Purchase From"), fieldtype: "Date" },
		{ fieldname: "to_date", label: __("Purchase To"), fieldtype: "Date" },
	],
};
