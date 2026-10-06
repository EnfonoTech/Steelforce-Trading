// Copyright (c) 2026, Enfono Technologies and contributors
// For license information, please see license.txt

frappe.query_reports["Supplier Validation Gaps"] = {
	filters: [
		{
			fieldname: "only_incomplete",
			label: __("Only Incomplete Suppliers"),
			fieldtype: "Check",
			default: 1,
		},
		{
			fieldname: "include_disabled",
			label: __("Include Disabled Suppliers"),
			fieldtype: "Check",
			default: 0,
		},
	],
};
