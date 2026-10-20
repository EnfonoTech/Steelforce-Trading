// Copyright (c) 2026, Enfono Technologies and contributors
// For license information, please see license.txt

frappe.listview_settings["Accounting Clearance"] = {
	add_fields: ["status", "pending_count", "docstatus"],
	get_indicator(doc) {
		if (doc.status === "Approved") return [__("Approved"), "green", "status,=,Approved"];
		if (doc.status === "Cancelled") return [__("Cancelled"), "red", "status,=,Cancelled"];
		if (doc.status === "Cleared") return [__("Cleared"), "blue", "status,=,Cleared"];
		return [__("Pending"), "orange", "status,=,Pending"];
	},
};
