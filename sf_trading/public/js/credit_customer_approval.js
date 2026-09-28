// sf_trading/public/js/credit_customer_approval.js
// Shows a Bypass Approval button while a customer is not yet Approved, for whoever is
// named on SF Trading Settings' bypass roster (server re-checks it -- this button is
// only ever an offer, never the gate).
frappe.ui.form.on("Customer", {
	refresh(frm) {
		if (frm.is_new() || !frm.doc.custom_approval_status || frm.doc.custom_approval_status === "Approved") {
			return;
		}

		frappe.call({
			method: "sf_trading.credit_customer_approval.get_approval_state",
			args: { customer: frm.doc.name },
			callback(r) {
				if (!r.message || r.message.approved || !r.message.can_bypass) {
					return;
				}
				frm.add_custom_button(__("Bypass Approval"), () => {
					frappe.confirm(
						__("Make {0} available for Sales Invoice without completing verification? This is logged.", [frm.doc.name]),
						() => {
							frappe.call({
								method: "sf_trading.credit_customer_approval.bypass_customer_approval",
								args: { customer: frm.doc.name },
								callback() {
									frappe.show_alert({ message: __("Approval bypassed"), indicator: "orange" });
									frm.reload_doc();
								},
							});
						}
					);
				}).addClass("btn-warning");
			},
		});
	},
});
