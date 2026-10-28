// sf_trading/public/js/customer_selling_context.js
// What the selling forms ask about a customer the moment it is picked -- its credit limit, how much of
// that the Credit invoices have used, any overdue credit invoice, its sales team -- fetched ONCE and shared
// by every handler (Sales Invoice, Sales Order). Before this each handler fetched what it needed by itself:
// the whole Customer document three times, a list of up to 500 invoices, the overdue check -- one after
// the other, which is most of the seconds a form spends busy after the customer is picked.
// Server side: sf_trading.api.sales_invoice_override.get_customer_selling_context.

frappe.provide("sf_trading");

(function () {
	// long enough to cover a form being filled in, short enough that a limit changed meanwhile is soon seen
	const KEEP_MS = 60 * 1000;
	const cache = {};

	// a Promise of the context, or of null when there is no customer; the same Promise while it is fresh
	sf_trading.customer_context = function (customer, company) {
		if (!customer) return Promise.resolve(null);
		company = company || frappe.defaults.get_default("company") || "";
		const key = customer + "::" + company;
		const hit = cache[key];
		if (hit && Date.now() - hit.at < KEEP_MS) return hit.promise;

		const promise = frappe
			.xcall("sf_trading.api.sales_invoice_override.get_customer_selling_context", { customer, company })
			.catch(function (error) {
				delete cache[key]; // a failed answer is never kept
				throw error;
			});
		cache[key] = { at: Date.now(), promise };
		return promise;
	};
})();
