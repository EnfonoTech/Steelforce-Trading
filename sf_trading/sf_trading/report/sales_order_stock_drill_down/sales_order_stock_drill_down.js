// Copyright (c) 2026, Enfono Technologies and contributors
// For license information, please see license.txt

// what the stock says about an item, a warehouse or an order
var SF_SOSD_COLORS = {
	"Enough": "green",
	"Covered": "green",
	"Short": "orange",
	"Part": "orange",
	"No Stock": "red",
	"Not Covered": "red",
};

// why an open order has no invoice yet (the tracker's own words), coloured by who has to move:
// red = somebody raises or submits something today, orange = chase the stock, green = nothing to do yet, gray = held
var SF_SOSD_WHY_COLORS = {
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

frappe.query_reports["Sales Order Stock Drill Down"] = {
	filters: [
		{
			fieldname: "company",
			label: __("Company"),
			fieldtype: "Link",
			options: "Company",
			default: frappe.defaults.get_user_default("Company"),
			reqd: 1,
		},
		{ fieldname: "item_code", label: __("Item"), fieldtype: "Link", options: "Item" },
		{ fieldname: "item_group", label: __("Item Group"), fieldtype: "Link", options: "Item Group" },
		{
			fieldname: "warehouse",
			label: __("Warehouse"),
			fieldtype: "Link",
			options: "Warehouse",
			get_query: () => ({
				filters: { company: frappe.query_report.get_filter_value("company"), is_group: 0 },
			}),
		},
		{ fieldname: "sales_order", label: __("Sales Order"), fieldtype: "Link", options: "Sales Order" },
		{ fieldname: "customer", label: __("Customer"), fieldtype: "Link", options: "Customer" },
		{ fieldname: "branch", label: __("Branch"), fieldtype: "Link", options: "Branch" },
		{
			fieldname: "stock_status",
			label: __("Stock Says"),
			fieldtype: "Select",
			options: ["", "Enough", "Short", "No Stock"].join("\n"),
		},
		{
			fieldname: "only_not_invoiced",
			label: __("Only Orders Not Invoiced"),
			fieldtype: "Check",
			default: 1,
		},
		{ fieldname: "from_date", label: __("Order Date From"), fieldtype: "Date" },
		{ fieldname: "to_date", label: __("Order Date To"), fieldtype: "Date" },
	],

	// Item > Warehouse > Order, all open: every order is seen under its warehouse (collapse from the footer)
	tree: true,
	name_field: "node",
	parent_field: "parent_node",
	initial_depth: 3,

	formatter(value, row, column, data, default_formatter) {
		value = default_formatter(value, row, column, data);
		if (!data) return value;
		if (column.fieldname === "label") {
			if (data.kind === "Order" && data.sales_order) {
				const so = frappe.utils.escape_html(data.sales_order);
				value = `<a href="/app/sales-order/${encodeURIComponent(data.sales_order)}">${so}</a>`;
			} else if (data.kind === "Item") {
				value = `<b>${value}</b>`;
			}
		}
		if (column.fieldname === "stock_status" && SF_SOSD_COLORS[data.stock_status]) {
			const color = SF_SOSD_COLORS[data.stock_status];
			value = `<span class="indicator-pill ${color} ellipsis"><span class="ellipsis">${data.stock_status}</span></span>`;
		}
		const why_color = SF_SOSD_WHY_COLORS[data.why_not_invoiced];
		if (column.fieldname === "why_not_invoiced" && why_color) {
			value = `<span class="indicator-pill ${why_color} ellipsis" style="font-weight: 600;"><span class="ellipsis">${frappe.utils.escape_html(
				data.why_not_invoiced
			)}</span></span>`;
		}
		if (column.fieldname === "next_step" && why_color && data.next_step) {
			value = `<span style="color: var(--${why_color}-600); font-weight: 600;">${frappe.utils.escape_html(data.next_step)}</span>`;
		}
		if (column.fieldname === "warehouse" && data.warehouse && data.kind !== "Item") {
			value = `<b>${value}</b>`;
		}
		if (column.fieldname === "free_qty" && data.free_qty < 0) {
			value = `<span style="color: var(--red-600);">${value}</span>`;
		}
		if (column.fieldname === "stock_after" && data.kind === "Order" && data.stock_after < 0) {
			value = `<span style="color: var(--red-600);">${value}</span>`;
		}
		return value;
	},
};
