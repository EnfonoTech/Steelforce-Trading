// Receive Payment on a Sales Order — the invoice popup's twin.
//
// Same shape as the Sales Invoice dialog (public/js/sales_invoice.js): one currency field per
// mode of payment the branch allows, a button per mode that fills the whole amount into it,
// and clicking into any amount field fills it with whatever is still unallocated. What differs
// is what is being collected: an order's balance (grand_total less advance_paid), not an
// invoice's outstanding, and there is no Loyalty field because an order carries no receivable
// to write off.
//
// Cheque modes (the branch's "For PDC" rows) get their own section with a cheque number and
// date. Both are asked for only when a cheque amount is actually entered, so a customer paying
// cash is never made to invent a cheque number — the invoice flow can demand them up front
// because it already knows from custom_payment_mode that a cheque is coming; an order does not.

frappe.ui.form.on("Sales Order", {
	refresh(frm) {
		if (frm.doc.docstatus !== 1) return;
		if (frm.doc.status === "Closed") return;

		// advance_paid is hidden on this site's Sales Order form, so the button is the only
		// place the balance is visible — always offer it and let the dialog say "nothing left".
		frm.add_custom_button(__("Receive Payment"), function () {
			sf_trading_show_so_payment_popup(frm);
		}).addClass("btn-primary");
	},
});

function sf_trading_show_so_payment_popup(frm) {
	if (frappe.flags.sf_trading_so_popup_showing) return;
	frappe.flags.sf_trading_so_popup_showing = true;

	frappe.call({
		method: "sf_trading.api.sales_order_payment.get_sales_order_payment_state",
		args: { sales_order: frm.doc.name },
		freeze: true,
		freeze_message: __("Loading payment modes..."),
		callback: function (r) {
			const state = r && r.message;
			if (!state) {
				frappe.flags.sf_trading_so_popup_showing = false;
				return;
			}
			if (flt(state.balance) <= 0) {
				frappe.flags.sf_trading_so_popup_showing = false;
				frappe.msgprint({
					title: __("Nothing to Collect"),
					message: __("Order {0} is already fully paid in advance.", [frm.doc.name]),
					indicator: "blue",
				});
				return;
			}
			if (!(state.modes || []).length && !(state.pdc_modes || []).length) {
				frappe.flags.sf_trading_so_popup_showing = false;
				frappe.msgprint(
					__("No payment modes configured for this branch. Set them on Branch Configuration.")
				);
				return;
			}
			sf_trading_render_so_dialog(frm, state);
		},
		error: function () {
			frappe.flags.sf_trading_so_popup_showing = false;
		},
	});
}

function sf_trading_render_so_dialog(frm, state) {
	const currency = state.currency || frm.doc.currency || "";
	const precision = cint(state.precision) || 2;
	const balance = flt(state.balance, precision);
	const cash_modes = state.modes || [];
	const cheque_modes = state.pdc_modes || [];

	const all_fns = [
		...cash_modes.map((_m, i) => "csh_" + i),
		...cheque_modes.map((_m, i) => "chq_" + i),
	];
	// The order says how it is meant to be settled, so the dialog opens on that footing: a
	// cheque order arrives with the balance already sitting on the first cheque line, a cash
	// order on the first cash line, and an order marked Credit prefills nothing — a deposit on
	// a credit order is allowed, it is just not what anybody expects to be typing.
	const order_mode = frm.doc.custom_payment_mode || "";
	const prefill =
		order_mode === "Cheque" && cheque_modes.length
			? "chq_0"
			: order_mode === "Cash" && cash_modes.length
			? "csh_0"
			: null;

	const fields = [
		{
			fieldname: "balance",
			fieldtype: "Currency",
			label: __("Amount to Pay"),
			default: balance,
			read_only: 1,
			options: "currency",
			precision,
		},
	];
	if (order_mode) {
		fields.push({
			fieldname: "order_mode",
			fieldtype: "Data",
			label: __("Order Payment Mode"),
			default: __(order_mode),
			read_only: 1,
		});
	}
	if (flt(state.advance_paid) > 0) {
		fields.push({
			fieldname: "advance_paid",
			fieldtype: "Currency",
			label: __("Already Received"),
			default: flt(state.advance_paid),
			read_only: 1,
			options: "currency",
			precision,
		});
	}

	if (cash_modes.length) {
		fields.push({ fieldtype: "Section Break", label: __("Enter Payment Amounts") });
		cash_modes.forEach(function (mode, idx) {
			fields.push(
				{ fieldtype: "Section Break", fieldname: "csh_row_" + idx, label: "", hide_border: 1 },
				{
					fieldname: "csh_" + idx,
					fieldtype: "Currency",
					label: mode,
					default: prefill === "csh_" + idx ? balance : 0,
					options: "currency",
					precision,
				},
				{ fieldtype: "Column Break", fieldname: "csh_cb_" + idx },
				{
					fieldtype: "Button",
					fieldname: "fill_csh_" + idx,
					label: mode,
					click: (function (fi) {
						return function () {
							all_fns.forEach(function (fn) {
								d.set_value(fn, 0);
							});
							d.set_value(fi, balance);
						};
					})("csh_" + idx),
				}
			);
		});
	}

	if (cheque_modes.length) {
		fields.push(
			{ fieldtype: "Section Break", label: __("Cheque Payments") },
			{ fieldname: "cheque_no", fieldtype: "Data", label: __("Cheque No") },
			{ fieldtype: "Column Break", fieldname: "cheque_cb" },
			{
				fieldname: "cheque_date",
				fieldtype: "Date",
				label: __("Cheque Date"),
				default: frappe.datetime.get_today(),
			}
		);
		cheque_modes.forEach(function (mode, idx) {
			fields.push(
				{ fieldtype: "Section Break", fieldname: "chq_row_" + idx, label: "", hide_border: 1 },
				{
					fieldname: "chq_" + idx,
					fieldtype: "Currency",
					label: mode,
					default: prefill === "chq_" + idx ? balance : 0,
					options: "currency",
					precision,
				},
				{ fieldtype: "Column Break", fieldname: "chq_cb_" + idx },
				{
					fieldtype: "Button",
					fieldname: "fill_chq_" + idx,
					label: mode,
					click: (function (fi) {
						return function () {
							all_fns.forEach(function (fn) {
								d.set_value(fn, 0);
							});
							d.set_value(fi, balance);
						};
					})("chq_" + idx),
				}
			);
		});
	}

	function collect(vals) {
		const payload = [];
		let total = 0;
		let cheque_total = 0;
		cash_modes.forEach(function (mode, i) {
			const amount = flt(vals["csh_" + i]) || 0;
			if (amount > 0) {
				payload.push({ mode_of_payment: mode, amount });
				total += amount;
			}
		});
		cheque_modes.forEach(function (mode, i) {
			const amount = flt(vals["chq_" + i]) || 0;
			if (amount > 0) {
				payload.push({ mode_of_payment: mode, amount });
				total += amount;
				cheque_total += amount;
			}
		});
		return { payload, total: flt(total, precision), cheque_total };
	}

	function apply_and_close(vals) {
		if (!vals) return;
		const { payload, total, cheque_total } = collect(vals);
		if (!payload.length) {
			frappe.msgprint({
				title: __("Error"),
				message: __("Please enter at least one payment amount."),
				indicator: "red",
			});
			return;
		}
		if (total - balance > 0.0001) {
			frappe.msgprint({
				title: __("Error"),
				message: __("Total payment {0} cannot be greater than amount to pay {1}.", [
					format_currency(total, currency),
					format_currency(balance, currency),
				]),
				indicator: "red",
			});
			return;
		}
		// A cheque needs its number and date; anything else does not. Asked for here rather
		// than as reqd fields so a cash-only collection is never blocked by them.
		if (cheque_total > 0 && !((vals.cheque_no || "").trim() && vals.cheque_date)) {
			frappe.msgprint({
				title: __("Cheque Details Required"),
				message: __("Enter the cheque number and date for the cheque amount."),
				indicator: "red",
			});
			return;
		}

		d.hide();
		frappe.flags.sf_trading_so_popup_showing = false;
		frappe.call({
			method: "sf_trading.api.sales_order_payment.create_payments_for_sales_order",
			args: {
				sales_order: frm.doc.name,
				payments: JSON.stringify(payload),
				cheque_no: cheque_total > 0 ? (vals.cheque_no || "").trim() : undefined,
				cheque_date: cheque_total > 0 ? vals.cheque_date : undefined,
			},
			freeze: true,
			freeze_message: __("Creating payments..."),
			callback: function (r) {
				const created = (r && r.message) || [];
				if (created.length) {
					frappe.show_alert(
						{
							message: __("Created {0} Payment Entries for this order", [created.length]),
							indicator: "green",
						},
						5
					);
				}
				frm.reload_doc();
			},
		});
	}

	const d = new frappe.ui.Dialog({
		title: __("Receive Payment"),
		fields,
		primary_action_label: __("Receive Payment"),
		primary_action: function (vals) {
			if (vals) apply_and_close(vals);
		},
		onhide: function () {
			frappe.flags.sf_trading_so_popup_showing = false;
		},
	});
	d.show();

	frappe.utils.sleep(100).then(function () {
		d.$wrapper.find(".section-body").css({ display: "flex", alignItems: "flex-end" });
		all_fns.forEach(function (fn, idx) {
			const field = d.fields_dict[fn];
			if (!field || !field.$wrapper) return;
			field.$wrapper
				.find("input")
				.off("click.sf_so_fill")
				.on("click.sf_so_fill", function () {
					let other = 0;
					all_fns.forEach(function (ofn, oi) {
						if (oi !== idx) other += flt(d.get_value(ofn)) || 0;
					});
					d.set_value(fn, Math.max(0, flt(balance - other, precision)));
				});
		});
	});
}
