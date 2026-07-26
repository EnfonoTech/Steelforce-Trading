// apps/sf_trading/sf_trading/public/js/sales_invoice_list.js
// "Create Payment Advice" action on the Sales Invoice list — see payment_advice_list_action.js.
frappe.listview_settings["Sales Invoice"] = Object.assign(
	frappe.listview_settings["Sales Invoice"] || {},
	sf_payment_advice_list_action("Sales Invoice", "customer", "Customer")
);
