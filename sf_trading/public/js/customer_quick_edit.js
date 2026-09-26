// sf_trading/public/js/customer_quick_edit.js
// Quick-edit provision on the Sales Invoice Customer field (client ask, 2026-09-26): fix exactly
// the fields that gate billing -- phone via Contact, a second number for B2B, CR/VAT for B2B --
// without leaving the draft invoice. Backed by sf_trading.api.customer_quick_edit; the dialog
// reads the SAME completeness checks the billing gates call (missing_company_fields,
// missing_credit_customer_requirements, missing_b2b_phone_requirements, party_phone_numbers), so
// this and those gates never disagree about what "complete" means.
//
// An attachment (GS Issue 13, credit customers) is NOT editable here -- Frappe's own Attach
// fieldtype uploads straight to the File doctype with no attached_to_name until the parent is
// saved, and re-parenting it correctly from a dialog on a DIFFERENT document (Sales Invoice) is
// more moving parts than this pass buys; the dialog names it as still missing and points at the
// Customer record's own Attachments panel instead.

frappe.ui.form.on("Sales Invoice", {
	refresh: add_customer_quick_edit_button,
	customer: add_customer_quick_edit_button,
});

function add_customer_quick_edit_button(frm) {
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
	$btn.on("click", () => open_customer_quick_edit_dialog(frm));
	frm.fields_dict.customer.$wrapper.append($btn);
}

function open_customer_quick_edit_dialog(frm) {
	frappe.call({
		method: "sf_trading.api.customer_quick_edit.get_quick_edit_data",
		args: { customer: frm.doc.customer },
		freeze: true,
		callback: (r) => {
			if (r.message) {
				render_quick_edit_dialog(frm, r.message);
			}
		},
	});
}

function render_quick_edit_dialog(frm, data) {
	const is_company = !!data.is_company;
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

	if (is_company) {
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
			{ fieldtype: "Section Break", label: __("B2B (Company)") },
			{
				fieldname: "custom_commercial_registration_number",
				fieldtype: "Data",
				label: __("Commercial Registration Number"),
				default: data.custom_commercial_registration_number,
			},
			{ fieldtype: "Column Break" },
			{
				fieldname: "custom_vat_registration_number",
				fieldtype: "Data",
				label: __("VAT Registration Number"),
				default: data.custom_vat_registration_number,
			}
		);
	}

	if ((missing.credit_customer || []).some((m) => m.indexOf("attachment") !== -1)) {
		fields.push(
			{ fieldtype: "Section Break", label: __("Credit Customer") },
			{
				fieldname: "attachment_note",
				fieldtype: "HTML",
				options:
					'<p class="text-muted">' +
					__(
						"This credit customer still needs at least one attachment (e.g. CR copy). Add it from the Customer record's own Attachments panel -- not editable from this dialog."
					) +
					"</p>",
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
				args: { customer: frm.doc.customer, values: values },
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
				},
			});
		},
	});

	dialog.show();
}
