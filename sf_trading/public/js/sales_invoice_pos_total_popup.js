// sf_trading: Popup to enter payment amounts for Sales Invoice
// Shows after save when the correct grand_total is available
frappe.ui.form.on("Sales Invoice", {
	refresh: function (frm) {
		// Remove "Permanently Submit?" confirmation when clicking Submit
		if (!frm._sf_savesubmit_wrapped) {
			frm._sf_savesubmit_wrapped = true;
			const orig_savesubmit = frm.savesubmit.bind(frm);
			frm.savesubmit = function (btn, callback, on_error) {
				var me = this;
				// Submit without confirm: either show payment popup first or run submit directly
				function do_submit_without_confirm() {
					return new Promise(function (resolve) {
						me.validate_form_action("Submit");
						frappe.validated = true;
						me.script_manager.trigger("before_submit").then(function () {
							if (!frappe.validated) return me.handle_save_fail(btn, on_error);
							me.save(
								"Submit",
								function (r) {
									if (r.exc) me.handle_save_fail(btn, on_error);
									else {
										frappe.utils.play_sound("submit");
										callback && callback();
										me.script_manager.trigger("on_submit").then(function () {
											resolve(me);
										}).then(function () {
											if (frappe.route_hooks.after_submit) {
												var cb = frappe.route_hooks.after_submit;
												delete frappe.route_hooks.after_submit;
												cb(me);
											}
										});
									}
								},
								btn,
								function () { me.handle_save_fail(btn, on_error); resolve(); }
							);
						});
					});
				}
				// Show payment popup before submit when applicable (no confirm)
				if (
					!frappe.flags.sf_trading_skip_payment_popup &&
					frm.doc.docstatus === 0 &&
					frm.doc.name && !String(frm.doc.name).startsWith("new-") &&
					frm.doc.custom_payment_mode !== "Credit" &&
					frm.doc.grand_total > 0
				) {
					if (frm.doc.pos_profile) {
						return frappe.db.get_value(
							"POS Profile",
							frm.doc.pos_profile,
							"disable_grand_total_to_default_mop"
						).then(function (r) {
							if (r && r.message === 1) return do_submit_without_confirm();
							sf_trading_show_pos_total_popup(frm);
							return Promise.resolve();
						});
					}
					sf_trading_show_pos_total_popup(frm);
					return Promise.resolve();
				}
				return do_submit_without_confirm();
			};
		}

		// Capture save action so before_save knows if user clicked Submit (skip confirm)
		if (frm._sf_save_wrapped) return;
		frm._sf_save_wrapped = true;
		const orig = frm.save.bind(frm);
		frm.save = function (save_action, callback, btn, on_error) {
			frappe.flags._sf_save_action = save_action || "Save";
			// Show payment popup BEFORE submit (no confirmation); skip when submitting from popup
			if (
				save_action === "Submit" &&
				!frappe.flags.sf_trading_skip_payment_popup &&
				frm.doc.docstatus === 0 &&
				frm.doc.name && !String(frm.doc.name).startsWith("new-") &&
				frm.doc.custom_payment_mode !== "Credit" &&
				frm.doc.grand_total > 0
			) {
				function show_popup() {
					sf_trading_show_pos_total_popup(frm);
					return Promise.resolve();
				}
				if (frm.doc.pos_profile) {
					return frappe.db.get_value(
						"POS Profile",
						frm.doc.pos_profile,
						"disable_grand_total_to_default_mop"
					).then(function (r) {
						if (r && r.message === 1) return orig(save_action, callback, btn, on_error);
						return show_popup();
					});
				}
				return show_popup();
			}
			return orig(save_action, callback, btn, on_error).finally(function () {
				delete frappe.flags._sf_save_action;
			});
		};
	},
	before_save: function (frm) {
		// Only show "Do you want to Submit?" for Credit sales
		if (!frm.doc.custom_payment_mode || frm.doc.custom_payment_mode !== "Credit") return;
		if (frm.doc.docstatus !== 0) return;
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
		// Prevent popup if flag is set (we're saving from popup)
		if (frappe.flags.sf_trading_skip_payment_popup) return;
		
		// Prevent popup if already showing
		if (frappe.flags.sf_trading_popup_showing) return;

		// Credit: no payment popup (only "Do you want to Submit?" in before_save)
		if (frm.doc.custom_payment_mode && frm.doc.custom_payment_mode === "Credit") {
			return;
		}

		// Non-credit: show payment popup after save
		if (!frm.doc.grand_total || frm.doc.grand_total <= 0) return;
		if (!frm.doc.name || frm.doc.name.startsWith("new-")) return;

		if (frm.doc.pos_profile) {
			frappe.db.get_value(
				"POS Profile",
				frm.doc.pos_profile,
				"disable_grand_total_to_default_mop",
				function (r) {
					if (r && r.message === 1) return;
					sf_trading_show_pos_total_popup(frm);
				}
			);
		} else {
			sf_trading_show_pos_total_popup(frm);
		}
	},
});

function sf_trading_show_pos_total_popup(frm) {
	if (frappe.flags.sf_trading_popup_showing) return;
	if (!frm || !frm.doc) return;

	frappe.flags.sf_trading_popup_showing = true;

	function ensure_payments_then_show() {
		if (frm.doc.payments && frm.doc.payments.length > 0) {
			sf_trading_render_dialog(frm);
			return;
		}
		if (frm.doc.pos_profile) {
			frappe.call({
				method: "frappe.client.get",
				args: { doctype: "POS Profile", name: frm.doc.pos_profile },
				callback: function (r) {
					if (r.message && r.message.payments && r.message.payments.length > 0) {
						const profile_payments = r.message.payments;
						const mode_list = profile_payments.map(function (p) { return p.mode_of_payment; });
						const default_by_mode = {};
						profile_payments.forEach(function (p) { default_by_mode[p.mode_of_payment] = p.default; });
						frappe.call({
							method: "sf_trading.api.sales_invoice_payment.get_payment_modes_with_account",
							args: { company: frm.doc.company, mode_list: mode_list },
							callback: function (res) {
								const valid_modes = res.message || [];
								if (valid_modes.length === 0) {
									frappe.flags.sf_trading_popup_showing = false;
									frappe.msgprint(__("No enabled payment modes with a default account for this company. Please set default Cash or Bank account in Mode of Payment or enable the mode."));
									return;
								}
								frm.clear_table("payments");
								valid_modes.forEach(function (mode) {
									const row = frm.add_child("payments");
									row.mode_of_payment = mode;
									row.default = default_by_mode[mode] || 0;
								});
								frm.refresh_field("payments");
								frappe.call({
									doc: frm.doc,
									method: "set_account_for_mode_of_payment",
									callback: function () {
										frm.refresh_field("payments");
										sf_trading_render_dialog(frm);
									},
									error: function () {
										frappe.flags.sf_trading_popup_showing = false;
										frappe.msgprint(__("Error loading payment accounts. Please try again."));
									}
								});
							},
							error: function () {
								frappe.flags.sf_trading_popup_showing = false;
								frappe.msgprint(__("Error loading payment modes. Please try again."));
							}
						});
					} else {
						frappe.flags.sf_trading_popup_showing = false;
						frappe.msgprint(__("Add payment modes in POS Profile first"));
					}
				},
				error: function () {
					frappe.flags.sf_trading_popup_showing = false;
					frappe.msgprint(__("Error loading POS Profile. Please try again."));
				}
			});
		} else {
			// Non-credit without POS Profile: only enabled modes with default account
			frappe.call({
				method: "sf_trading.api.sales_invoice_payment.get_payment_modes_with_account",
				args: { company: frm.doc.company },
				callback: function (r) {
					const modes = r.message || [];
					if (modes.length === 0) {
						frappe.flags.sf_trading_popup_showing = false;
						frappe.msgprint(__("No enabled Mode of Payment with default Cash or Bank account for this company. Please set default account in Mode of Payment."));
						return;
					}
					frm.clear_table("payments");
					modes.forEach(function (name) {
						const row = frm.add_child("payments");
						row.mode_of_payment = name;
					});
					frm.refresh_field("payments");
					frappe.call({
						doc: frm.doc,
						method: "set_account_for_mode_of_payment",
						callback: function () {
							frm.refresh_field("payments");
							sf_trading_render_dialog(frm);
						},
						error: function () {
							frappe.flags.sf_trading_popup_showing = false;
							frappe.msgprint(__("Error loading payment accounts. Please try again."));
						}
					});
				},
				error: function () {
					frappe.flags.sf_trading_popup_showing = false;
					frappe.msgprint(__("Error loading payment modes. Please try again."));
				}
			});
		}
	}

	ensure_payments_then_show();
}

function sf_trading_render_dialog(frm) {
	// Validate form state
	if (!frm || !frm.doc) {
		frappe.flags.sf_trading_popup_showing = false;
		return;
	}
	
	const payments = frm.doc.payments || [];
	if (payments.length === 0) {
		frappe.flags.sf_trading_popup_showing = false;
		return;
	}

	const invoice_total = flt(frm.doc.rounded_total || frm.doc.grand_total || 0);
	const currency = frm.doc.currency || "";
	
	// Validate invoice total
	if (invoice_total <= 0) {
		frappe.flags.sf_trading_popup_showing = false;
		frappe.msgprint(__("Invoice total must be greater than zero."));
		return;
	}

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
		// Validate inputs
		if (!vals) {
			frappe.msgprint({
				title: __("Error"),
				message: __("Please enter payment amounts."),
				indicator: "red",
			});
			return;
		}
		
		// Collect entered payments
		let total = 0;
		const payments_payload = [];
		payments.forEach(function (p, i) {
			const amt = flt(vals["pay_" + i]) || 0;
			if (amt > 0) {
				payments_payload.push({
					mode_of_payment: p.mode_of_payment,
					amount: amt,
				});
				total += amt;
			}
		});

		if (!payments_payload.length) {
			frappe.msgprint({
				title: __("Error"),
				message: __("Please enter at least one payment amount."),
				indicator: "red",
			});
			return;
		}

		// Prevent over-payment: do not allow total entered payments to exceed invoice total
		if (total - invoice_total > 0.5) {
			frappe.msgprint({
				title: __("Error"),
				message: __(
					"Total payment amount {0} cannot be greater than invoice total {1}.",
					[format_currency(total, currency), format_currency(invoice_total, currency)]
				),
				indicator: "red",
			});
			return;
		}

		// Optional: ensure we are not under-allocating
		if (total < invoice_total) {
			frappe.msgprint({
				title: __("Incomplete"),
				message: __("{0} still to be allocated", [
					format_currency(invoice_total - total, currency),
				]),
				indicator: "red",
			});
			return;
		}

		// Close dialog before creating Payment Entries
		d.hide();
		frappe.flags.sf_trading_popup_showing = false;

		// Helper to call backend and create Payment Entries
		const create_payments = () => {
			frappe.call({
				method: "sf_trading.api.sales_invoice_payment.create_pos_payments_for_invoice",
				args: {
					sales_invoice: frm.doc.name,
					payments: JSON.stringify(payments_payload),
				},
				freeze: true,
				freeze_message: __("Creating payments..."),
				callback: function (r) {
					if (r && r.message && r.message.length) {
						frappe.show_alert(
							{
								message: __(
									"Created {0} Payment Entries for this invoice",
									[r.message.length]
								),
								indicator: "green",
							},
							5
						);
						frm.reload_doc();
					}
				},
				error: function (err) {
					console.error("sf_trading: error creating Payment Entries", err);
					frappe.msgprint({
						title: __("Error"),
						message: __("Could not create Payment Entries. Please try again."),
						indicator: "red",
					});
				},
			});
		};

		// Ensure invoice is submitted before creating payments if user chose Save & Submit
		if (submit && frm.doc.docstatus === 0) {
			frappe.flags.sf_trading_skip_payment_popup = true;
			frm
				.save("Submit")
				.then(() => {
					create_payments();
				})
				.finally(() => {
					setTimeout(() => {
						delete frappe.flags.sf_trading_skip_payment_popup;
					}, 500);
				});
		} else if (frm.doc.docstatus === 1) {
			// Already submitted: just create payments
			create_payments();
		} else {
			// Draft + user clicked Save: no payment creation; popup will show again when they Submit
			frappe.show_alert({
				message: __("Invoice saved. Submit the invoice when ready to add payments."),
				indicator: "blue",
			}, 4);
		}
	}

	const d = new frappe.ui.Dialog({
		title: __("Enter Payment Amounts"),
		fields: fields,
		primary_action_label: __("Save & Submit"),
		primary_action: function (vals) {
			if (vals) apply_payments_and_close(vals, true);
		},
		secondary_action_label: __("Save"),
		secondary_action: function () {
			const vals = d.get_values();
			// Draft + Save: just close, no validation, no payment creation; reload so form shows saved state
			if (frm.doc.docstatus === 0) {
				d.hide();
				frappe.flags.sf_trading_popup_showing = false;
				frappe.show_alert({
					message: __("Invoice saved. Submit the invoice when ready to add payments."),
					indicator: "blue",
				}, 4);
				frm.reload_doc();
				return;
			}
			if (vals) apply_payments_and_close(vals, false);
		},
		onhide: function() {
			// Reset flag when dialog is closed
			frappe.flags.sf_trading_popup_showing = false;
		}
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
