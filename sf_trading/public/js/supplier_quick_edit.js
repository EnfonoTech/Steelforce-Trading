// sf_trading/public/js/supplier_quick_edit.js
// Quick Edit Billing Fields for a supplier (sf_trading/api/supplier_quick_edit.py), the twin of the
// customer one: a "Quick Edit" button under the Supplier field of a Purchase Order, Purchase
// Invoice and Purchase Receipt, and "Quick Edit Billing Fields" on the Supplier master. The banner
// lists what stops this supplier being bought from or paid -- read from the same rules the gates
// run, for an existing supplier as much as a new one -- and the dialog fixes it in place: mobile,
// email, address, Tax ID, Payment Terms and a supporting document (which becomes a row of the
// supplier's Supporting Documents grid).

["Purchase Order", "Purchase Invoice", "Purchase Receipt"].forEach((doctype) => {
	frappe.ui.form.on(doctype, {
		refresh: sf_add_supplier_quick_edit_button,
		supplier: sf_add_supplier_quick_edit_button,
	});
});

frappe.ui.form.on("Supplier", {
	refresh(frm) {
		if (frm.is_new()) return;
		frm.add_custom_button(__("Quick Edit Billing Fields"), () => {
			// the dialog saves this same Supplier behind the open form; reload so the next Save
			// does not meet a stale timestamp
			sf_open_supplier_quick_edit(frm.doc.name, () => frm.reload_doc());
		});
	},
});

function sf_add_supplier_quick_edit_button(frm) {
	const field = frm.fields_dict.supplier;
	if (!field) return;
	field.$wrapper.find(".sf-supplier-quick-edit").remove();
	if (!frm.doc.supplier) return;
	const $btn = $(
		'<button type="button" class="btn btn-xs btn-default sf-supplier-quick-edit" ' +
			'title="' + __("Fix this supplier's details") + '" style="margin-top: 4px;">' +
			'<i class="fa fa-pencil"></i> ' + __("Quick Edit") +
			"</button>"
	);
	$btn.on("click", () => sf_open_supplier_quick_edit(frm.doc.supplier));
	field.$wrapper.append($btn);
}

function sf_open_supplier_quick_edit(supplier, on_saved) {
	frappe.call({
		method: "sf_trading.api.supplier_quick_edit.get_quick_edit_data",
		args: { supplier: supplier },
		freeze: true,
		callback: (r) => {
			if (r.message) sf_render_supplier_quick_edit(supplier, r.message, on_saved);
		},
	});
}

function sf_render_supplier_quick_edit(supplier, data, on_saved) {
	const blocking = [].concat(data.blocking || [], data.expired || []);
	const fields = [
		{
			fieldtype: "HTML",
			options: blocking.length
				? '<div class="alert alert-warning">' +
					__("Currently blocking purchases and payments: {0}", [blocking.join(", ")]) +
					"</div>"
				: '<div class="alert alert-success">' + __("Nothing currently blocking purchases or payments.") + "</div>",
		},
		// a new supplier needs a mobile (party_documents); with Enforce Supplier Completeness on, every
		// supplier needs a phone and an email (supplier_validation)
		{ fieldname: "mobile", fieldtype: "Data", label: __("Mobile Number"), default: data.mobile, reqd: data.rules_apply || data.enforced ? 1 : 0 },
		{ fieldname: "email", fieldtype: "Data", options: "Email", label: __("Email"), default: data.email, reqd: data.enforced ? 1 : 0 },
		{ fieldtype: "Column Break" },
		{ fieldname: "address_line1", fieldtype: "Data", label: __("Address Line 1"), default: data.address_line1 },
		{ fieldname: "address_city", fieldtype: "Data", label: __("City"), default: data.address_city },
		{ fieldtype: "Section Break" },
		{
			fieldname: "tax_id",
			fieldtype: "Data",
			label: __("Tax ID"),
			default: data.tax_id,
			reqd: data.is_company ? 1 : 0,
		},
		{ fieldtype: "Column Break" },
		{
			fieldname: "payment_terms",
			fieldtype: "Link",
			options: "Payment Terms Template",
			label: __("Payment Terms"),
			default: data.payment_terms,
			// a new supplier must carry them (sf_trading/party_documents.py)
			reqd: data.rules_apply ? 1 : 0,
		},
	];

	// a supporting document: required while the rules still want one; offered otherwise, so an
	// expired one can be replaced
	const needs = !!data.needs_document;
	fields.push(
		{ fieldtype: "Section Break", label: __("Supporting Document") },
		{
			fieldname: "attachment",
			fieldtype: "Attach",
			label: __("Document Attachment"),
			reqd: needs ? 1 : 0,
			description: data.has_document_row
				? __("Already on file. Attach another only to add or replace a document.")
				: __("Becomes a row of the supplier's Supporting Documents."),
		},
		{ fieldtype: "Column Break" },
		{
			fieldname: "document_type",
			fieldtype: "Link",
			options: "Supporting Document Type",
			label: __("Document Name"),
			default: "Commercial Registration",
			depends_on: "eval:doc.attachment",
			get_query: () => ({ filters: { disabled: 0, applies_to: ["in", ["Customer and Supplier", "Supplier"]] } }),
		},
		{ fieldname: "document_number", fieldtype: "Data", label: __("Document #"), depends_on: "eval:doc.attachment" },
		{
			fieldname: "expiry_date",
			fieldtype: "Date",
			label: __("Expiry Date"),
			depends_on: "eval:doc.attachment",
			// the standard types that validate their expiry (party_documents.DOCUMENT_TYPES)
			mandatory_depends_on: "eval:doc.attachment && ['Commercial Registration', 'Trade License', 'National ID / CPR', 'Passport', 'Contract / Agreement'].includes(doc.document_type)",
		}
	);

	const d = new frappe.ui.Dialog({
		title: __("Quick Edit: {0}", [data.supplier_name || supplier]),
		size: "large",
		fields: fields,
		primary_action_label: __("Save"),
		primary_action(values) {
			frappe.call({
				method: "sf_trading.api.supplier_quick_edit.save_quick_edit_data",
				args: { supplier: supplier, values: values },
				freeze: true,
				freeze_message: __("Saving..."),
				callback: (r) => {
					const result = r.message || {};
					d.hide();
					if ((result.warnings || []).length) {
						frappe.msgprint({
							title: __("Saved, but the supplier still needs attention"),
							indicator: "orange",
							message: result.warnings.join("<br>"),
						});
					} else {
						frappe.show_alert({ message: __("Supplier details saved"), indicator: "green" }, 5);
					}
					if (on_saved) on_saved();
				},
			});
		},
	});
	d.show();
}
