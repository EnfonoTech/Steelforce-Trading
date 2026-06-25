// sf_trading: Popup to enter payment amounts for Sales Invoice
// Shows after save when the correct grand_total is available
function sf_trading_open_invoice_print(frm) {
	if (!frm || !frm.doc || !frm.doc.name) return;
	const base_url = window.location.origin;
	const format = encodeURIComponent(frm.meta.default_print_format || "");

	const url =
		`${base_url}/printview?` +
		`doctype=Sales%20Invoice` +
		`&name=${encodeURIComponent(frm.doc.name)}` +
		`&trigger_print=1` +
		`&format=${format}` +
		`&no_letterhead=0` +
		`&settings=%7B%7D` +
		`&_lang=${frappe.boot.lang}`;

	const a = document.createElement("a");
	a.href = url;
	a.target = "_blank";
	a.rel = "noopener noreferrer";
	document.body.appendChild(a);
	a.click();
	document.body.removeChild(a);
}

frappe.ui.form.on("Sales Invoice", {
	refresh: function (frm) {
		// Remove "Permanently Submit?" confirmation when clicking Submit.
		// Popup/confirm logic is handled in before_submit below.
		if (!frm._sf_savesubmit_wrapped) {
			frm._sf_savesubmit_wrapped = true;
			frm.savesubmit = function (btn, callback, on_error) {
				var me = this;
				me.validate_form_action("Submit");
				frappe.validated = true;
				me.script_manager.trigger("before_submit").then(function () {
					if (!frappe.validated) return me.handle_save_fail(btn, on_error);
					me.save(
						"Submit",
						function (r) {
							if (r.exc) {
								me.handle_save_fail(btn, on_error);
							} else {
								frappe.utils.play_sound("submit");
								callback && callback();
								me.script_manager.trigger("on_submit").then(function () {
									if (frappe.route_hooks.after_submit) {
										var cb = frappe.route_hooks.after_submit;
										delete frappe.route_hooks.after_submit;
										cb(me);
									}
								});
							}
						},
						btn,
						function () { me.handle_save_fail(btn, on_error); }
					);
				});
			};
		}

		// Warn once when a new PDC return is first opened
		if (frm.is_new() && frm.doc.is_return && frm.doc.custom_payment_mode === "PDC") {
			frappe.msgprint({
				title: __("PDC Return"),
				message: __("This is a return for a PDC invoice. Confirm the payment with accounts before submitting."),
				indicator: "orange",
			});
		}

		// Add Print button on submitted invoices
		if (frm.doc.docstatus === 1) {
			frm.add_custom_button(__("Print Invoice"), function () {
				sf_trading_open_invoice_print(frm);
			});
		}
	},
	before_submit: function (frm) {
		// PDC: show PDC popup (cheque date + no + amount)
		if (frm.doc.custom_payment_mode === "PDC") {
			frappe.validated = false;
			if (Math.abs(flt(frm.doc.grand_total)) > 0 && Math.abs(flt(frm.doc.outstanding_amount)) > 0) {
				sf_trading_show_pdc_popup(frm);
			} else {
				// Advance fully covers the invoice — submit directly then print
				frm.save("Submit").then(function () {
					if (frm.doc.docstatus === 1) {
						sf_trading_open_invoice_print(frm);
						frm.reload_doc();
					}
				});
			}
			return;
		}

		// Cash: show payment popup
		if (frm.doc.custom_payment_mode !== "Credit") {
			frappe.validated = false;
			if (Math.abs(flt(frm.doc.grand_total)) > 0 && Math.abs(flt(frm.doc.outstanding_amount)) > 0) {
				sf_trading_show_pos_total_popup(frm);
			} else {
				// Advance fully covers the invoice — submit directly then print
				frm.save("Submit").then(function () {
					if (frm.doc.docstatus === 1) {
						sf_trading_open_invoice_print(frm);
						frm.reload_doc();
					}
				});
			}
			return;
		}

		// Credit: show confirm before submitting
		frappe.validated = false;
		frappe.confirm(
			__("Do you want to Submit this Sales Invoice?"),
			function () {
				frappe.flags.sf_trading_submitting_credit = true;
				frm.save("Submit").then(function () {
					if (frm.doc.docstatus === 1) {
						sf_trading_open_invoice_print(frm);
						frm.reload_doc();
					}
				}).finally(function () {
					delete frappe.flags.sf_trading_submitting_credit;
				});
			}
		);
	},
	after_save: function (frm) {
		if (frappe.flags.sf_trading_skip_payment_popup) return;
		if (frappe.flags.sf_trading_popup_showing) return;
		if (frm.doc.docstatus !== 0) return;
		if (!frm.doc.name || frm.doc.name.startsWith("new-")) return;
		// Credit: ask "Do you want to Submit?" after saving
		if (frm.doc.custom_payment_mode === "Credit") {
			if (frappe.flags.sf_trading_credit_confirm_open) return;
			frappe.flags.sf_trading_credit_confirm_open = true;
			const d = frappe.confirm(
				__("Do you want to Submit this Sales Invoice now?"),
				function () {
					frappe.flags.sf_trading_skip_payment_popup = true;
					frappe.flags.sf_trading_submitting_credit = true;
					frm.save("Submit").then(function () {
						if (frm.doc.docstatus === 1) {
							sf_trading_open_invoice_print(frm);
							frm.reload_doc();
						}
					}).finally(function () {
						delete frappe.flags.sf_trading_submitting_credit;
						setTimeout(function () {
							delete frappe.flags.sf_trading_skip_payment_popup;
						}, 500);
					});
				},
				function () { /* user chose No: invoice already saved, nothing to do */ }
			);
			// Always reset flag when dialog closes (Yes, No, or X button)
			if (d) {
				d.onhide = function () {
					delete frappe.flags.sf_trading_credit_confirm_open;
				};
			}
			return;
		}

		// PDC: show PDC popup after save (same flow as Cash popup)
		if (frm.doc.custom_payment_mode === "PDC") {
			if (Math.abs(flt(frm.doc.grand_total)) > 0 && Math.abs(flt(frm.doc.outstanding_amount)) > 0) {
				sf_trading_show_pdc_popup(frm);
			}
			return;
		}

		// Non-credit: show Cash payment popup after save
		if (!frm.doc.grand_total || Math.abs(flt(frm.doc.grand_total)) <= 0) return;
		if (Math.abs(flt(frm.doc.outstanding_amount)) <= 0) return;

		sf_trading_show_pos_total_popup(frm);
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
		// Payment modes come from the branch/company allowlist only — POS Profile is not used.
		frappe.call({
			method: "sf_trading.api.sales_invoice_payment.get_payment_modes_with_account",
			args: { company: frm.doc.company, is_return: frm.doc.is_return ? 1 : 0, branch: frm.doc.branch || undefined },
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
					method: "sf_trading.api.sales_invoice_payment.get_accounts_for_modes",
					args: { company: frm.doc.company, modes: JSON.stringify(modes) },
					callback: function (ar) {
						const accounts = ar.message || {};
						(frm.doc.payments || []).forEach(function (p) { p.account = accounts[p.mode_of_payment] || ""; });
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

	// Use outstanding_amount when positive (handles advance payments reducing the payable amount).
	// Fall back to rounded_total / grand_total for fresh invoices where outstanding may not yet differ.
	const invoice_total = Math.abs(flt(
		(frm.doc.outstanding_amount > 0 ? frm.doc.outstanding_amount : null) ||
		frm.doc.rounded_total ||
		frm.doc.grand_total || 0
	));
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
			label: __("Amount to Pay"),
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

		// Prevent over-payment
		if (total - invoice_total > 0.0001) {
			frappe.msgprint({
				title: __("Error"),
				message: __(
					"Total payment amount {0} cannot be greater than amount to pay {1}.",
					[format_currency(total, currency), format_currency(invoice_total, currency)]
				),
				indicator: "red",
			});
			return;
		}

		// Prevent under-allocation
		if (invoice_total - total > 0.0001) {
			frappe.msgprint({
				title: __("Incomplete"),
				message: __("{0} still to be allocated", [
					format_currency(invoice_total - total, currency),
				]),
				indicator: "red",
			});
			return;
		}

		// Check outstanding_amount (set after submission) then close dialog and create payments.
		// Keeps dialog visible until we know it's safe — if outstanding < total (e.g. advances
		// already applied), the error shows while the dialog is still on screen.
		const finalize_payments = () => {
			const actual_os = Math.abs(flt(frm.doc.outstanding_amount || 0));
			if (actual_os > 0 && flt(total - actual_os) > 0.0001) {
				frappe.msgprint({
					title: __("Payment Error"),
					message: __(
						"Payment total ({0}) exceeds outstanding amount ({1}). " +
						"The invoice may have advance payments already applied. " +
						"Please create the payment manually for the correct outstanding amount.",
						[format_currency(total, currency), format_currency(actual_os, currency)]
					),
					indicator: "red",
				});
				return;
			}
			d.hide();
			frappe.flags.sf_trading_popup_showing = false;
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
			});
		};

		// Ensure invoice is submitted before creating payments if user chose Save & Submit
		if (submit && frm.doc.docstatus === 0) {
			frappe.flags.sf_trading_skip_payment_popup = true;
			frm
				.save("Submit")
				.then(() => {
					// Only print and create payments if submission actually succeeded
					if (frm.doc.docstatus !== 1) return;
					sf_trading_open_invoice_print(frm);
					finalize_payments();
				})
				.finally(() => {
					setTimeout(() => {
						delete frappe.flags.sf_trading_skip_payment_popup;
					}, 500);
				});
		} else if (frm.doc.docstatus === 1) {
			// Already submitted: validate outstanding then create payments
			finalize_payments();
		} else {
			// Draft + user clicked Save: no payment creation; popup will show again when they Submit
			d.hide();
			frappe.flags.sf_trading_popup_showing = false;
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
				d.set_value("pay_" + idx, Math.max(0, flt(invoice_total - other)));
			});
		});
	});
}

// ── PDC popup ─────────────────────────────────────────────────────────────────
// Same Save / Save & Submit flow as the Cash popup, but collects Cheque Date
// and Cheque No, and passes them to create_pos_payments_for_invoice so the
// Payment Entry is posted on the future cheque date.

function sf_trading_show_pdc_popup(frm) {
	if (frappe.flags.sf_trading_popup_showing) return;
	if (!frm || !frm.doc) return;

	frappe.flags.sf_trading_popup_showing = true;

	const invoice_total = Math.abs(flt(
		(frm.doc.outstanding_amount > 0 ? frm.doc.outstanding_amount : null) ||
		frm.doc.rounded_total ||
		frm.doc.grand_total || 0
	));
	const currency = frm.doc.currency || "";

	function load_pdc_modes_then_show() {
		frappe.call({
			method: "sf_trading.api.sales_invoice_payment.get_payment_modes_with_account",
			args: { company: frm.doc.company, is_pdc: 1, is_return: frm.doc.is_return ? 1 : 0, branch: frm.doc.branch || undefined },
			callback: function (r) {
				const modes = r.message || [];
				if (!modes.length) {
					frappe.flags.sf_trading_popup_showing = false;
					frappe.msgprint(__("No PDC Mode of Payment configured for this branch. Please mark a Mode of Payment as 'For PDC' in Branch Configuration."));
					return;
				}
				show_pdc_dialog(modes);
			},
			error: function () {
				frappe.flags.sf_trading_popup_showing = false;
				frappe.msgprint(__("Error loading PDC payment modes. Please try again."));
			},
		});
	}

	function show_pdc_dialog(modes) {
		const fields = [
			{
				fieldname: "invoice_total",
				fieldtype: "Currency",
				label: __("Amount to Pay"),
				default: invoice_total,
				read_only: 1,
				options: currency,
			},
			{ fieldtype: "Section Break", label: __("Cheque Details") },
			{
				fieldname: "cheque_date",
				fieldtype: "Date",
				label: __("Cheque Date"),
				reqd: 1,
			},
			{
				fieldname: "cheque_no",
				fieldtype: "Data",
				label: __("Cheque No"),
				reqd: 1,
			},
			{ fieldtype: "Section Break", label: __("Payment Amounts") },
		];

		modes.forEach(function (mode, idx) {
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
					default: idx === 0 ? invoice_total : 0,
					options: currency,
				},
				{ fieldtype: "Column Break", fieldname: "cb_" + idx },
				{
					fieldtype: "Button",
					fieldname: "fill_" + idx,
					label: mode,
					click: function () {
						modes.forEach(function (_, i) {
							d.set_value("pay_" + i, i === idx ? invoice_total : 0);
						});
					},
				}
			);
		});

		function apply_and_close(vals, submit) {
			if (!vals) return;
			const cheque_date = vals.cheque_date;
			const cheque_no = (vals.cheque_no || "").trim();
			if (!cheque_date || !cheque_no) {
				frappe.msgprint({ title: __("Required"), message: __("Please enter Cheque Date and Cheque No."), indicator: "red" });
				return;
			}

			let total = 0;
			const payments_payload = [];
			modes.forEach(function (mode, i) {
				const amt = flt(vals["pay_" + i]) || 0;
				if (amt > 0) {
					payments_payload.push({ mode_of_payment: mode, amount: amt });
					total += amt;
				}
			});

			if (!payments_payload.length) {
				frappe.msgprint({ title: __("Error"), message: __("Please enter at least one payment amount."), indicator: "red" });
				return;
			}
			if (total - invoice_total > 0.0001) {
				frappe.msgprint({ title: __("Error"), message: __("Total payment {0} exceeds amount to pay {1}.", [format_currency(total, currency), format_currency(invoice_total, currency)]), indicator: "red" });
				return;
			}
			if (invoice_total - total > 0.0001) {
				frappe.msgprint({ title: __("Incomplete"), message: __("{0} still to be allocated.", [format_currency(invoice_total - total, currency)]), indicator: "red" });
				return;
			}

			const finalize_pdc_payments = function () {
				const actual_os = Math.abs(flt(frm.doc.outstanding_amount || 0));
				if (actual_os > 0 && flt(total - actual_os) > 0.0001) {
					frappe.msgprint({
						title: __("Payment Error"),
						message: __(
							"Payment total ({0}) exceeds outstanding amount ({1}). " +
							"The invoice may have advance payments already applied. " +
							"Please create the payment manually for the correct outstanding amount.",
							[format_currency(total, currency), format_currency(actual_os, currency)]
						),
						indicator: "red",
					});
					return;
				}
				d.hide();
				frappe.flags.sf_trading_popup_showing = false;
				frappe.call({
					method: "sf_trading.api.sales_invoice_payment.create_pos_payments_for_invoice",
					args: {
						sales_invoice: frm.doc.name,
						payments: JSON.stringify(payments_payload),
						cheque_date: cheque_date,
						cheque_no: cheque_no,
					},
					freeze: true,
					freeze_message: __("Creating PDC payment..."),
					callback: function (r) {
						if (r && r.message && r.message.length) {
							frappe.show_alert({ message: __("PDC Payment Entry created."), indicator: "green" }, 5);
							frm.reload_doc();
						}
					},
				});
			};

			if (submit && frm.doc.docstatus === 0) {
				frappe.flags.sf_trading_skip_payment_popup = true;
				frm.save("Submit").then(function () {
					if (frm.doc.docstatus !== 1) return;
					sf_trading_open_invoice_print(frm);
					finalize_pdc_payments();
				}).finally(function () {
					setTimeout(function () { delete frappe.flags.sf_trading_skip_payment_popup; }, 500);
				});
			} else if (frm.doc.docstatus === 1) {
				finalize_pdc_payments();
			} else {
				frappe.show_alert({ message: __("Invoice saved. Submit when ready to record the PDC payment."), indicator: "blue" }, 4);
			}
		}

		const d = new frappe.ui.Dialog({
			title: __("PDC Payment"),
			fields: fields,
			primary_action_label: __("Save & Submit"),
			primary_action: function (vals) { if (vals) apply_and_close(vals, true); },
			secondary_action_label: __("Save"),
			secondary_action: function () {
				if (frm.doc.docstatus === 0) {
					d.hide();
					frappe.flags.sf_trading_popup_showing = false;
					frappe.show_alert({ message: __("Invoice saved. Submit when ready to record the PDC payment."), indicator: "blue" }, 4);
					frm.reload_doc();
					return;
				}
				const vals = d.get_values();
				if (vals) apply_and_close(vals, false);
			},
			onhide: function () { frappe.flags.sf_trading_popup_showing = false; },
		});

		d.show();

		// Align amount field and button on the same line
		frappe.utils.sleep(100).then(function () {
			d.$wrapper.find(".section-body").css({ display: "flex", alignItems: "flex-end" });

			// Click on amount field → auto-fill with remaining balance
			modes.forEach(function (_, idx) {
				const field = d.fields_dict["pay_" + idx];
				if (!field || !field.$wrapper) return;
				field.$wrapper.find("input").off("click.sf_pdc").on("click.sf_pdc", function () {
					let other = 0;
					modes.forEach(function (__, i) {
						if (i !== idx) other += flt(d.get_value("pay_" + i)) || 0;
					});
					d.set_value("pay_" + idx, Math.max(0, flt(invoice_total - other)));
				});
			});
		});
	}

	load_pdc_modes_then_show();
}

// ── Credit Limit fetch ────────────────────────────────────────────────────────
// When custom_payment_mode = "Credit", read the customer's credit limit from
// the Customer Credit Limit child table and populate custom_credit_limit.

frappe.ui.form.on("Sales Invoice", {
	custom_payment_mode: function (frm) {
		sf_set_credit_limit(frm);
	},
	customer: function (frm) {
		sf_set_credit_limit(frm);
	},
	company: function (frm) {
		sf_set_credit_limit(frm);
	},
});

function sf_set_credit_limit(frm) {
	if (frm.doc.custom_payment_mode !== "Credit" || !frm.doc.customer) {
		frm.doc.custom_credit_limit = 0;
		frm.refresh_field("custom_credit_limit");
		return;
	}

	var company = frm.doc.company || frappe.defaults.get_default("company");

	// Step 1: get credit limit from Customer doc
	frappe.db.get_doc("Customer", frm.doc.customer).then(function (cust) {
		var credit_limit = 0;
		if (cust.credit_limits && cust.credit_limits.length) {
			var row = cust.credit_limits.find(function (r) { return r.company === company; });
			credit_limit = flt(row ? row.credit_limit : cust.credit_limits[0].credit_limit);
		}
		if (!credit_limit) {
			frm.doc.custom_credit_limit = 0;
			frm.refresh_field("custom_credit_limit");
			return;
		}
		// Step 2: subtract grand_total of ALL submitted credit invoices (paid or unpaid)
		frappe.db.get_list("Sales Invoice", {
			filters: {
				customer: frm.doc.customer,
				company: company,
				custom_payment_mode: "Credit",
				docstatus: 1,
			},
			fields: ["grand_total"],
			limit: 500,
		}).then(function (invoices) {
			var used = 0;
			(invoices || []).forEach(function (inv) { used += flt(inv.grand_total); });
			var available = Math.max(0, flt(credit_limit - used, 2));
			frm.doc.custom_credit_limit = available;
			frm.refresh_field("custom_credit_limit");
		});
	});
}
