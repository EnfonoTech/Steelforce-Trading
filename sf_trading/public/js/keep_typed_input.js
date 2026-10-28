// sf_trading/public/js/keep_typed_input.js
// A field keeps what is being typed into it while the form redraws.
//
// Frappe puts the typed text into the document only when the field is left (the change event). Until
// then it lives in the input alone, and every redraw of the field copies the DOCUMENT's value back
// over it. On a Sales Invoice a redraw is not rare: choosing the customer sets a dozen fields from the
// server over the next five seconds (address, contact, price list, currency, payment terms, ...), and each
// round of that redraws every grid -- so an item code typed into the first row a moment after the
// customer was picked simply disappeared, with no message, while the cursor stayed in the cell.
//
// The rule here is narrow: while a field has the cursor AND the document has not changed since the field
// last drew it, a redraw can only be about to erase what the user typed, so the typed text stays. A value
// that really changed (a barcode scan, an item fetch, a price list) still goes in, and a field the user has
// left behaves as it always did.

(function () {
	if (!frappe.ui || !frappe.ui.form || !frappe.ui.form.ControlInput) return;
	const proto = frappe.ui.form.ControlInput.prototype;
	if (proto.__sf_keeps_typed_input) return;
	proto.__sf_keeps_typed_input = true;

	const original = proto.refresh_input;
	proto.refresh_input = function () {
		const input = this.$input && this.$input[0];
		let typed = null;

		if (
			input &&
			input === document.activeElement &&
			!input.readOnly &&
			!input.disabled &&
			this.frm &&
			this.doctype &&
			this.docname &&
			this.df
		) {
			const in_document = frappe.model.get_value(this.doctype, this.docname, this.df.fieldname);
			if ((this.value || "") === (in_document || "")) typed = input.value;
		}

		const result = original.apply(this, arguments);
		if (typed !== null && input.value !== typed) input.value = typed;
		return result;
	};
})();
