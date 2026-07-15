// Currency-based Purchase Tax Template — live form feedback.
// The rule itself is enforced server-side in sf_trading/purchase_tax_template.py
// (before_validate), so this only keeps the form display in sync.

(function () {
	const DOCTYPES = [
		"Supplier Quotation",
		"Purchase Order",
		"Purchase Receipt",
		"Purchase Invoice",
	];

	function apply_template_by_currency(frm) {
		if (!frm.doc.company || !frm.doc.currency || frm.doc.docstatus !== 0) return;
		frappe.call({
			method: "sf_trading.purchase_tax_template.get_expected_template",
			args: { company: frm.doc.company, currency: frm.doc.currency },
			callback: (r) => {
				const template = r.message;
				if (template && frm.doc.taxes_and_charges !== template) {
					frm.set_value("taxes_and_charges", template);
				}
			},
		});
	}

	DOCTYPES.forEach((dt) => {
		frappe.ui.form.on(dt, {
			currency: apply_template_by_currency,
			company: apply_template_by_currency,
		});
	});
})();
