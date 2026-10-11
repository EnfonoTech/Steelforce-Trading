// sf_trading/public/js/party_documents.js
// Customer and Supplier forms: the Supporting Documents grid and the new-master rules
// (sf_trading/party_documents.py).
//
//   * Picking a Document Name fills the row from its Supporting Document Type: Validate, Days
//     Ahead, Grace Days, Financial Implication.
//   * On a master the rules govern (every supplier, a credit customer, created on or after SF Trading
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
				is_credit: (frm.doc.credit_limits || []).some((row) => flt(row.credit_limit) > 0) ? 1 : 0,
			},
		})
		.then((r) => (r && r.message) || {});
}

function sf_party_mark_required(frm, quiet) {
	sf_party_rules_apply(frm).then((rules) => {
		const required = rules.applies ? 1 : 0;
		// Payment Terms: every credit customer, old or new (sales_order_governance), as well as a
		// governed new master
		const credit = frm.doctype === "Customer" && (frm.doc.credit_limits || []).some((row) => flt(row.credit_limit) > 0);
		frm.toggle_reqd("payment_terms", required || credit ? 1 : 0);
		if (quiet) return;
		if (frm.fields_dict.custom_supporting_documents) {
			// a Company supplier / a VAT-registered customer needs one whatever its age
			frm.set_df_property("custom_supporting_documents", "reqd", required || rules.needs_document ? 1 : 0);
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
	});
});

// a customer becomes a credit customer -- and governed -- the moment a credit limit is entered
// (Customer Group carries the same child table: only a Customer form asks)
frappe.ui.form.on("Customer Credit Limit", {
	credit_limit(frm) {
		if (frm.doctype === "Customer") sf_party_mark_required(frm, !frm.is_new());
	},
	credit_limits_remove(frm) {
		if (frm.doctype === "Customer") sf_party_mark_required(frm, !frm.is_new());
	},
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
