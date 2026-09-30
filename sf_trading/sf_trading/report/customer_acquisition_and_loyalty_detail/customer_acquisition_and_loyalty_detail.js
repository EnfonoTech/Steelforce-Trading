// Copyright (c) 2026, Enfono Technologies and contributors
// For license information, please see license.txt

frappe.query_reports["Customer Acquisition and Loyalty Detail"] = {
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
			fieldname: "from_date",
			label: __("From Date"),
			fieldtype: "Date",
			default: erpnext.utils.get_fiscal_year(frappe.datetime.get_today(), true)[1],
			reqd: 1,
		},
		{
			fieldname: "to_date",
			label: __("To Date"),
			fieldtype: "Date",
			default: erpnext.utils.get_fiscal_year(frappe.datetime.get_today(), true)[2],
			reqd: 1,
		},
		{
			fieldname: "view",
			label: __("View"),
			fieldtype: "Select",
			options: [
				{ value: "Invoice Wise", label: __("Invoice Wise") },
				{ value: "Customer Wise", label: __("Customer Wise") },
			],
			default: "Invoice Wise",
			reqd: 1,
		},
		{
			fieldname: "customer_type",
			label: __("Show"),
			fieldtype: "Select",
			// Invoice Wise: the new (first) or the repeat invoices. Customer Wise: "Repeat" =
			// customers with at least one repeat invoice in the period. Either way the total
			// equals the standard report's Repeat Customers column, which counts invoices.
			options: [
				{ value: "", label: __("All Customers") },
				{ value: "New", label: __("New Customers") },
				{ value: "Repeat", label: __("Repeat Customers") },
			],
		},
		{
			fieldname: "customer",
			label: __("Customer"),
			fieldtype: "Link",
			options: "Customer",
		},
		{
			fieldname: "customer_group",
			label: __("Customer Group"),
			fieldtype: "Link",
			options: "Customer Group",
		},
		{
			fieldname: "territory",
			label: __("Territory"),
			fieldtype: "Link",
			options: "Territory",
		},
		{
			fieldname: "cost_center",
			label: __("Cost Center"),
			fieldtype: "Link",
			options: "Cost Center",
			get_query: () => {
				return { filters: { company: frappe.query_report.get_filter_value("company") } };
			},
		},
		{
			fieldname: "branch",
			label: __("Branch"),
			fieldtype: "Link",
			options: "Branch",
		},
		{
			fieldname: "sales_person",
			label: __("Sales Person"),
			fieldtype: "Link",
			options: "Sales Person",
		},
	],

	formatter: function (value, row, column, data, default_formatter) {
		value = default_formatter(value, row, column, data);
		if (data && column.fieldname === "customer_status") {
			const colour = data.is_new_customer ? "var(--green-600)" : "var(--blue-600)";
			value = `<span style="color: ${colour}; font-weight: 600">${value}</span>`;
		}
		if (data && column.fieldname === "type") {
			const colour = data.is_new ? "var(--green-600)" : "var(--orange-600)";
			value = `<span style="color: ${colour}">${value}</span>`;
		}
		if (data && column.fieldname === "total" && data.total) {
			const customer = encodeURIComponent(data.customer);
			value = `<a href="#" style="color: var(--blue-600)" onclick="sfCALInvoices('${customer}'); return false;">${value}</a>`;
		}
		return value;
	},
};

window.sfCALInvoices = function (customer) {
	const f = (k) => frappe.query_report.get_filter_value(k);
	frappe.set_route("List", "Sales Invoice", {
		customer: decodeURIComponent(customer),
		company: f("company"),
		docstatus: 1,
		posting_date: ["between", [f("from_date"), f("to_date")]],
	});
};
