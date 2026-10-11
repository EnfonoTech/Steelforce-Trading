// sf_trading/public/js/party_quick_entry.js
// The Customer and Supplier quick entry (the "+ Add" dialog), on top of ERPNext's
// ContactAddressQuickEntryForm (erpnext/public/js/utils/contact_address_quick_entry.js).
//
// Supplier:
//   * no "Supplier Primary Address" picker -- a new supplier has no address to pick yet; the
//     address is typed in Primary Address Details below, and ERPNext creates it on save. Line 1,
//     City and Country are required there.
//   * Mobile Number is required, and also fills the master's own Mobile No (custom_mobile_no).
//   * Payment Terms is required (sf_trading/party_documents.py); Tax ID is required for a Company.
// Customer:
//   * Mobile Number is required -- a customer without a phone cannot be billed.
//   * a VAT-registered customer also needs Commercial Registration and a document attached.
//     Payment Terms belong to credit customers (a credit limit, set on the master later).
// Both: the Supporting Document section's upload becomes the master's first Supporting Documents
// row; it is required for a VAT-registered customer and a Company supplier.
// Marks on fields the doctype itself has (Payment Terms, Tax ID, CR, Country) are set on the dialog
// after it renders: the dialog swaps each such field's definition for the document's own, so a
// changed copy passed in beforehand is lost. The server enforces Payment Terms, Tax ID, CR and the
// mobile number (sf_trading/party_documents.py, party_completeness.py).

frappe.provide("frappe.ui.form");

// The VAT Registration Number (a supplier's Tax ID) is a supporting document of its own. In every
// dialog that takes an upload -- this quick entry, Create Customer / Supplier, both Quick Edit
// dialogs -- Document Name defaults to the VAT Certificate type and Document # to the number typed
// above, until the user picks something else; the master then holds a Supporting Documents row for
// the number (sf_trading/party_documents.py ensure_vat_row). `cfg` names the dialog's own fields:
// {vat, type, number}; `source` says which of them just changed.
// Supplier Groups whose suppliers hold no document: mirror of party_documents.DOCUMENT_EXEMPT_SUPPLIER_GROUPS
window.SF_DOCUMENT_EXEMPT_SUPPLIER_GROUPS = ["other supplier"];

window.sf_vat_document_sync = function (d, cfg, source) {
	const VAT_TYPE = "VAT Certificate";
	if (!d || !d.get_value || !d.fields_dict || !d.fields_dict[cfg.type] || !d.fields_dict[cfg.number]) return;
	const vat = String(d.get_value(cfg.vat) || "").trim();
	const type = d.get_value(cfg.type) || "";
	const number = String(d.get_value(cfg.number) || "").trim();
	const fill_number = () => {
		// only a blank number, or one this helper wrote for an earlier version of the VAT number
		if (vat && vat !== "0" && (!number || number === d._sf_vat_auto_number)) {
			d._sf_vat_auto_number = vat;
			d.set_value(cfg.number, vat);
		}
	};
	if (source === "type") {
		// a type picked by hand stays; the one set below does not count as picked
		if (d._sf_vat_auto_type === type) d._sf_vat_auto_type = null;
		else d._sf_vat_type_chosen = true;
		if (type === VAT_TYPE) fill_number();
		else if (number && number === d._sf_vat_auto_number) {
			// another document is not numbered with the VAT number this helper wrote
			d._sf_vat_auto_number = "";
			d.set_value(cfg.number, "");
		}
		return;
	}
	if (!vat || vat === "0") return;
	if (!d._sf_vat_type_chosen && type !== VAT_TYPE) {
		d._sf_vat_auto_type = VAT_TYPE;
		d.set_value(cfg.type, VAT_TYPE);
		fill_number();
	} else if (type === VAT_TYPE) {
		fill_number();
	}
};

(function () {
	const Base = frappe.ui.form.ContactAddressQuickEntryForm;
	if (!Base) return;

	const ADDRESS_PICKERS = ["supplier_primary_address", "customer_primary_address", "primary_address"];
	const SF_DEFAULT_DOCUMENT = { Customer: "VAT Certificate", Supplier: "Commercial Registration" };
	const B2B = "eval:doc.custom_vat_registration_number";

	class SFPartyQuickEntryForm extends Base {
		render_dialog() {
			const is_supplier = this.doctype === "Supplier";
			this.mandatory = (this.mandatory || [])
				.filter((df) => !ADDRESS_PICKERS.includes(df.fieldname))
				// the dialog's own Mobile Number below fills this one
				.filter((df) => !(is_supplier && df.fieldname === "custom_mobile_no"));
			super.render_dialog();
			this.sf_mark_required(is_supplier);
		}

		sf_mark_required(is_supplier) {
			const dialog = this.dialog;
			if (!dialog) return;
			const mark = (fieldname, prop, value) => {
				if (dialog.fields_dict[fieldname]) dialog.set_df_property(fieldname, prop, value);
			};
			if (is_supplier) {
				mark("payment_terms", "reqd", 1);
				mark("tax_id", "mandatory_depends_on", "eval:doc.supplier_type=='Company'");
				mark("country", "reqd", 1);
			} else {
				// Payment Terms follow credit status (party_documents.is_governed), which the quick entry
				// never sets: only CR is asked of a VAT-registered customer here
				mark("custom_commercial_registration_number", "mandatory_depends_on", B2B);
			}
			dialog.refresh_dependency();
			// the number typed here defaults the document below (sf_vat_document_sync)
			const vat_field = is_supplier ? "tax_id" : "custom_vat_registration_number";
			const cfg = { vat: vat_field, type: "sf_document_type", number: "sf_document_number" };
			if (dialog.fields_dict[vat_field]) {
				dialog.fields_dict[vat_field].df.onchange = () => window.sf_vat_document_sync(dialog, cfg, "vat");
			}
		}

		get_variant_fields() {
			const is_supplier = this.doctype === "Supplier";
			const required = is_supplier
				? ["mobile_number", "address_line1", "city", "country"]
				: ["mobile_number"];
			const fields = super.get_variant_fields().map((df) =>
				required.includes(df.fieldname) ? Object.assign({}, df, { reqd: 1 }) : df
			);
			const doctype = this.doctype;
			return fields.concat([
				{ fieldtype: "Section Break", label: __("Supporting Document") },
				{
					fieldname: "sf_document_attachment",
					fieldtype: "Attach",
					label: __("Document Attachment"),
					mandatory_depends_on: is_supplier
						? "eval:doc.supplier_type=='Company' && !(window.SF_DOCUMENT_EXEMPT_SUPPLIER_GROUPS || []).includes(String(doc.supplier_group || '').trim().toLowerCase())"
						: "eval:doc.custom_vat_registration_number",
					description: is_supplier
						? __("Required for a Company supplier, except in the Other Supplier group.")
						: __("Required when a VAT Registration Number is entered."),
				},
				{ fieldtype: "Column Break" },
				{
					fieldname: "sf_document_type",
					fieldtype: "Link",
					options: "Supporting Document Type",
					label: __("Document Name"),
					default: SF_DEFAULT_DOCUMENT[doctype],
					depends_on: "eval:doc.sf_document_attachment",
					onchange: () => window.sf_vat_document_sync(this.dialog, {
						vat: is_supplier ? "tax_id" : "custom_vat_registration_number",
						type: "sf_document_type",
						number: "sf_document_number",
					}, "type"),
					get_query: () => ({ filters: { disabled: 0, applies_to: ["in", ["Customer and Supplier", doctype]] } }),
				},
				{ fieldname: "sf_document_number", fieldtype: "Data", label: __("Document #"), depends_on: "eval:doc.sf_document_attachment" },
				{
					fieldname: "sf_expiry_date",
					fieldtype: "Date",
					label: __("Expiry Date"),
					depends_on: "eval:doc.sf_document_attachment",
					// the standard types that validate their expiry (party_documents.DOCUMENT_TYPES); the
					// server holds any other type to its own setting
					mandatory_depends_on: "eval:doc.sf_document_attachment && ['Commercial Registration', 'Trade License', 'National ID / CPR', 'Passport', 'Contract / Agreement'].includes(doc.sf_document_type)",
				},
			]);
		}

		insert() {
			const doc = this.dialog.doc;
			if (this.doctype === "Supplier" && doc.mobile_number) {
				doc.custom_mobile_no = doc.mobile_number;
			}
			const file = doc.sf_document_attachment;
			if (!file) return super.insert();
			// the upload becomes the first Supporting Documents row, carrying its type's rules; the
			// file itself is tied to the new master on save (party_documents.relink_attachments)
			const type = doc.sf_document_type || SF_DEFAULT_DOCUMENT[this.doctype];
			return frappe
				.xcall("sf_trading.party_documents.get_document_type_defaults", { document_type: type })
				.then((d) => {
					d = d || {};
					doc.custom_supporting_documents = [
						{
							document_type: type,
							document_number: doc.sf_document_number || null,
							expiry_date: doc.sf_expiry_date || null,
							attachment: file,
							validate_expiry: d.validate_expiry || 0,
							days_ahead: d.days_ahead || 0,
							grace_days: d.grace_days || 0,
							financial_implication: d.validate_expiry ? d.financial_implication || 0 : 0,
						},
					];
					return super.insert();
				});
		}
	}

	frappe.ui.form.SupplierQuickEntryForm = SFPartyQuickEntryForm;
	frappe.ui.form.CustomerQuickEntryForm = SFPartyQuickEntryForm;
})();
