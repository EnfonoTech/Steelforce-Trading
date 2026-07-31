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
			args: { company: frm.doc.company, currency: frm.doc.currency, supplier: frm.doc.supplier },
			callback: (r) => {
				const template = r.message;
				if (template && frm.doc.taxes_and_charges !== template) {
					frm.set_value("taxes_and_charges", template);
				}
			},
		});
	}

	// ERPNext's core get_party_details passes the document's *current* currency
	// as a fallback to the backend, so when the newly selected supplier has no
	// default_currency, the currency field is left unchanged instead of
	// resetting to the company's currency (erpnext/accounts/party.py: `currency =
	// party.get("default_currency") or currency or get_company_currency(company)`).
	// Correct that here before re-checking the tax template.
	function on_supplier_change(frm) {
		if (!frm.doc.supplier || !frm.doc.company || frm.doc.docstatus !== 0) {
			apply_template_by_currency(frm);
			return;
		}
		frappe.db.get_value("Supplier", frm.doc.supplier, "default_currency").then((r) => {
			const supplier_currency = r.message && r.message.default_currency;
			if (supplier_currency) {
				apply_template_by_currency(frm);
				return;
			}
			frappe.db.get_value("Company", frm.doc.company, "default_currency").then((cr) => {
				const company_currency = cr.message && cr.message.default_currency;
				if (company_currency && frm.doc.currency !== company_currency) {
					frm.set_value("currency", company_currency).then(() => apply_template_by_currency(frm));
				} else {
					apply_template_by_currency(frm);
				}
			});
		});
	}

	DOCTYPES.forEach((dt) => {
		frappe.ui.form.on(dt, {
			currency: apply_template_by_currency,
			company: apply_template_by_currency,
			supplier: on_supplier_change,
			// Catches the case where the supplier's Tax ID was added in another
			// tab while this document was still open — re-checked when the form
			// regains focus/reloads. The authoritative fix is server-side on
			// save regardless.
			refresh: apply_template_by_currency,
		});
	});
})();
