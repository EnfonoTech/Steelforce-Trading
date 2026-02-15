// sf_trading: Popup to enter payment amounts when is_pos is checked
// Shows after save when the correct grand_total is available
frappe.ui.form.on("Sales Invoice", {
	refresh: function (frm) {
		// Capture save action so before_save knows if user clicked Submit (skip confirm)
		if (frm._sf_save_wrapped) return;
		frm._sf_save_wrapped = true;
		const orig = frm.save.bind(frm);
		frm.save = function (save_action, callback, btn, on_error) {
			frappe.flags._sf_save_action = save_action || "Save";
			return orig(save_action, callback, btn, on_error).finally(function () {
				delete frappe.flags._sf_save_action;
			});
		};
	},
	before_save: function (frm) {
		// When Include Payment (POS) is NOT checked, ask to submit on Save (not Submit)
		if (frm.doc.is_pos || frm.doc.docstatus !== 0) return;
		if (frappe.flags._sf_save_action === "Submit") return;
		if (frm._asked_to_submit) return;

		frm._asked_to_submit = true;
		frappe.validated = false;

		frappe.confirm(
			__("Do you want to Submit this Sales Invoice now?"),
			function () {
				frm.save("Submit").then(function () {
					frm._asked_to_submit = false;
					frm.reload_doc();
				});
			},
			function () {
				frm.save().then(function () {
					frm._asked_to_submit = false;
				});
			}
		);
	},
	after_save: function (frm) {
		if (frappe.flags.sf_trading_skip_payment_popup) return;
		if (!frm.doc.is_pos || frm.doc.docstatus !== 0) return;
		if (!frm.doc.pos_profile || !frm.doc.grand_total || frm.doc.grand_total <= 0) return;

		// Show popup on every save (unless POS Profile disables it)
		frappe.db.get_value(
			"POS Profile",
			frm.doc.pos_profile,
			"disable_grand_total_to_default_mop",
			function (r) {
				if (r && r.message === 1) return;
				sf_trading_show_pos_total_popup(frm);
			}
		);
	},
});

function sf_trading_show_pos_total_popup(frm) {
	function do_show_popup() {
		// Load payment modes from POS Profile if empty
		if (!frm.doc.payments || frm.doc.payments.length === 0) {
			frappe.call({
				method: "frappe.client.get",
				args: { doctype: "POS Profile", name: frm.doc.pos_profile },
				callback: function (r) {
					if (r.message && r.message.payments && r.message.payments.length > 0) {
						frm.clear_table("payments");
						r.message.payments.forEach(function (pay) {
							const row = frm.add_child("payments");
							row.mode_of_payment = pay.mode_of_payment;
							row.default = pay.default;
						});
						frm.refresh_field("payments");
						frappe.call({
							doc: frm.doc,
							method: "set_account_for_mode_of_payment",
							callback: function () {
								frm.refresh_field("payments");
								sf_trading_render_dialog(frm);
							},
						});
					} else {
						frappe.msgprint(__("Add payment modes in POS Profile first"));
					}
				},
			});
		} else {
			sf_trading_render_dialog(frm);
		}
	}

	do_show_popup();
}

function sf_trading_render_dialog(frm) {
	const payments = frm.doc.payments || [];
	if (payments.length === 0) return;

	const invoice_total = frm.doc.rounded_total || frm.doc.grand_total || 0;
	const currency = frm.doc.currency || "";

	const fields = [
		{
			fieldname: "invoice_total",
			fieldtype: "Currency",
			label: __("Invoice Total"),
			default: invoice_total,
			read_only: 1,
			options: currency,
		},
		{ fieldtype: "Section Break", label: __("Enter Payment Amounts") },
	];

	payments.forEach(function (payment, idx) {
		const mode = payment.mode_of_payment || "Payment " + (idx + 1);
		fields.push(
			{
				fieldtype: "Section Break",
				fieldname: "row_" + idx,
				label: "",
				hide_border: 1,
				collapsible: 0,
			},
			{
				fieldname: "pay_" + idx,
				fieldtype: "Currency",
				label: mode,
				default: payment.amount || 0,
				options: currency,
			},
			{ fieldtype: "Column Break", fieldname: "cb_" + idx },
			{
				fieldtype: "Button",
				fieldname: "fill_" + idx,
				label: mode,
				click: function () {
					payments.forEach(function (_, i) {
						d.set_value("pay_" + i, i === idx ? invoice_total : 0);
					});
				},
			}
		);
	});

	function apply_payments_and_close(vals, submit) {
		let total = 0;
		payments.forEach(function (p, i) {
			const amt = flt(vals["pay_" + i]) || 0;
			total += amt;
			p.amount = amt;
			p.base_amount = flt(amt * frm.doc.conversion_rate, precision("base_amount", p));
		});
		if (total < invoice_total) {
			frappe.msgprint({
				title: __("Incomplete"),
				message: __("{0} still to be allocated", [format_currency(invoice_total - total, currency)]),
				indicator: "red",
			});
			return;
		}
		frm.refresh_field("payments");
		if (frm.cscript && frm.cscript.calculate_taxes_and_totals) {
			frm.cscript.calculate_taxes_and_totals(true);
		}
		frm.refresh_fields();
		d.hide();
		frappe.flags.sf_trading_skip_payment_popup = true;
		const save_fn = submit ? frm.savesubmit.bind(frm) : frm.save.bind(frm);
		save_fn().finally(function () {
			setTimeout(function () {
				delete frappe.flags.sf_trading_skip_payment_popup;
			}, 500);
		});
	}

	const d = new frappe.ui.Dialog({
		title: __("Enter Payment Amounts"),
		fields: fields,
		primary_action_label: __("Save"),
		primary_action: function (vals) {
			apply_payments_and_close(vals, false);
		},
		secondary_action_label: __("Save & Submit"),
		secondary_action: function () {
			const vals = d.get_values();
			if (vals) apply_payments_and_close(vals, true);
		},
	});

	d.show();

	// Align button with input (same level) and field click handler
	frappe.utils.sleep(100).then(function () {
		// Align button with input (same level)
		d.$wrapper.find(".section-body").css({
			display: "flex",
			alignItems: "flex-end",
		});

		// Field click: fill with balance only (invoice_total - sum of others)
		payments.forEach(function (_, idx) {
			const field = d.fields_dict["pay_" + idx];
			if (!field || !field.$wrapper) return;
			const $input = field.$wrapper.find("input");
			$input.off("click.sf_fill_balance").on("click.sf_fill_balance", function () {
				let other = 0;
				payments.forEach(function (__, i) {
					if (i !== idx) other += flt(d.get_value("pay_" + i)) || 0;
				});
				d.set_value("pay_" + idx, Math.max(0, flt(invoice_total - other, 2)));
			});
		});
	});
}
