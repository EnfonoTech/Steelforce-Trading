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
// plus COMPLETENESS_EXEMPT_SUPPLIER_GROUPS (Govt, W/O VAT: no Payment Terms, document or Tax ID either)
window.SF_DOCUMENT_EXEMPT_SUPPLIER_GROUPS = ["other supplier", "govt", "w/o vat"];
window.SF_COMPLETENESS_EXEMPT_SUPPLIER_GROUPS = ["govt", "w/o vat"];
window.SF_NOT_COMPLETENESS_EXEMPT =
	"!(window.SF_COMPLETENESS_EXEMPT_SUPPLIER_GROUPS || []).includes(String(doc.supplier_group || '').trim().toLowerCase())";

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
		// a type picked by hand stays. Not picked: the one set below, and the dialog's own default
		// (which fires this when the dialog is built)
		if (d._sf_vat_auto_type === type) d._sf_vat_auto_type = null;
		else if (type !== (d.fields_dict[cfg.type].df.default || "") || d._sf_vat_switched) d._sf_vat_type_chosen = true;
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
		d._sf_vat_switched = true;
		d.set_value(cfg.type, VAT_TYPE);
		fill_number();
	} else if (type === VAT_TYPE) {
		fill_number();
	}
};

// A new Customer's Customer Group follows its type: the group named after the type (Company,
// Individual) goes into `group_field` of `dialog` when the site has one. A group picked by hand stays,
// and a type with no group of its name (Partnership) takes back only what an earlier type put there.
// Used by this quick entry and by Create New Customer (create_customer.js). Resolves true when it set
// a group.
window.sf_customer_group_for_type = function (dialog, group_field, type) {
	if (!dialog || !dialog.fields_dict || !dialog.fields_dict[group_field] || !type) {
		return Promise.resolve(false);
	}
	return frappe.db.get_value("Customer Group", type, "is_group").then((r) => {
		const row = (r && r.message) || {};
		const current = dialog.get_value(group_field);
		const ours = !!current && current === dialog._sf_group_auto;
		if (row.is_group === undefined || cint(row.is_group)) {
			if (ours) {
				dialog._sf_group_auto = null;
				dialog.set_value(group_field, "");
			}
			return false;
		}
		if (current && !ours) return false;
		dialog._sf_group_auto = type;
		dialog.set_value(group_field, type);
		return true;
	});
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
			// a new Customer only: quick edit opens a master that already has its group
			const add_group = !is_supplier && this.doc && this.doc.__islocal;
			if (add_group) this.sf_add_customer_group();
			super.render_dialog();
			this.sf_mark_required(is_supplier);
			if (add_group) this.sf_default_customer_group();
		}

		// A new Customer carries the site's default Customer Group, and the dialog drew no field for
		// it (the doctype does not require one), so the default was saved as it stood. Here that default
		// is the root, "All Customer Groups" (Selling Settings), a group node core refuses
		// (Customer.validate_customer_group: "Invalid Customer Group"). The full form never gets there:
		// the field's link filter hides group nodes and the user picks one. So the dialog asks too,
		// leaf nodes only. The field is an alias, like the contact fields in get_variant_fields: the
		// dialog swaps a field it shares with the doctype for the doctype's own definition, which would
		// drop the required mark.
		sf_add_customer_group() {
			const at = this.mandatory.findIndex((df) => df.fieldname === "customer_type");
			this.mandatory.splice(at + 1, 0, {
				fieldname: "sf_customer_group",
				fieldtype: "Link",
				options: "Customer Group",
				label: __("Customer Group"),
				reqd: 1,
				get_query: () => ({ filters: { is_group: 0 } }),
			});
		}

		// The group follows the customer type (Company, Individual), now and whenever the type is changed.
		// A type with no group of its name leaves the site default when that is already a leaf; a group
		// node (the root) is left for the user to pick.
		sf_default_customer_group() {
			const dialog = this.dialog;
			const for_type = () =>
				window.sf_customer_group_for_type(dialog, "sf_customer_group", dialog.get_value("customer_type"));
			// the dialog's own element: the field's definition is the doctype's, shared with the full form
			const type = dialog.fields_dict.customer_type;
			if (type && type.$input) type.$input.on("change", for_type);

			for_type().then((set) => {
				const group = this.doc.customer_group;
				if (set || !group || dialog.get_value("sf_customer_group")) return;
				frappe.db.get_value("Customer Group", group, "is_group").then((r) => {
					const is_group = cint(r && r.message && r.message.is_group);
					if (is_group || dialog.get_value("sf_customer_group")) return;
					// ours, so a later change of type may still replace it
					dialog._sf_group_auto = group;
					dialog.set_value("sf_customer_group", group);
				});
			});
		}

		sf_mark_required(is_supplier) {
			const dialog = this.dialog;
			if (!dialog) return;
			const mark = (fieldname, prop, value) => {
				if (dialog.fields_dict[fieldname]) dialog.set_df_property(fieldname, prop, value);
			};
			if (is_supplier) {
				mark("payment_terms", "mandatory_depends_on", "eval:" + SF_NOT_COMPLETENESS_EXEMPT);
				mark("tax_id", "mandatory_depends_on", "eval:doc.supplier_type=='Company' && " + SF_NOT_COMPLETENESS_EXEMPT);
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
				? ["mobile_number", "email_address", "address_line1", "city", "country"]
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
						? __("Required for a Company supplier, except in the Other Supplier, Govt and W/O VAT groups.")
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
			if (this.dialog.fields_dict.sf_customer_group) {
				// the picked leaf replaces the default the new document carried, the root group
				doc.customer_group = this.dialog.get_value("sf_customer_group") || null;
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
