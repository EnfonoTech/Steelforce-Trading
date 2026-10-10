// sf_trading/public/js/party_documents.js
// Customer and Supplier forms: the Supporting Documents grid and the new-master rules
// (sf_trading/party_documents.py).
//
//   * Picking a Document Name fills the row from its Supporting Document Type: Validate, Days
//     Ahead, Grace Days, Financial Implication.
//   * On a master the rules govern (every supplier, a B2B customer, created on or after SF Trading
//     Settings > New-Master Rules From) Payment Terms and the documents grid are marked required,
//     and the form says what is still missing. The server enforces the same rules.

// loaded by both the Customer and the Supplier form: register the handlers once per session
if (!window.sf_party_documents_loaded) {
window.sf_party_documents_loaded = true;

function sf_party_rules_apply(frm) {
	const onload = (frm.doc.__onload || {}).sf_master_rules;
	if (!frm.is_new() && onload) return Promise.resolve(onload);
	return frappe
		.call({
			method: "sf_trading.party_documents.get_master_rules",
			args: {
				doctype: frm.doctype,
				name: frm.is_new() ? null : frm.doc.name,
				customer_vat: frm.doc.custom_vat_registration_number || null,
			},
		})
		.then((r) => (r && r.message) || {});
}

function sf_party_mark_required(frm) {
	sf_party_rules_apply(frm).then((rules) => {
		const required = rules.applies ? 1 : 0;
		frm.toggle_reqd("payment_terms", required);
		if (frm.fields_dict.custom_supporting_documents) {
			frm.set_df_property("custom_supporting_documents", "reqd", required);
		}
		const notes = [];
		if ((rules.missing || []).length) {
			notes.push(__("Still needed: {0}.", [rules.missing.join(", ")]));
		}
		if ((rules.expired || []).length) {
			notes.push(__("Expired, blocking invoices and payments: {0}.", [rules.expired.join(", ")]));
		}
		if (notes.length && !frm.is_new()) {
			frm.dashboard.add_comment(notes.join(" "), (rules.expired || []).length ? "red" : "orange", true);
		}
	});
}

["Customer", "Supplier"].forEach((doctype) => {
	frappe.ui.form.on(doctype, {
		setup(frm) {
			frm.set_query("document_type", "custom_supporting_documents", () => ({
				filters: {
					disabled: 0,
					applies_to: ["in", ["Customer and Supplier", doctype]],
				},
			}));
		},
		refresh(frm) {
			sf_party_mark_required(frm);
		},
		custom_vat_registration_number(frm) {
			// a customer becomes B2B -- and governed -- the moment a VAT number is entered
			if (frm.is_new()) sf_party_mark_required(frm);
		},
	});
});

frappe.ui.form.on("Customer Supporting Document", {
	document_type(frm, cdt, cdn) {
		const row = locals[cdt][cdn];
		if (!row.document_type) return;
		frappe.call({
			method: "sf_trading.party_documents.get_document_type_defaults",
			args: { document_type: row.document_type },
			callback(r) {
				const defaults = (r && r.message) || {};
				["validate_expiry", "days_ahead", "grace_days", "financial_implication"].forEach((field) => {
					if (field in defaults) frappe.model.set_value(cdt, cdn, field, defaults[field] || 0);
				});
				if (defaults.needs_date_of_birth) {
					frappe.show_alert({ message: __("{0} needs the holder's Date of Birth.", [row.document_type]), indicator: "blue" });
				}
			},
		});
	},
	validate_expiry(frm, cdt, cdn) {
		if (!locals[cdt][cdn].validate_expiry) frappe.model.set_value(cdt, cdn, "financial_implication", 0);
	},
});
}
