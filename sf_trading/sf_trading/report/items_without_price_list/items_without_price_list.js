// apps/sf_trading/sf_trading/report/items_without_price_list/items_without_price_list.js

const SF_STATUS_COLOURS = {
	"No Item Price": "#c62828",
	"Zero Rate": "#ef6c00",
	"Expired / Not Yet Valid": "#f9a825",
};

frappe.query_reports["Items Without Price List"] = {
	filters: [
		{
			fieldname: "price_type",
			label: __("Price Type"),
			fieldtype: "Select",
			options: "Selling\nBuying",
			default: "Selling",
			reqd: 1,
			on_change: function () {
				// a selling list is no answer on the buying side
				frappe.query_report.set_filter_value("price_list", "");
				frappe.query_report.refresh();
			},
		},
		{
			fieldname: "price_list",
			label: __("Price List"),
			fieldtype: "Link",
			options: "Price List",
			description: __("Blank: the branch's default list, else the site default."),
			get_query: function () {
				const buying = frappe.query_report.get_filter_value("price_type") === "Buying";
				return { filters: { enabled: 1, [buying ? "buying" : "selling"]: 1 } };
			},
		},
		{
			fieldname: "branch",
			label: __("Branch"),
			fieldtype: "Link",
			options: "Branch",
		},
		{
			fieldname: "item_group",
			label: __("Item Group"),
			fieldtype: "Link",
			options: "Item Group",
		},
		{
			fieldname: "brand",
			label: __("Brand"),
			fieldtype: "Link",
			options: "Brand",
		},
		{
			fieldname: "item_code",
			label: __("Item"),
			fieldtype: "Link",
			options: "Item",
		},
		{
			fieldname: "item_type",
			label: __("Item Type"),
			fieldtype: "Select",
			options: "Stock Items\nServices / Non-Stock\nAll Items",
			default: "Stock Items",
		},
		{
			fieldname: "status",
			label: __("Status"),
			fieldtype: "Select",
			options: "\nNo Item Price\nZero Rate\nExpired / Not Yet Valid",
		},
		{
			fieldname: "as_on_date",
			label: __("As On Date"),
			fieldtype: "Date",
			default: frappe.datetime.get_today(),
		},
		{
			fieldname: "moved_since",
			label: __("Sold / Purchased Since"),
			fieldtype: "Date",
		},
		{
			fieldname: "only_in_stock",
			label: __("Only Items in Stock"),
			fieldtype: "Check",
		},
		{
			fieldname: "no_price_anywhere",
			label: __("Not Priced in Any Price List"),
			fieldtype: "Check",
		},
		{
			fieldname: "include_disabled",
			label: __("Include Disabled Items"),
			fieldtype: "Check",
		},
	],

	formatter: function (value, row, column, data, default_formatter) {
		value = default_formatter(value, row, column, data);
		if (column.fieldname === "status" && data && SF_STATUS_COLOURS[data.status]) {
			value = `<span style="color:${SF_STATUS_COLOURS[data.status]};font-weight:600">${data.status}</span>`;
		}
		return value;
	},
};
