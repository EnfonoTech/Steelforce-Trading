// Customer filter: Credit mode shows only customers with credit_limit > 0
function sf_apply_customer_credit_filter(frm) {
	frm.set_query("customer", function () {
		if (frm.doc.custom_payment_mode === "Credit") {
			return {
				filters: [["Customer Credit Limit", "credit_limit", ">", 0]],
			};
		}
		return {};
	});
}

function sf_get_customer_credit_limit(customer, company) {
	// credit_limits is a child table on the Customer master
	return frappe.call({
		method: "frappe.client.get",
		args: { doctype: "Customer", name: customer },
	}).then(function (r) {
		const rows = (r.message && r.message.credit_limits) || [];
		const row = rows.find(function (d) { return d.company === company; });
		return row ? flt(row.credit_limit) : 0;
	});
}

frappe.ui.form.on("Sales Invoice", {
	onload: function (frm) {
		sf_apply_customer_credit_filter(frm);
	},
	refresh: function (frm) {
		sf_apply_customer_credit_filter(frm);
	},
	custom_payment_mode: function (frm) {
		sf_apply_customer_credit_filter(frm);
		// Re-validate current customer when switching to Credit
		if (frm.doc.custom_payment_mode === "Credit" && frm.doc.customer) {
			sf_get_customer_credit_limit(frm.doc.customer, frm.doc.company).then(function (limit) {
				if (limit <= 0) {
					frappe.msgprint({
						title: __("No Credit Limit"),
						message: __("Customer {0} has no credit limit set for this company. Set a credit limit or use Cash payment mode.", [frm.doc.customer]),
						indicator: "red",
					});
					frm.set_value("customer", "");
				}
			});
		}
	},
	customer: function (frm) {
		if (frm.doc.custom_payment_mode !== "Credit" || !frm.doc.customer) return;
		sf_get_customer_credit_limit(frm.doc.customer, frm.doc.company).then(function (limit) {
			if (limit <= 0) {
				frappe.msgprint({
					title: __("No Credit Limit"),
					message: __("Customer {0} has no credit limit set for this company. Set a credit limit or use Cash payment mode.", [frm.doc.customer]),
					indicator: "red",
				});
				frm.set_value("customer", "");
			}
		});
	},
});
