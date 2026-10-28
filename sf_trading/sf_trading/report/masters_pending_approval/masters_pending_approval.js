// sf_trading/sf_trading/report/masters_pending_approval/masters_pending_approval.js
// The credit customers and suppliers still waiting for their approval, and what each still lacks.

frappe.query_reports["Masters Pending Approval"] = {
	filters: [
		{
			fieldname: "party_type",
			label: __("Party Type"),
			fieldtype: "Select",
			options: ["Customer and Supplier", "Customer", "Supplier"].join("\n"),
			default: "Customer and Supplier",
		},
		{
			fieldname: "status",
			label: __("Status"),
			fieldtype: "Select",
			options: ["", "Draft", "Pending Approval", "Rejected", "Approved Before the Workflow"].join("\n"),
		},
		{ fieldname: "incomplete_only", label: __("Incomplete Only"), fieldtype: "Check", default: 0 },
		{ fieldname: "include_disabled", label: __("Include Disabled"), fieldtype: "Check", default: 0 },
	],

	formatter(value, row, column, data, default_formatter) {
		value = default_formatter(value, row, column, data);
		if (!data) return value;
		if (column.fieldname === "status") {
			const colour = { Draft: "orange", "Pending Approval": "blue", Rejected: "red" }[data.status] || "gray";
			value = `<span class="indicator-pill ${colour}">${value}</span>`;
		}
		if (column.fieldname === "missing" && data.missing) {
			value = `<span style="color:var(--red-500)">${value}</span>`;
		}
		return value;
	},
};
