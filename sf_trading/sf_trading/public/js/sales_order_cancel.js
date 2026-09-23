// sf_trading/public/js/sales_order_cancel.js
// GS Issue 17: a Sales Order needs a remark before it can be cancelled.
//
// This does NOT gate anything -- the actual refusal lives server-side, in
// sf_trading.sales_order_governance.before_cancel_require_remark_and_branch_head, because a
// client-side check alone is not a gate on this account: a document can be cancelled through the
// API, an import, or another screen with no browser in the loop at all (see the account's own
// trap notes). What this file buys is a decent cancel experience for the person doing it from the
// desk -- prompting for the remark before the cancel HTTP call fires, instead of the user hitting
// a raw server error with no obvious next step.
frappe.ui.form.on("Sales Order", {
	before_cancel(frm) {
		if (frm.doc.custom_cancellation_remark) {
			// Already stamped (e.g. this cancel was re-tried after a prior attempt failed for
			// some other reason) -- do not prompt twice.
			return Promise.resolve();
		}

		return new Promise((resolve, reject) => {
			frappe.prompt(
				[
					{
						fieldname: "remark",
						fieldtype: "Small Text",
						label: __("Cancellation Remark"),
						reqd: 1,
					},
				],
				(values) => {
					frappe.call({
						method: "frappe.client.set_value",
						args: {
							doctype: "Sales Order",
							name: frm.doc.name,
							fieldname: "custom_cancellation_remark",
							value: values.remark,
						},
						callback: () => {
							frm.doc.custom_cancellation_remark = values.remark;
							resolve();
						},
						error: () => reject(),
					});
				},
				__("Why is this order being cancelled?"),
				__("Continue Cancelling")
			);
			// Closing the dialog without submitting (Escape / clicking away) leaves this
			// promise unresolved rather than rejected -- frappe.prompt has no documented
			// on-cancel callback to hook here. The cancel simply does not proceed in that
			// case, which fails safe: nothing is written, the order stays submitted, and the
			// user can just try Cancel again. The server-side check is the real gate either
			// way (see the module docstring above).
		});
	},
});
