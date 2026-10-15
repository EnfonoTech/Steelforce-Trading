// sf_trading/public/js/cash_due_date.js
// A Cash sale is due the day it is made: the customer's payment terms are not looked at.
// Server side is sf_trading/cash_due_date.py (the authority, it runs on every save); this only keeps the form
// honest while it is open, because ERPNext fills the template and the due date from the customer after the fact.
(function () {
	function apply(frm) {
		const doc = frm.doc;
		if (doc.docstatus !== 0) return;
		if (doc.custom_payment_mode !== "Cash" || doc.is_return || doc.is_pos) return;

		const day = doc.posting_date || doc.transaction_date;
		if (!day) return;

		if (doc.payment_terms_template) {
			frm.set_value("payment_terms_template", "");
		}
		if (frm.fields_dict.due_date && doc.due_date !== day) {
			frm.set_value("due_date", day);
		}
		if ((doc.payment_schedule || []).length) {
			frm.clear_table("payment_schedule");
			frm.refresh_field("payment_schedule");
		}
	}

	// one handler for every moment the terms or the date can change under a Cash sale: picking the mode, the date,
	// or the customer (ERPNext then sets the template and the due date from the party, a moment later)
	const events = {
		custom_payment_mode: apply,
		posting_date: apply,
		transaction_date: apply,
		payment_terms_template: apply,
		due_date: apply,
	};

	["Sales Invoice", "Sales Order"].forEach((doctype) => frappe.ui.form.on(doctype, events));
})();
