// sf_trading/public/js/party_documents.js
// Customer and Supplier forms: the Supporting Documents grid and the new-master rules
// (sf_trading/party_documents.py).
//
//   * Picking a Document Name fills the row from its Supporting Document Type: Validate, Days
//     Ahead, Grace Days, Financial Implication.
//   * On a master the rules govern (every supplier, a credit customer, created on or after SF Trading
//     Settings > New-Master Rules From) Payment Terms and the documents grid are marked required,
//     and the form says what is still missing. The server enforces the same rules. Payment Terms are
//     also marked on every older supplier: buying from or paying one without them is refused.

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
		// Payment Terms: every credit customer, old or new (sales_order_governance), every supplier, old
		// or new (the server says so: party_documents.payment_terms_required), as well as a governed
		// new master
		const credit = frm.doctype === "Customer" && (frm.doc.credit_limits || []).some((row) => flt(row.credit_limit) > 0);
		// a Govt / W/O VAT supplier is asked for none of Payment Terms, a document or a Tax ID
		// (party_documents.COMPLETENESS_EXEMPT_SUPPLIER_GROUPS)
		const group = String(frm.doc.supplier_group || "").trim().toLowerCase();
		const fully_exempt = frm.doctype === "Supplier"
			&& (rules.completeness_exempt_groups || []).some((g) => g.toLowerCase() === group);
		frm.toggle_reqd("payment_terms", !fully_exempt && (required || credit || rules.payment_terms_required) ? 1 : 0);
		if (frm.fields_dict.custom_supporting_documents) {
			// a Company supplier / a VAT-registered customer needs one whatever its age -- unless its
			// Supplier Group is one that holds none (party_documents.DOCUMENT_EXEMPT_SUPPLIER_GROUPS)
			const exempt = frm.doctype === "Supplier"
				&& (rules.exempt_groups || []).some((g) => g.toLowerCase() === String(frm.doc.supplier_group || "").trim().toLowerCase());
			frm.set_df_property("custom_supporting_documents", "reqd", !exempt && (required || rules.needs_document) ? 1 : 0);
		}
		if (quiet) return; // a re-mark after an edit does not repeat the notes
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

const SF_VAT_TYPE = "VAT Certificate";
const SF_VAT_FIELD = { Customer: "custom_vat_registration_number", Supplier: "tax_id" };

function sf_party_vat_number(frm) {
	const vat = String(frm.doc[SF_VAT_FIELD[frm.doctype]] || "").trim();
	return vat === "0" ? "" : vat;
}

// the VAT Registration Number (a supplier's Tax ID) is a supporting document of its own: typing it
// adds a VAT Certificate row carrying it, its file still to attach (the server adds the same row on
// save -- party_documents.ensure_vat_row)
function sf_party_vat_row(frm) {
	const vat = sf_party_vat_number(frm);
	if (!vat || !frm.fields_dict.custom_supporting_documents) return;
	const rows = (frm.doc.custom_supporting_documents || []).filter((r) => r.document_type === SF_VAT_TYPE);
	if (rows.some((r) => String(r.document_number || "").trim() === vat)) return;
	const blank = rows.find((r) => !String(r.document_number || "").trim());
	if (blank) {
		frappe.model.set_value(blank.doctype, blank.name, "document_number", vat);
		return;
	}
	// a VAT row holding only a number (no file, dates or remarks) is retyped: the field fires on every
	// edit of the number, and each one used to add a row (22, 2200, 22000, ...)
	const loose = rows.find((r) => !["attachment", "issue_date", "expiry_date", "remarks", "date_of_birth"].some((f) => r[f]));
	if (loose) {
		frappe.model.set_value(loose.doctype, loose.name, "document_number", vat);
		return;
	}
	const row = frm.add_child("custom_supporting_documents");
	// setting the type fills Validate / Days Ahead / Grace Days from it (handler below)
	frappe.model.set_value(row.doctype, row.name, "document_type", SF_VAT_TYPE);
	frappe.model.set_value(row.doctype, row.name, "document_number", vat);
	frm.refresh_field("custom_supporting_documents");
}

["Customer", "Supplier"].forEach((doctype) => {
	frappe.ui.form.on(doctype, {
		[SF_VAT_FIELD[doctype]](frm) {
			sf_party_vat_row(frm);
		},
		// the exemption follows the group picked on the form
		supplier_group(frm) {
			if (frm.doctype === "Supplier") sf_party_mark_required(frm, true);
		},
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
		// the VAT Certificate carries the VAT number it certifies
		if (row.document_type === SF_VAT_TYPE && !String(row.document_number || "").trim() && sf_party_vat_number(frm)) {
			frappe.model.set_value(cdt, cdn, "document_number", sf_party_vat_number(frm));
		}
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
