// sf_trading/public/js/sales_order_cancel.js
// GS Issue 17: a Sales Order needs a remark before it can be cancelled -- and so does a Purchase
// Order, the same rule for buying (sf_trading/purchase_order_cancel.py).
//
// This does NOT gate anything -- the actual refusal lives server-side, in each doctype's
// before_cancel hook, because a client-side check alone is not a gate on this account: a document
// can be cancelled through the API, an import, or another screen with no browser in the loop at
// all (see the account's own trap notes). What this file buys is a decent cancel experience for
// the person doing it from the desk -- prompting for the remark before the cancel HTTP call fires,
// instead of the user hitting a raw server error with no obvious next step.

function sf_prompt_cancellation_remark(frm, title) {
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
						doctype: frm.doctype,
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
			title,
			__("Continue Cancelling")
		);
		// Closing the dialog without submitting (Escape / clicking away) leaves this
		// promise unresolved rather than rejected -- frappe.prompt has no documented
		// on-cancel callback to hook here. The cancel simply does not proceed in that
		// case, which fails safe: nothing is written, the document stays submitted, and the
		// user can just try Cancel again. The server-side check is the real gate either
		// way (see the module comment above).
	});
}

frappe.ui.form.on("Sales Order", {
	before_cancel(frm) {
		// Only a Sales Manager cancels an order (sales_order_governance.CANCEL_APPROVER_ROLES);
		// anyone else asks, and the Sales Manager's approval does the cancelling.
		if (!frappe.user.has_role(["Sales Manager", "System Manager"])) {
			frappe.msgprint({
				title: __("Approval Required"),
				indicator: "orange",
				message: __("A Sales Manager approves order cancellations. Use Actions > Request Cancellation and give the reason."),
			});
			// the form's own cancel flow stops cleanly when a before_cancel handler clears this
			frappe.validated = false;
			return Promise.resolve();
		}
		return sf_prompt_cancellation_remark(frm, __("Why is this order being cancelled?"));
	},
});

frappe.ui.form.on("Purchase Order", {
	before_cancel(frm) {
		return sf_prompt_cancellation_remark(frm, __("Why is this purchase order being cancelled?"));
	},
});
