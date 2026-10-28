// sf_trading/public/js/sales_order_credit.js
// The Sales Invoice's credit checks, on the Sales Order: a clerk learns a credit customer cannot be
// sold to while picking them, not when the order is submitted (public/js/sales_invoice.js):
//
//   * Credit mode offers only customers that carry a credit limit; Cash / Cheque only the company's own.
//   * a customer with no credit limit is refused for Credit as soon as either is picked;
//   * an overdue credit invoice is told on picking the customer -- saving is refused by the server
//     (api/sales_invoice_override.validate);
//   * outstanding + this order over the credit limit is told as items are added, and again on save.
//     The limit itself is still enforced at submit, by core's check_credit_limit.

(function () {
	const CREDIT = "Credit";

	function apply_customer_filter(frm) {
		frm.set_query("customer", function (doc) {
			if (doc.custom_payment_mode === CREDIT) {
				return {
					query: "sf_trading.customer_permission.customer_query_credit_branch",
					filters: { branch: doc.branch || "", company: doc.company || "" },
				};
			}
			if (doc.company) return { filters: { custom_company: doc.company } };
			return {};
		});
	}

	function customer_credit_limit(customer, company) {
		// the one shared answer per customer (public/js/customer_selling_context.js)
		return sf_trading.customer_context(customer, company).then(function (ctx) {
			return flt(ctx && ctx.credit_limit);
		});
	}

	function say_no_credit_limit(frm) {
		frappe.msgprint({
			title: __("No Credit Limit"),
			message: __("Customer {0} has no credit limit set for this company. Set a credit limit or use Cash payment mode.", [frm.doc.customer]),
			indicator: "red",
		});
	}

	function check_overdue(frm) {
		if (frm.doc.custom_payment_mode !== CREDIT || !frm.doc.customer || !frm.doc.company) return;
		const customer = frm.doc.customer;
		sf_trading.customer_context(customer, frm.doc.company).then(function (ctx) {
			const inv = ctx && ctx.overdue;
			if (!inv || frm.doc.customer !== customer) return;
			frappe.msgprint({
				title: __("Overdue Credit Invoice"),
				message: __(
					"Customer {0} has an overdue credit invoice <a href='/app/sales-invoice/{1}'>{1}</a> dated {2} "
					+ "with outstanding amount {3}. "
					+ "Saving this order will be blocked until it is settled.",
					[frm.doc.customer, inv.name, inv.posting_date,
					format_currency(inv.outstanding_amount, frm.doc.currency)]
				),
				indicator: "orange",
			});
		});
	}

	// ── outstanding + this order against the credit limit ────────────────────────
	function credit_check_applies(frm) {
		return frm.doc.docstatus === 0
			&& frm.doc.custom_payment_mode === CREDIT
			&& frm.doc.customer
			&& frm.doc.company;
	}

	function fetch_credit_status(frm) {
		const key = frm.doc.customer + "::" + frm.doc.company;
		if (frm._sf_credit_status && frm._sf_credit_status.key === key) {
			return Promise.resolve(frm._sf_credit_status);
		}
		return frappe.call({
			method: "sf_trading.api.sales_invoice_override.get_credit_limit_status",
			args: { customer: frm.doc.customer, company: frm.doc.company },
		}).then(function (r) {
			const status = r.message || {};
			status.key = key;
			frm._sf_credit_status = status;
			return status;
		});
	}

	function run_credit_check(frm) {
		if (!credit_check_applies(frm)) return;
		fetch_credit_status(frm).then(function (status) {
			if (!flt(status.credit_limit) || cint(status.bypass)) return;

			const order_total = flt(frm.doc.base_grand_total || frm.doc.grand_total);
			const limit = flt(status.credit_limit);
			const projected = flt(status.outstanding) + order_total;
			const currency = status.currency;
			if (flt(projected - limit, 3) <= 0) return;

			frappe.msgprint({
				title: __("Credit Limit Exceeded"),
				indicator: "red",
				message: __(
					"Customer {0}<br>Credit Limit: <b>{1}</b><br>Current Outstanding: <b>{2}</b><br>"
					+ "This Order: <b>{3}</b><br>Exceeds limit by: <b>{4}</b>",
					[frm.doc.customer, format_currency(limit, currency),
					 format_currency(status.outstanding, currency), format_currency(order_total, currency),
					 format_currency(projected - limit, currency)]
				),
			});
		});
	}

	function schedule_credit_check(frm) {
		// let ERPNext finish recalculating totals first (the rate fetch is async)
		if (frm._sf_credit_check_timer) clearTimeout(frm._sf_credit_check_timer);
		frm._sf_credit_check_timer = setTimeout(function () { run_credit_check(frm); }, 600);
	}

	frappe.ui.form.on("Sales Order", {
		onload: function (frm) {
			apply_customer_filter(frm);
			if (frm.doc.docstatus === 0 && frm.doc.customer) check_overdue(frm);
		},
		refresh: function (frm) {
			apply_customer_filter(frm);
			if (credit_check_applies(frm) && (frm.doc.items || []).length) schedule_credit_check(frm);
		},
		custom_payment_mode: function (frm) {
			apply_customer_filter(frm);
			schedule_credit_check(frm);
			if (frm.doc.custom_payment_mode !== CREDIT || !frm.doc.customer) return;
			customer_credit_limit(frm.doc.customer, frm.doc.company).then(function (limit) {
				if (limit <= 0) {
					say_no_credit_limit(frm);
					frm.set_value("custom_payment_mode", "Cash");
					return;
				}
				check_overdue(frm);
			});
		},
		customer: function (frm) {
			frm._sf_credit_status = null;
			schedule_credit_check(frm);
			if (frm.doc.custom_payment_mode !== CREDIT || !frm.doc.customer) return;
			customer_credit_limit(frm.doc.customer, frm.doc.company).then(function (limit) {
				if (limit <= 0) {
					say_no_credit_limit(frm);
					frm.set_value("customer", "");
					return;
				}
				check_overdue(frm);
			});
		},
		discount_amount: function (frm) { schedule_credit_check(frm); },
		additional_discount_percentage: function (frm) { schedule_credit_check(frm); },
		validate: function (frm) { run_credit_check(frm); },
	});

	frappe.ui.form.on("Sales Order Item", {
		item_code: function (frm) { schedule_credit_check(frm); },
		qty: function (frm) { schedule_credit_check(frm); },
		rate: function (frm) { schedule_credit_check(frm); },
		items_remove: function (frm) { schedule_credit_check(frm); },
	});
})();
