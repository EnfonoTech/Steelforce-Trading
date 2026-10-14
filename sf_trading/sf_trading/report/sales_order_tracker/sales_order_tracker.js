// Copyright (c) 2026, Enfono Technologies and contributors
// For license information, please see license.txt

var SF_SOT_CLOSE_COLORS = {
	"Ready To Close": "green",
	"Review Before Closing": "orange",
	"Keep Open": "gray",
	"Closed": "gray",
};

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

// why an open order has no invoice yet (sf_trading/order_stock.py), coloured by who has to move:
// red = somebody raises or submits something today, orange = chase the stock, green = nothing to do yet, gray = held
var SF_SOT_WHY_COLORS = {
	"Stock Ready, Not Invoiced": "red",
	"Invoice In Draft": "red",
	"Delivery Note In Draft": "red",
	"Customer Blocked": "red",
	"Delivered, Not Billed": "red",
	"Not Invoiced Yet": "red",
	"No Stock": "orange",
	"Part Stock": "orange",
	"Not Due Yet": "green",
	"On Hold": "gray",
};

// what the warehouse holds against what is still to be delivered
var SF_SOT_STOCK_COLORS = {
	"In Stock": "green",
	"Part Stock": "orange",
	"No Stock": "red",
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
		{
			fieldname: "why_not_invoiced",
			label: __("Why Not Invoiced"),
			fieldtype: "Select",
			options: [
				"",
				"Stock Ready, Not Invoiced",
				"Invoice In Draft",
				"Delivery Note In Draft",
				"No Stock",
				"Part Stock",
				"Customer Blocked",
				"Delivered, Not Billed",
				"Not Due Yet",
				"On Hold",
				"Not Invoiced Yet",
			].join("\n"),
		},
		{
			fieldname: "needs_action",
			label: __("Needs Action Now"),
			fieldtype: "Check",
			default: 0,
		},
		{
			fieldname: "stock_check",
			label: __("Stock"),
			fieldtype: "Select",
			options: ["", "In Stock", "Part Stock", "No Stock"].join("\n"),
		},
		{
			fieldname: "view",
			label: __("View"),
			fieldtype: "Select",
			options: ["Order Wise", "Item Wise"].join("\n"),
			default: "Order Wise",
		},
		{
			fieldname: "status",
			label: __("ERPNext Status"),
			fieldtype: "Select",
			options: ["", "To Deliver and Bill", "To Bill", "To Deliver", "On Hold", "Completed", "Closed"].join("\n"),
		},
		{ fieldname: "customer", label: __("Customer"), fieldtype: "Link", options: "Customer" },
		{ fieldname: "customer_group", label: __("Customer Group"), fieldtype: "Link", options: "Customer Group" },
		{ fieldname: "territory", label: __("Territory"), fieldtype: "Link", options: "Territory" },
		{ fieldname: "branch", label: __("Branch"), fieldtype: "Link", options: "Branch" },
		{ fieldname: "cost_center", label: __("Cost Center"), fieldtype: "Link", options: "Cost Center" },
		{ fieldname: "sales_person", label: __("Sales Person"), fieldtype: "Link", options: "Sales Person" },
		{ fieldname: "item_code", label: __("Item"), fieldtype: "Link", options: "Item" },
		{ fieldname: "item_group", label: __("Item Group"), fieldtype: "Link", options: "Item Group" },
		{
			fieldname: "converted_to",
			label: __("Converted To"),
			fieldtype: "Select",
			options: ["", "Invoice", "Delivery Note", "Both", "Neither"].join("\n"),
		},
		{
			fieldname: "close_check",
			label: __("Close?"),
			fieldtype: "Select",
			options: ["", "Ready To Close", "Review Before Closing", "Keep Open", "Closed"].join("\n"),
		},
		{
			fieldname: "return_stock",
			label: __("Return Stock"),
			fieldtype: "Select",
			options: ["", "Stock Restored", "Awaiting DN Return"].join("\n"),
		},
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
		const why_color = SF_SOT_WHY_COLORS[data.why_not_invoiced];
		if (column.fieldname === "why_not_invoiced" && why_color) {
			value = `<span class="indicator-pill ${why_color} ellipsis" style="font-weight: 600;"><span class="ellipsis">${frappe.utils.escape_html(
				data.why_not_invoiced
			)}</span></span>`;
		}
		if (column.fieldname === "next_step" && why_color && data.next_step) {
			value = `<span style="color: var(--${why_color}-600); font-weight: 600;">${frappe.utils.escape_html(data.next_step)}</span>`;
		}
		if ((column.fieldname === "sales_order" || column.fieldname === "customer_name") && why_color) {
			// a bar down the left of the row's first cells, in the colour of the reason, so the rows to act on stand out
			value = `<span style="display: inline-block; border-left: 5px solid var(--${why_color}-500); padding-left: 6px;${
				data.needs_action && column.fieldname === "customer_name" ? " font-weight: 600;" : ""
			}">${value}</span>`;
		}
		if (column.fieldname === "stock_check" && SF_SOT_STOCK_COLORS[data.stock_check]) {
			const color = SF_SOT_STOCK_COLORS[data.stock_check];
			value = `<span class="indicator-pill ${color} ellipsis"><span class="ellipsis">${data.stock_check}</span></span>`;
		}
		if (column.fieldname === "line_stock" && SF_SOT_STOCK_COLORS[data.line_stock]) {
			const color = SF_SOT_STOCK_COLORS[data.line_stock];
			value = `<span class="indicator-pill ${color} ellipsis"><span class="ellipsis">${data.line_stock}</span></span>`;
		}
		if (column.fieldname === "waiting_days" && data.waiting_days > 0) {
			const late = data.needs_action && data.waiting_days >= 2;
			value = `<span style="font-weight: 600;${late ? " color: var(--red-600);" : ""}">${data.waiting_days}</span>`;
		}
		if (column.fieldname === "stock_drilldown" && data.stock_drilldown) {
			// the order item by item and warehouse by warehouse, with the orders that compete for the same stock
			const company = frappe.query_report.get_filter_value("company") || "";
			const route =
				"/app/query-report/Sales%20Order%20Stock%20Drill%20Down?company=" +
				encodeURIComponent(company) +
				"&sales_order=" +
				encodeURIComponent(data.stock_drilldown);
			value = `<a href="${route}">${__("Stock")} &rarr;</a>`;
		}
		if (column.fieldname === "close_check" && SF_SOT_CLOSE_COLORS[data.close_check]) {
			const color = SF_SOT_CLOSE_COLORS[data.close_check];
			value = `<span class="indicator-pill ${color} ellipsis"><span class="ellipsis">${data.close_check}</span></span>`;
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
		// every order the data says is ready to close: billed in full, paid, returned goods back in stock
		report.page.add_inner_button(__("Tick Ready To Close"), () => {
			const table = report.datatable;
			if (!table) return;
			let ticked = 0;
			(report.data || []).forEach((row, i) => {
				if (row && row.close_check === "Ready To Close") {
					table.rowmanager.checkRow(i, true);
					ticked += 1;
				}
			});
			frappe.show_alert({
				message: ticked ? __("{0} row(s) ticked", [ticked]) : __("No order on this page is ready to close."),
				indicator: ticked ? "green" : "orange",
			});
		});

		report.page.add_inner_button(__("Close Selected Orders"), () => {
			const rows = (report.get_checked_items() || []).filter((r) => r && r.sales_order);
			if (!rows.length) {
				frappe.msgprint(__("Tick the orders to close first."));
				return;
			}
			const closable = rows.filter((r) => r.can_close);
			const skipped = [...new Set(rows.filter((r) => !r.can_close).map((r) => r.sales_order))];
			if (!closable.length) {
				frappe.msgprint(__("None of the ticked orders is open, so there is nothing to close."));
				return;
			}
			// what the report's own data says about the ticked orders
			const unique = {};
			closable.forEach((r) => (unique[r.sales_order] = r));
			const orders = Object.values(unique);
			const not_ready = orders.filter((r) => r.close_check !== "Ready To Close");
			const warning = not_ready.length
				? `<div class="alert alert-warning">${__("{0} of the {1} ticked order(s) are not marked Ready To Close:", [
						not_ready.length,
						orders.length,
				  ])}<br>${not_ready
						.map((r) => `${frappe.utils.escape_html(r.sales_order)} - ${r.close_check}: ${frappe.utils.escape_html(r.close_reason || "")}`)
						.join("<br>")}</div>`
				: `<div class="alert alert-success">${__("All {0} ticked order(s) are Ready To Close.", [orders.length])}</div>`;
			frappe.prompt(
				[
					{ fieldtype: "HTML", options: warning },
					{
						fieldname: "confirm",
						label: __("I have checked the orders that are not marked Ready To Close"),
						fieldtype: "Check",
						hidden: not_ready.length ? 0 : 1,
						reqd: not_ready.length ? 1 : 0,
					},
					{
						fieldname: "reason",
						label: __("Why are these orders being closed?"),
						fieldtype: "Small Text",
						reqd: 1,
						default: not_ready.length ? "" : __("Billed in full, paid in full, returned goods back in stock"),
						description:
							__("Written to the comments of each order. Closing is reversible: Status > Re-open on the order.") +
							(skipped.length ? " " + __("Not closed (not open): {0}", [skipped.join(", ")]) : ""),
					},
				],
				(values) => {
					frappe.call({
						method: "sf_trading.sf_trading.report.sales_order_tracker.sales_order_tracker.close_orders",
						args: { names: [...new Set(closable.map((r) => r.sales_order))], reason: values.reason },
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
