// Copyright (c) 2026, Enfono Technologies and contributors
// For license information, please see license.txt

var SF_SOT_COLORS = {
	"Overdue": "red",
	"Overdue - Settled": "orange",
	"Overdue - Returned": "orange",
	"Converted": "blue",
	"Not Due": "green",
	"On Hold": "gray",
	"Completed": "green",
	"Closed": "gray",
};

frappe.query_reports["Sales Order Tracker"] = {
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
			fieldname: "tracking_state",
			label: __("Tracking State"),
			fieldtype: "Select",
			options: [
				"",
				"Needs Attention",
				"Overdue",
				"Overdue - Settled",
				"Overdue - Returned",
				"Converted",
				"Not Due",
				"On Hold",
				"Completed",
				"Closed",
			].join("\n"),
		},
		{ fieldname: "customer", label: __("Customer"), fieldtype: "Link", options: "Customer" },
		{ fieldname: "branch", label: __("Branch"), fieldtype: "Link", options: "Branch" },
		{
			fieldname: "payment_state",
			label: __("Payment"),
			fieldtype: "Select",
			options: ["", "Paid", "Part Paid", "Unpaid", "Advance Only", "Not Invoiced"].join("\n"),
		},
		{ fieldname: "has_return", label: __("Has A Return"), fieldtype: "Check" },
		{ fieldname: "from_date", label: __("Order Date From"), fieldtype: "Date" },
		{ fieldname: "to_date", label: __("Order Date To"), fieldtype: "Date" },
		{ fieldname: "delivery_from", label: __("Delivery Date From"), fieldtype: "Date" },
		{ fieldname: "delivery_to", label: __("Delivery Date To"), fieldtype: "Date" },
		{ fieldname: "sales_order", label: __("Sales Order"), fieldtype: "Link", options: "Sales Order" },
		{
			fieldname: "include_closed",
			label: __("Include Completed And Closed"),
			fieldtype: "Check",
			default: 0,
		},
	],

	formatter(value, row, column, data, default_formatter) {
		value = default_formatter(value, row, column, data);
		if (!data) return value;
		if (column.fieldname === "tracking_state" && SF_SOT_COLORS[data.tracking_state]) {
			const color = SF_SOT_COLORS[data.tracking_state];
			value = `<span class="indicator-pill ${color} ellipsis"><span class="ellipsis">${data.tracking_state}</span></span>`;
		}
		if (column.fieldname === "days_overdue" && data.days_overdue > 0) {
			value = `<span style="color: var(--red-600); font-weight: 600;">${data.days_overdue}</span>`;
		}
		if (column.fieldname === "outstanding_amount" && data.outstanding_amount > 0) {
			value = `<span style="color: var(--red-600);">${value}</span>`;
		}
		return value;
	},

	// a tick box per row: the orders to close are picked on the report itself
	get_datatable_options(options) {
		return Object.assign(options, { checkboxColumn: true });
	},

	onload(report) {
		report.page.add_inner_button(__("Close Selected Orders"), () => {
			const rows = (report.get_checked_items() || []).filter((r) => r && r.sales_order);
			if (!rows.length) {
				frappe.msgprint(__("Tick the orders to close first."));
				return;
			}
			const closable = rows.filter((r) => r.can_close);
			const skipped = rows.filter((r) => !r.can_close).map((r) => r.sales_order);
			if (!closable.length) {
				frappe.msgprint(__("None of the ticked orders is open, so there is nothing to close."));
				return;
			}
			frappe.prompt(
				[
					{
						fieldname: "reason",
						label: __("Why are these orders being closed?"),
						fieldtype: "Small Text",
						reqd: 1,
						description:
							__("Written to the comments of each order. Closing is reversible: Status > Re-open on the order.") +
							(skipped.length ? " " + __("Not closed (not open): {0}", [skipped.join(", ")]) : ""),
					},
				],
				(values) => {
					frappe.call({
						method: "sf_trading.sf_trading.report.sales_order_tracker.sales_order_tracker.close_orders",
						args: { names: closable.map((r) => r.sales_order), reason: values.reason },
						freeze: true,
						freeze_message: __("Closing..."),
						callback(r) {
							const out = r.message || {};
							frappe.msgprint({
								title: __("Sales Orders Closed"),
								indicator: (out.refused || []).length ? "orange" : "green",
								message:
									__("Closed: {0}", [(out.closed || []).join(", ") || "-"]) +
									((out.refused || []).length
										? "<br>" + __("Not closed: {0}", [out.refused.map((x) => `${x.name} (${x.status})`).join(", ")])
										: ""),
							});
							report.refresh();
						},
					});
				},
				__("Close {0} Sales Order(s)", [closable.length]),
				__("Close")
			);
		});
	},
};
