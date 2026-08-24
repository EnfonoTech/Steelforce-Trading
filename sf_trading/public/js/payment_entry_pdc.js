// Bank a post-dated cheque from the cheque's own Payment Entry.
//
// The PDC Report can do this in bulk; this is the same action where an accountant actually
// finds themselves — on the cheque entry, having just been told by the bank that it cleared.
// The button appears only on a submitted cheque receipt (a Receive entry whose mode of payment
// carries ZATCA payment means code 20) and turns into a link once the transfer exists, so the
// form always says which state the cheque is in.
//
// The transfer, the link back to this entry and the clearance date are all server-side, in
// sf_trading/pdc_transfer.py.

frappe.ui.form.on("Payment Entry", {
	refresh(frm) {
		if (frm.doc.docstatus !== 1) return;
		if (frm.doc.payment_type !== "Receive") return;
		if (!frm.doc.mode_of_payment) return;

		frappe.call({
			method: "sf_trading.pdc_transfer.get_transfer_context",
			args: { payment_entry: frm.doc.name },
			callback(r) {
				const context = r && r.message;
				if (!context || !context.is_cheque) return;
				// the form object is reused across documents — ignore an answer that arrived
				// after the user moved on
				if (frm.doc.name !== context.payment_entry) return;

				if (context.transfer) {
					frm.dashboard.add_indicator(
						context.transfer_docstatus === 1
							? __("Cheque banked: {0}", [context.transfer])
							: __("Transfer drafted: {0}", [context.transfer]),
						context.transfer_docstatus === 1 ? "green" : "orange"
					);
					frm.add_custom_button(
						__("Open Internal Transfer"),
						() => frappe.set_route("Form", "Payment Entry", context.transfer),
						__("PDC")
					);
					return;
				}

				frm.add_custom_button(
					__("Create Internal Transfer"),
					() => sf_pdc_transfer_dialog(frm, context),
					__("PDC")
				);
			},
		});
	},
});

function sf_pdc_transfer_dialog(frm, context) {
	const d = new frappe.ui.Dialog({
		title: __("Create Internal Transfer"),
		fields: [
			{
				fieldname: "info",
				fieldtype: "HTML",
				options: `<p>${__("Moving {0} out of {1}.", [
					format_currency(context.amount, context.currency),
					frappe.utils.escape_html(context.from_account || ""),
				])}</p>`,
			},
			{
				fieldname: "to_account",
				fieldtype: "Link",
				options: "Account",
				label: __("Credited To (Bank Account)"),
				reqd: 1,
				get_query: () => ({
					filters: {
						company: context.company,
						is_group: 0,
						account_type: ["in", ["Bank", "Cash"]],
					},
				}),
			},
			{
				fieldname: "posting_date",
				fieldtype: "Date",
				label: __("Transfer Date"),
				default: context.cheque_date || frappe.datetime.get_today(),
				reqd: 1,
			},
			{
				fieldname: "submit_transfer",
				fieldtype: "Check",
				label: __("Submit the transfer"),
				default: 1,
				description: __("Leave unticked to keep the transfer as a draft for approval."),
			},
		],
		primary_action_label: __("Create"),
		primary_action(values) {
			d.hide();
			frappe.call({
				method: "sf_trading.pdc_transfer.create_internal_transfer",
				args: {
					payment_entry: frm.doc.name,
					to_account: values.to_account,
					posting_date: values.posting_date,
					submit: values.submit_transfer ? 1 : 0,
				},
				freeze: true,
				freeze_message: __("Creating internal transfer..."),
				callback(r) {
					if (!r || !r.message) return;
					frappe.show_alert(
						{ message: __("Internal Transfer {0} created", [r.message]), indicator: "green" },
						6
					);
					frm.reload_doc();
				},
			});
		},
	});
	d.show();
}
