// sf_trading/public/js/customer_quick_edit.js
// Quick-edit provision for a customer's own billing-blocking fields -- phone via Contact, a
// second number for B2B, CR/VAT for B2B -- from two launch points: the Sales Invoice Customer
// field (without leaving the draft invoice) and the Customer master itself (2026-09-26: added so
// the same convenience -- fixing phone/address without navigating to a separate Contact/Address
// record -- is available while looking at the customer record directly, not only mid-invoice).
// Backed by sf_trading.api.customer_quick_edit; the dialog reads the SAME completeness checks the
// billing gates call (missing_company_fields, missing_credit_customer_requirements,
// missing_b2b_phone_requirements, party_phone_numbers, and now party_completeness.is_b2b_customer
// for "is this B2B"), so this and those gates never disagree about what "complete" means.
//
// An attachment IS editable here (2026-09-26, second pass): both launch points only ever open
// this dialog against an ALREADY-SAVED Customer, so a bare Attach control's upload -- which lands
// unassociated in the File doctype, with no live frm to bind it to attached_to_doctype/name --
// gets explicitly linked to that Customer server-side (sf_trading.api.customer_quick_edit.
// _link_attachment), before the CR/VAT save, so customer_override.validate's own VAT-needs-a-
// document rule sees it in the same request.

frappe.ui.form.on("Sales Invoice", {
	refresh: add_sales_invoice_quick_edit_button,
	customer: add_sales_invoice_quick_edit_button,
});

frappe.ui.form.on("Customer", {
	refresh: add_customer_master_quick_edit_button,
});

function add_sales_invoice_quick_edit_button(frm) {
	frm.fields_dict.customer.$wrapper.find(".sf-customer-quick-edit").remove();

	if (!frm.doc.customer) {
		return;
	}

	const $btn = $(
		'<button type="button" class="btn btn-xs btn-default sf-customer-quick-edit" ' +
			'title="' + __("Fix this customer's details") + '" style="margin-top: 4px;">' +
			'<i class="fa fa-pencil"></i> ' + __("Quick Edit") +
			"</button>"
	);
	// A Sales Invoice's own modified timestamp is untouched by editing a DIFFERENT record
	// (Customer) behind the scenes, so no reload is needed here the way the Customer master
	// launch point below needs one.
	$btn.on("click", () => open_customer_quick_edit_dialog(frm.doc.customer));
	frm.fields_dict.customer.$wrapper.append($btn);
}

function add_customer_master_quick_edit_button(frm) {
	if (frm.is_new()) {
		return; // nothing to link a Contact/Address against yet
	}

	frm.add_custom_button(__("Quick Edit Billing Fields"), () => {
		// The dialog's own save writes to THIS SAME Customer record via a separate server call,
		// bumping `modified` behind the currently-open form's back -- reload_doc() afterwards is
		// what keeps the next ordinary Save on this form from hitting a stale-timestamp conflict
		// ("Document has been modified after you have opened it"), the exact trap this account's
		// own coding standard already calls out for db_set-style writes to an in-memory doc.
		open_customer_quick_edit_dialog(frm.doc.name, () => frm.reload_doc());
	});
}

function open_customer_quick_edit_dialog(customer, on_saved) {
	frappe.call({
		method: "sf_trading.api.customer_quick_edit.get_quick_edit_data",
		args: { customer: customer },
		freeze: true,
		callback: (r) => {
			if (r.message) {
				render_quick_edit_dialog(customer, r.message, on_saved);
			}
		},
	});
}

function render_quick_edit_dialog(customer, data, on_saved) {
	// Two DIFFERENT flags (2026-09-26, second correction): missing_company_fields (GS Issue 1) and
	// missing_b2b_phone_requirements now BOTH gate on party_completeness.is_b2b_customer (VAT on
	// file) -- they agree. is_company here is field-VISIBILITY only (customer_type=="Company"), kept
	// independent so a Company customer with no VAT yet still sees the CR/VAT inputs and can fill
	// VAT in to become B2B; is_b2b drives the 2nd mobile number requirement. A Company customer with
	// no VAT yet is is_company=true / is_b2b=false -- CR/VAT fields show (nothing blocking yet, but
	// the fields are there to fill in), 2nd phone does not (that rule hasn't engaged either).
	const is_company = !!data.is_company;
	const is_b2b = !!data.is_b2b;
	const missing = data.missing || {};
	const all_missing = [].concat(
		missing.company_fields || [],
		missing.credit_customer || [],
		missing.b2b_phone || [],
		missing.any_phone || []
	);

	const fields = [
		{
			fieldtype: "HTML",
			options: all_missing.length
				? '<div class="alert alert-warning">' +
					__("Currently blocking billing: {0}", [all_missing.join(", ")]) +
					"</div>"
				: '<div class="alert alert-success">' + __("Nothing currently blocking billing.") + "</div>",
		},
		{ fieldname: "phone_1", fieldtype: "Data", label: __("Mobile No (1)"), default: data.phone_1 },
	];

	if (is_b2b) {
		fields.push({
			fieldname: "phone_2",
			fieldtype: "Data",
			label: __("Mobile No (2) -- required for a B2B customer"),
			default: data.phone_2,
		});
	}

	fields.push(
		{ fieldtype: "Column Break" },
		{ fieldname: "address_line1", fieldtype: "Data", label: __("Address Line 1"), default: data.address_line1 },
		{ fieldname: "address_city", fieldtype: "Data", label: __("City"), default: data.address_city }
	);

	if (is_company) {
		fields.push(
			{ fieldtype: "Section Break", label: __("Company (CR / VAT)") },
			{
				fieldname: "custom_vat_registration_number",
				fieldtype: "Data",
				label: __("VAT Registration Number"),
				default: data.custom_vat_registration_number,
			},
			{ fieldtype: "Column Break" },
			{
				fieldname: "custom_commercial_registration_number",
				fieldtype: "Data",
				label: __("Commercial Registration Number"),
				default: data.custom_commercial_registration_number,
			}
		);
	}

	// Shown whenever nothing is attached yet -- covers BOTH missing_credit_customer_requirements'
	// own attachment rule (GS Issue 13) and customer_override.validate's separate "a VAT number
	// needs a document" rule, since both simply check whether any File is attached to this
	// Customer. One upload here satisfies whichever (or both) of them applied.
	if (!data.has_attachment) {
		fields.push(
			{ fieldtype: "Section Break", label: __("Attachment") },
			{
				fieldname: "attachment",
				fieldtype: "Attach",
				label: __("Attachment (e.g. CR / VAT copy)"),
				description: __("Needed if this customer is a credit customer, or has a VAT Registration Number."),
			}
		);
	}

	const dialog = new frappe.ui.Dialog({
		title: __("Quick Edit: {0}", [data.customer_name]),
		fields: fields,
		primary_action_label: __("Save"),
		primary_action: (values) => {
			frappe.call({
				method: "sf_trading.api.customer_quick_edit.save_quick_edit_data",
				args: { customer: customer, values: values },
				freeze: true,
				callback: (r) => {
					dialog.hide();
					if (r.message && r.message.warnings && r.message.warnings.length) {
						frappe.msgprint({
							title: __("Saved, with a note"),
							indicator: "orange",
							message: r.message.warnings.join("<br>"),
						});
					} else {
						frappe.show_alert({ message: __("Customer details updated"), indicator: "green" });
					}
					if (on_saved) {
						on_saved();
					}
				},
			});
		},
	});

	dialog.show();
}
