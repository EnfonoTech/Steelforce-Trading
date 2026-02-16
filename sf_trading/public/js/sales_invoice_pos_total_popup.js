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
		// Prevent popup if flag is set (we're saving from popup)
		if (frappe.flags.sf_trading_skip_payment_popup) return;
		
		// Prevent popup if already showing
		if (frappe.flags.sf_trading_popup_showing) return;
		
		// Only show for POS invoices in draft state
		if (!frm.doc.is_pos || frm.doc.docstatus !== 0) return;
		
		// Validate required fields
		if (!frm.doc.pos_profile || !frm.doc.grand_total || frm.doc.grand_total <= 0) return;
		
		// Ensure form is ready
		if (!frm.doc.name || frm.doc.name.startsWith("new-")) return;

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
	// Prevent multiple popups
	if (frappe.flags.sf_trading_popup_showing) return;
	
	// Validate form state
	if (!frm || !frm.doc || !frm.doc.pos_profile) {
		console.warn("sf_trading: Cannot show popup - invalid form state");
		return;
	}
	
	frappe.flags.sf_trading_popup_showing = true;
	
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
							error: function() {
								frappe.flags.sf_trading_popup_showing = false;
								frappe.msgprint(__("Error loading payment accounts. Please try again."));
							}
						});
					} else {
						frappe.flags.sf_trading_popup_showing = false;
						frappe.msgprint(__("Add payment modes in POS Profile first"));
					}
				},
				error: function() {
					frappe.flags.sf_trading_popup_showing = false;
					frappe.msgprint(__("Error loading POS Profile. Please try again."));
				}
			});
		} else {
			sf_trading_render_dialog(frm);
		}
	}

	do_show_popup();
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
		// Prevent multiple simultaneous saves
		if (frappe.flags.sf_trading_saving) {
			frappe.msgprint({
				title: __("Please Wait"),
				message: __("Saving in progress. Please wait..."),
				indicator: "orange",
			});
			return;
		}
		
		// Validate form state
		if (!frm || !frm.doc || frm.doc.docstatus !== 0) {
			frappe.msgprint({
				title: __("Error"),
				message: __("Cannot update payments. Form is not in draft state."),
				indicator: "red",
			});
			return;
		}
		
		// Validate inputs
		if (!vals) {
			frappe.msgprint({
				title: __("Error"),
				message: __("Please enter payment amounts."),
				indicator: "red",
			});
			return;
		}
		
		let total = 0;
		// First validate total
		payments.forEach(function (p, i) {
			const amt = flt(vals["pay_" + i]) || 0;
			total += amt;
		});
		
		if (total < invoice_total) {
			frappe.msgprint({
				title: __("Incomplete"),
				message: __("{0} still to be allocated", [format_currency(invoice_total - total, currency)]),
				indicator: "red",
			});
			return;
		}
		
		// Ensure form payments exist and match
		const form_payments = frm.doc.payments || [];
		if (form_payments.length === 0) {
			frappe.msgprint({
				title: __("Error"),
				message: __("No payment methods found. Please refresh the form."),
				indicator: "red",
			});
			return;
		}
		
		// Ensure conversion_rate is valid
		const conversion_rate = flt(frm.doc.conversion_rate) || 1;
		
		// Helper function for precision
		const get_precision = function(fieldname, doc) {
			try {
				return precision(fieldname, doc) || 2;
			} catch(e) {
				return 2; // Default precision
			}
		};
		
		// Update payments with robust matching
		let update_count = 0;
		payments.forEach(function (p, i) {
			const amt = flt(vals["pay_" + i]) || 0;
			if (amt <= 0) return; // Skip zero amounts
			
			const base_amt = flt(amt * conversion_rate, get_precision("base_amount", p));
			
			// Try multiple matching strategies for reliability
			let form_payment = null;
			
			// Strategy 1: Match by mode_of_payment
			if (p.mode_of_payment) {
				form_payment = form_payments.find(fp => fp.mode_of_payment === p.mode_of_payment);
			}
			
			// Strategy 2: Match by index if same length
			if (!form_payment && i < form_payments.length && payments.length === form_payments.length) {
				form_payment = form_payments[i];
			}
			
			// Strategy 3: Match by idx if available
			if (!form_payment && p.idx) {
				form_payment = form_payments.find(fp => fp.idx === p.idx);
			}
			
			// Strategy 4: Match by name if available
			if (!form_payment && p.name) {
				form_payment = form_payments.find(fp => fp.name === p.name);
			}
			
			// Update if match found
			if (form_payment) {
				form_payment.amount = amt;
				form_payment.base_amount = base_amt;
				update_count++;
			}
		});
		
		// Validate that we updated at least one payment
		if (update_count === 0) {
			frappe.msgprint({
				title: __("Error"),
				message: __("Could not match payments. Please refresh the form and try again."),
				indicator: "red",
			});
			return;
		}
		
		// Mark form as dirty and refresh payments field
		frm.dirty();
		frm.refresh_field("payments");
		
		// Recalculate totals if available
		if (frm.cscript && frm.cscript.calculate_taxes_and_totals) {
			try {
				frm.cscript.calculate_taxes_and_totals(true);
			} catch (e) {
				console.warn("Error calculating taxes:", e);
			}
		}
		
		// Close dialog before saving and reset popup flag
		d.hide();
		frappe.flags.sf_trading_skip_payment_popup = true;
		frappe.flags.sf_trading_popup_showing = false;
		frappe.flags.sf_trading_saving = true;
		
		// Use save with "Submit" action instead of savesubmit
		const save_action = submit ? "Submit" : "Save";
		
		// Add small delay to ensure form updates are processed
		setTimeout(function() {
			frm.save(save_action).then(function() {
				if (submit) {
					// Reload after submit to show updated status
					setTimeout(function() {
						frm.reload_doc();
					}, 300);
				}
			}).catch(function(err) {
				// Show error if save fails
				frappe.msgprint({
					title: __("Error"),
					message: __("Failed to save invoice: {0}", [err.message || err]),
					indicator: "red",
				});
			}).finally(function () {
				setTimeout(function () {
					delete frappe.flags.sf_trading_skip_payment_popup;
					delete frappe.flags.sf_trading_saving;
				}, 500);
			});
		}, 100);
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
