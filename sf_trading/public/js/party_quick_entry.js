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
//   * a B2B customer (VAT Registration Number entered) also needs Commercial Registration and
//     Payment Terms.
// Marks on fields the doctype itself has (Payment Terms, Tax ID, CR, Country) are set on the dialog
// after it renders: the dialog swaps each such field's definition for the document's own, so a
// changed copy passed in beforehand is lost. The server enforces Payment Terms, Tax ID, CR and the
// mobile number (sf_trading/party_documents.py, party_completeness.py).

frappe.provide("frappe.ui.form");

(function () {
	const Base = frappe.ui.form.ContactAddressQuickEntryForm;
	if (!Base) return;

	const ADDRESS_PICKERS = ["supplier_primary_address", "customer_primary_address", "primary_address"];
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
				mark("payment_terms", "mandatory_depends_on", B2B);
				mark("custom_commercial_registration_number", "mandatory_depends_on", B2B);
			}
			dialog.refresh_dependency();
		}

		get_variant_fields() {
			const is_supplier = this.doctype === "Supplier";
			const required = is_supplier
				? ["mobile_number", "address_line1", "city", "country"]
				: ["mobile_number"];
			return super.get_variant_fields().map((df) =>
				required.includes(df.fieldname) ? Object.assign({}, df, { reqd: 1 }) : df
			);
		}

		insert() {
			if (this.doctype === "Supplier" && this.dialog.doc.mobile_number) {
				this.dialog.doc.custom_mobile_no = this.dialog.doc.mobile_number;
			}
			return super.insert();
		}
	}

	frappe.ui.form.SupplierQuickEntryForm = SFPartyQuickEntryForm;
	frappe.ui.form.CustomerQuickEntryForm = SFPartyQuickEntryForm;
})();
