// Copyright (c) 2026, Enfono Technologies and contributors
// For license information, please see license.txt

(() => {
	// a Ticket Manager may look at somebody else's tickets; the server refuses it for anyone else
	const can_pick_user =
		frappe.session.user === "Administrator" ||
		frappe.user.has_role("Ticket Manager") ||
		frappe.user.has_role("System Manager");
	const colour = { Open: "red", "In Progress": "blue", "On Hold": "orange", Resolved: "green", Closed: "gray" };

	frappe.query_reports["My Tickets"] = {
		filters: [
			{
				fieldname: "status",
				label: __("Status"),
				fieldtype: "Select",
				options: ["Active", "All", "Open", "In Progress", "On Hold", "Resolved", "Closed"].join("\n"),
				default: "Active",
				description: __("Active is everything not yet closed."),
			},
			{
				fieldname: "part",
				label: __("My Part"),
				fieldtype: "Select",
				options: ["", "Raised", "Assigned", "Keep Informed", "Commented", "Mentioned"].join("\n"),
			},
			{
				fieldname: "needs_me",
				label: __("Only What Needs Me"),
				fieldtype: "Check",
				default: 0,
			},
			{
				fieldname: "from_date",
				label: __("Opened Since"),
				fieldtype: "Date",
			},
			{
				fieldname: "user",
				label: __("User"),
				fieldtype: "Link",
				options: "User",
				default: frappe.session.user,
				reqd: 1,
				hidden: can_pick_user ? 0 : 1,
			},
		],

		formatter(value, row, column, data, default_formatter) {
			const shown = default_formatter(value, row, column, data);
			if (!data) {
				return shown;
			}
			if (column.fieldname === "status" && data.status) {
				return `<span class="indicator-pill ${colour[data.status] || "gray"}">${frappe.utils.escape_html(
					__(data.status)
				)}</span>`;
			}
			if (column.fieldname === "next_step" && data.next_step) {
				return `<span class="text-danger bold">${shown}</span>`;
			}
			return shown;
		},

		onload(report) {
			report.page.add_inner_button(__("Raise a Ticket"), () => frappe.new_doc("Ticket"));
		},
	};
})();
