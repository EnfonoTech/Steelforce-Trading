// sf_trading/public/js/sales_order_cancel.js
// Cancelling a Sales Order or Purchase Order asks why, as a sales return does: a reason picked from
// the Order Cancellation Reason list, plus details where the reason needs them ("Other" always).
// sf_trading/order_cancellation.py stores both on the order; the before_cancel guards refuse a
// cancel without them, so this dialog is the convenience and the server is the gate -- an order can
// also be cancelled through the API or a script, with no browser in the loop.
//
// On a Sales Order only a Sales Manager cancels. Everyone else uses Request Cancellation, which
// records the same reason and sends the order to the Sales Manager (sf_trading/approval_routing.py).

const SF_CANCEL_OTHER = "Other";

function sf_cancellation_dialog(frm, title, primary_label, on_pick) {
	let d = null;
	d = new frappe.ui.Dialog({
		title: title,
		fields: [
			{
				fieldname: "reason",
				fieldtype: "Link",
				options: "Order Cancellation Reason",
				label: __("Cancellation Reason"),
				reqd: 1,
				default: frm.doc.custom_cancellation_reason || "",
				get_query: () => ({
					filters: {
						disabled: 0,
						applies_to: ["in", ["Sales and Purchase Orders", frm.doctype]],
					},
				}),
				onchange() {
					const reason = d.get_value("reason");
					if (!reason) {
						d.set_df_property("details", "reqd", 0);
						return;
					}
					frappe.db.get_value("Order Cancellation Reason", reason, "needs_details").then((r) => {
						const needs = reason === SF_CANCEL_OTHER || (r.message && r.message.needs_details);
						d.set_df_property("details", "reqd", needs ? 1 : 0);
					});
				},
			},
			{
				fieldname: "details",
				fieldtype: "Small Text",
				label: __("Details"),
				description: __("What happened. Required for Other and for reasons marked Needs Details."),
			},
		],
		primary_action_label: primary_label,
		primary_action(values) {
			d.hide();
			on_pick(values);
		},
	});
	d.show();
	return d;
}

function sf_prompt_cancellation_remark(frm, title) {
	// Always asked, prefilled with any reason already on the order: a stamp left by an earlier cancel
	// that failed must not be reused unseen. (Approving a request cancels on the server, with the
	// requester's reason, and never comes through here.)
	return new Promise((resolve, reject) => {
		sf_cancellation_dialog(frm, title, __("Continue Cancelling"), (values) => {
			frappe.call({
				method: "sf_trading.order_cancellation.set_cancellation_reason",
				args: { doctype: frm.doctype, name: frm.doc.name, reason: values.reason, details: values.details },
				callback(r) {
					frm.doc.custom_cancellation_reason = values.reason;
					frm.doc.custom_cancellation_remark = r.message;
					resolve();
				},
				error: () => reject(),
			});
		});
		// Closing the dialog leaves the promise unresolved: the cancel simply does not go ahead, and
		// nothing has been written.
	});
}

function sf_request_cancellation(frm) {
	sf_cancellation_dialog(frm, __("Request Cancellation"), __("Send to Sales Manager"), (values) => {
		frappe.call({
			method: "sf_trading.order_cancellation.request_sales_order_cancellation",
			args: { sales_order: frm.doc.name, reason: values.reason, details: values.details },
			freeze: true,
			freeze_message: __("Sending the request..."),
			callback() {
				frappe.show_alert({ message: __("Cancellation requested"), indicator: "orange" }, 5);
				frm.reload_doc();
			},
		});
	});
}

frappe.ui.form.on("Sales Order", {
	refresh(frm) {
		if (frm.doc.docstatus !== 1 || frm.doc.workflow_state === "Cancellation Requested") return;
		if (["Closed", "Completed"].includes(frm.doc.status)) return;
		frappe
			.xcall("sf_trading.order_cancellation.can_request_cancellation", { sales_order: frm.doc.name })
			.then((allowed) => {
				if (allowed && frm.doc.docstatus === 1) {
					frm.add_custom_button(__("Request Cancellation"), () => sf_request_cancellation(frm), __("Actions"));
				}
			});
	},

	before_cancel(frm) {
		// Only a Sales Manager cancels an order (sales_order_governance.CANCEL_APPROVER_ROLES);
		// anyone else asks, and the Sales Manager's approval does the cancelling.
		if (!frappe.user.has_role(["Sales Manager", "System Manager"])) {
			frappe.msgprint({
				title: __("Approval Required"),
				indicator: "orange",
				message: __("A Sales Manager approves order cancellations. Use Actions > Request Cancellation and pick the reason."),
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
