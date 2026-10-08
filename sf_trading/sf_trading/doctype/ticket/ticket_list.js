// sf_trading/sf_trading/doctype/ticket/ticket_list.js
frappe.listview_settings["Ticket"] = {
	add_fields: ["status", "priority", "assignee"],
	get_indicator(doc) {
		const colour = {
			Open: "red",
			"In Progress": "blue",
			"On Hold": "orange",
			Resolved: "green",
			Closed: "gray",
		}[doc.status];
		return [__(doc.status), colour || "gray", "status,=," + doc.status];
	},
};
