frappe.listview_settings["Goods In"] = {
	add_fields: ["status"],
	get_indicator(doc) {
		const colour = { Draft: "red", "To Bill": "orange", Completed: "green", Cancelled: "gray" }[doc.status];
		return [__(doc.status), colour || "gray", "status,=," + doc.status];
	},
};
