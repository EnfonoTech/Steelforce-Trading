// Do not offer a return the server is going to refuse.
//
// The refusal is server-side and in two places: the endpoint that builds the credit note
// (sf_trading.sales_return.make_sales_return) and the save of the return itself. This file is only
// about not offering the action, and about saying why.
//
// Removing the button after the fact does not work. ERPNext adds Return / Credit Note inside the
// Sales Invoice controller's own refresh (erpnext/accounts/doctype/sales_invoice/sales_invoice.js),
// synchronously, and the toolbar is rebuilt on every render — so a removal that runs a moment
// later (this one has to, it needs an answer from the server) takes away a button the next render
// puts straight back, and no amount of retrying settles it.
//
// So the add itself is intercepted. While a document is flagged blocked, the page declines to paint
// that one button, however many times anything asks. The flag is per document and re-read on every
// refresh; every other button goes through untouched.

const SF_RETURN_LABEL = "Return / Credit Note";

function sf_guard_return_button(frm) {
	if (frm.__sf_return_button_guarded) return;
	frm.__sf_return_button_guarded = true;

	const original = frm.page.add_inner_button.bind(frm.page);
	frm.page.add_inner_button = function (label, action, group, type) {
		if (frm.__sf_block_return_button && label === __(SF_RETURN_LABEL)) {
			return;
		}
		return original(label, action, group, type);
	};
}

frappe.ui.form.on("Sales Invoice", {
	onload(frm) {
		sf_guard_return_button(frm);
	},

	refresh(frm) {
		sf_guard_return_button(frm);

		if (frm.doc.docstatus !== 1 || frm.doc.is_return) {
			frm.__sf_block_return_button = false;
			return;
		}

		const ticket = frm.doc.name;
		// One form object serves every document of this doctype, so the answer is remembered
		// against the document it belongs to and re-applied on each render.
		if (frm.__sf_return_window && frm.__sf_return_window.name === ticket) {
			sf_apply_return_window(frm, frm.__sf_return_window.state);
			return;
		}

		frm.__sf_block_return_button = false;
		frappe.call({
			method: "sf_trading.sales_return.check_source_return_window",
			args: { doctype: frm.doctype, docname: frm.doc.name },
			callback(r) {
				const state = r && r.message;
				if (!state || frm.doc.name !== ticket) return;
				frm.__sf_return_window = { name: ticket, state };
				sf_apply_return_window(frm, state);
			},
		});
	},
});

function sf_apply_return_window(frm, state) {
	frm.__sf_block_return_button = !!(state.enabled && state.blocked);

	if (!state.enabled || !state.past_window) return;

	const basis =
		state.basis === "Return Posting Date"
			? __("the return's own posting date")
			: __("this invoice's date");
	const past_by = state.age - state.days;

	if (state.blocked) {
		// already painted by an earlier render, before the answer arrived
		frm.remove_custom_button(__(SF_RETURN_LABEL), __("Create"));

		frm.dashboard.add_indicator(
			__("Return window closed — {0} days old, limit {1}", [state.age, state.days]),
			"red"
		);
		frm.dashboard.add_comment(
			__("A return against this invoice would be {0} day(s) past the {1} day window, counted from {2}. Ask someone authorised to override the sales return window.", [
				past_by,
				state.days,
				basis,
			]),
			"red",
			true
		);
		return;
	}

	frm.dashboard.add_indicator(
		__("Return window passed ({0} days) — you may override", [state.age]),
		"orange"
	);
	frm.dashboard.add_comment(
		__("A return against this invoice is {0} day(s) past the {1} day window, counted from {2}. You are allowed to raise one anyway.", [
			past_by,
			state.days,
			basis,
		]),
		"yellow",
		true
	);
}
