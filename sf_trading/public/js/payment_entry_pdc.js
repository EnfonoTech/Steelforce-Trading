// Bank or return a post-dated cheque from the cheque's own Payment Entry.
//
// The PDC Report can bank cheques in bulk; this is the same work where an accountant actually
// finds themselves -- on the cheque entry, having just heard from the bank. It works on a
// submitted cheque received from a customer (Receive) or issued to a supplier (Pay), where the
// mode of payment carries ZATCA payment means code 20:
//
//   * Bank Cheque -- an Internal Transfer for all that is left on the cheque or for part of it;
//     a cheque can be banked in several parts.
//   * Return Cheque (Bounced) -- a Journal Entry reversing whatever is still held, so the
//     customer owes it again (or we owe the supplier again).
//
// The dashboard always says where the cheque stands: banked, returned and still waiting. All of
// it is server-side, in sf_trading/pdc_transfer.py.

const SF_PDC_COLOURS = {
	Pending: "orange",
	"Partly Cleared": "blue",
	Cleared: "green",
	Returned: "red",
	"Partly Returned": "red",
};

frappe.ui.form.on("Payment Entry", {
	setup(frm) {
		// Building the transfer by hand: only a submitted cheque that still has something to
		// bank is worth offering, and the server checks the same thing on save.
		frm.set_query("custom_pdc_source_payment_entry", function () {
			return {
				query: "sf_trading.pdc_transfer.cheques_awaiting_transfer",
				filters: { company: frm.doc.company },
			};
		});
	},

	custom_pdc_source_payment_entry(frm) {
		const cheque = frm.doc.custom_pdc_source_payment_entry;
		if (!cheque || frm.doc.payment_type !== "Internal Transfer") return;
		frappe.call({
			method: "sf_trading.pdc_transfer.get_transfer_context",
			args: { payment_entry: cheque },
			callback(r) {
				const context = r && r.message;
				if (!context || !context.is_cheque) return;
				// fill the transfer from the cheque, the way the button would have
				if (context.direction === "received") {
					frm.set_value("paid_from", context.holding_account);
				} else {
					frm.set_value("paid_to", context.holding_account);
				}
				frm.set_value("paid_amount", context.remaining);
				frm.set_value("received_amount", context.remaining);
				if (context.cheque_no) frm.set_value("reference_no", context.cheque_no);
				if (context.cheque_date) frm.set_value("reference_date", context.cheque_date);
				frappe.show_alert(
					{ message: __("Filled from cheque {0}.", [cheque]), indicator: "blue" },
					4
				);
			},
		});
	},

	refresh(frm) {
		if (frm.doc.docstatus !== 1) return;
		if (!["Receive", "Pay"].includes(frm.doc.payment_type)) return;
		if (!frm.doc.mode_of_payment) return;

		frappe.call({
			method: "sf_trading.pdc_transfer.get_transfer_context",
			args: { payment_entry: frm.doc.name },
			callback(r) {
				const context = r && r.message;
				if (!context || !context.is_cheque) return;
				// the form object is reused across documents -- ignore an answer that arrived
				// after the user moved on
				if (frm.doc.name !== context.payment_entry) return;
				sf_pdc_paint(frm, context);
			},
		});
	},
});

function sf_pdc_paint(frm, context) {
	const money = (value) => format_currency(value || 0, context.currency);
	frm.dashboard.add_indicator(
		__("PDC {0}", [__(context.status)]),
		SF_PDC_COLOURS[context.status] || "gray"
	);
	if (flt(context.cleared) > 0) {
		frm.dashboard.add_indicator(__("Banked {0}", [money(context.cleared)]), "green");
	}
	if (flt(context.returned) > 0) {
		frm.dashboard.add_indicator(__("Returned {0}", [money(context.returned)]), "red");
	}
	if (flt(context.remaining) > 0 && flt(context.remaining) !== flt(context.amount)) {
		frm.dashboard.add_indicator(__("Still held {0}", [money(context.remaining)]), "orange");
	}

	const group = __("PDC");
	(context.transfers || []).forEach((t) => {
		frm.add_custom_button(
			__("Transfer {0} ({1}){2}", [t.name, money(t.amount), t.docstatus ? "" : " - " + __("Draft")]),
			() => frappe.set_route("Form", "Payment Entry", t.name),
			group
		);
	});
	(context.returns || []).forEach((j) => {
		frm.add_custom_button(
			__("Return {0} ({1}){2}", [j.name, money(j.amount), j.docstatus ? "" : " - " + __("Draft")]),
			() => frappe.set_route("Form", "Journal Entry", j.name),
			group
		);
	});

	if ((context.drafts || []).length) {
		frm.dashboard.add_comment(
			__("{0} is still a draft. Submit or delete it before banking or returning more of this cheque.", [
				context.drafts.join(", "),
			]),
			"orange",
			true
		);
		return;
	}
	if (flt(context.remaining) <= 0) return;

	frm.add_custom_button(__("Bank Cheque (Clear)"), () => sf_pdc_transfer_dialog(frm, context), group);
	frm.add_custom_button(__("Return Cheque (Bounced)"), () => sf_pdc_return_dialog(frm, context), group);
}

function sf_pdc_transfer_dialog(frm, context) {
	const received = context.direction === "received";
	const d = new frappe.ui.Dialog({
		title: __("Bank Cheque {0}", [context.cheque_no || frm.doc.name]),
		fields: [
			{
				fieldname: "info",
				fieldtype: "HTML",
				options: `<p>${
					received
						? __("{0} is still held in {1}. Bank all of it, or the part the bank credited.", [
								format_currency(context.remaining, context.currency),
								frappe.utils.escape_html(context.holding_account || ""),
						  ])
						: __("{0} is still held in {1}. Bank all of it, or the part the bank paid.", [
								format_currency(context.remaining, context.currency),
								frappe.utils.escape_html(context.holding_account || ""),
						  ])
				}</p>`,
			},
			{
				fieldname: "to_account",
				fieldtype: "Link",
				options: "Account",
				label: received ? __("Credited To (Bank Account)") : __("Paid From (Bank Account)"),
				reqd: 1,
				get_query: () => ({
					filters: {
						company: context.company,
						is_group: 0,
						account_type: ["in", ["Bank", "Cash"]],
						name: ["!=", context.holding_account],
					},
				}),
			},
			{
				fieldname: "amount",
				fieldtype: "Currency",
				label: __("Amount"),
				options: "currency",
				default: context.remaining,
				reqd: 1,
				description: __("Up to {0}. Less than that banks part of the cheque; the rest stays held.", [
					format_currency(context.remaining, context.currency),
				]),
			},
			{ fieldname: "currency", fieldtype: "Link", options: "Currency", hidden: 1, default: context.currency },
			{
				fieldname: "posting_date",
				fieldtype: "Date",
				label: __("Transfer Date"),
				default: frappe.datetime.get_today(),
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
		primary_action_label: __("Bank"),
		primary_action(values) {
			if (flt(values.amount) <= 0 || flt(values.amount) > flt(context.remaining)) {
				frappe.msgprint(__("Enter an amount up to {0}.", [format_currency(context.remaining, context.currency)]));
				return;
			}
			d.hide();
			frappe.call({
				method: "sf_trading.pdc_transfer.create_internal_transfer",
				args: {
					payment_entry: frm.doc.name,
					to_account: values.to_account,
					posting_date: values.posting_date,
					submit: values.submit_transfer ? 1 : 0,
					amount: values.amount,
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

function sf_pdc_return_dialog(frm, context) {
	const received = context.direction === "received";
	const d = new frappe.ui.Dialog({
		title: __("Return Cheque {0}", [context.cheque_no || frm.doc.name]),
		fields: [
			{
				fieldname: "info",
				fieldtype: "HTML",
				options: `<p>${
					received
						? __("Reverses the {0} still held: {1} owes it again, on the invoices this cheque paid.", [
								format_currency(context.remaining, context.currency),
								frappe.utils.escape_html(frm.doc.party_name || frm.doc.party || ""),
						  ])
						: __("Reverses the {0} still held: we owe {1} again, on the bills this cheque paid.", [
								format_currency(context.remaining, context.currency),
								frappe.utils.escape_html(frm.doc.party_name || frm.doc.party || ""),
						  ])
				}${
					flt(context.cleared) > 0
						? "<br>" + __("The {0} already banked stays banked.", [format_currency(context.cleared, context.currency)])
						: ""
				}</p>`,
			},
			{
				fieldname: "return_date",
				fieldtype: "Date",
				label: __("Returned On"),
				default: frappe.datetime.get_today(),
				reqd: 1,
			},
			{
				fieldname: "reason",
				fieldtype: "Small Text",
				label: __("Reason"),
				reqd: 1,
				description: __("For example: insufficient funds, signature mismatch, account closed."),
			},
		],
		primary_action_label: __("Return Cheque"),
		primary_action(values) {
			d.hide();
			frappe.call({
				method: "sf_trading.pdc_transfer.return_pdc",
				args: { payment_entry: frm.doc.name, return_date: values.return_date, reason: values.reason },
				freeze: true,
				freeze_message: __("Posting the return..."),
				callback(r) {
					if (!r || !r.message) return;
					frappe.show_alert(
						{ message: __("Cheque returned: {0}", [r.message.journal_entry]), indicator: "red" },
						6
					);
					frm.reload_doc();
				},
			});
		},
	});
	d.show();
}
