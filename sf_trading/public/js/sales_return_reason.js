// "Return Reason Template" (custom_return_reason_template, Link to Sales Return Reason) is required
// on a return -- its Custom Field's mandatory_depends_on marks it, and
// sf_trading.sales_return_reason.require_reason_template is the server half. It is still a picker
// over the free text, not a replacement for it: Return/Debit Reason (custom_return_reason) is a pre-existing,
// independently-used free-text field (reporting, print, debit notes) and this never repurposes or
// replaces it. Picking a template APPENDS its label into whatever is already typed there, without
// disturbing it; picking a different template swaps the appended text for the new one; clearing
// the picker removes exactly what it added -- all client-side, before save, never a database
// write of its own. sf_trading.sales_return_reason.validate_return_reason (Sales Invoice validate)
// is the one rule this cannot skip: "Other" must be explained in the text itself, not just
// labelled.

const SF_OTHER_RETURN_REASON = "Other";

function sf_seed_return_reason_suffix(frm) {
	// A reload restores both fields from the database but not this session's own in-memory
	// bookkeeping -- reconstruct it so a later deselect on THIS load still removes the right text.
	if (frm._sf_return_reason_suffix !== undefined) return;

	const picked = frm.doc.custom_return_reason_template;
	const text = frm.doc.custom_return_reason || "";

	if (!picked || !text.endsWith(picked)) {
		frm._sf_return_reason_suffix = "";
		return;
	}

	const before = text.slice(0, text.length - picked.length);
	frm._sf_return_reason_suffix = before.endsWith(" ") ? " " + picked : picked;
}

function sf_focus_return_reason(frm) {
	frm.scroll_to_field("custom_return_reason");
	// the field's own section may still be expanding when this runs
	setTimeout(() => {
		const control = frm.fields_dict.custom_return_reason;
		if (control && control.$input) {
			control.$input.trigger("focus");
		}
	}, 300);
}

function sf_apply_return_reason_template(frm) {
	const prev = frm._sf_return_reason_suffix || "";
	let text = frm.doc.custom_return_reason || "";

	if (prev && text.endsWith(prev)) {
		text = text.slice(0, text.length - prev.length);
	}

	const picked = frm.doc.custom_return_reason_template;
	if (picked) {
		const suffix = (text ? " " : "") + picked;
		text = text + suffix;
		frm._sf_return_reason_suffix = suffix;
	} else {
		frm._sf_return_reason_suffix = "";
	}

	frm.set_value("custom_return_reason", text);

	if (picked === SF_OTHER_RETURN_REASON) {
		sf_focus_return_reason(frm);
	}
}

frappe.ui.form.on("Sales Invoice", {
	refresh(frm) {
		sf_seed_return_reason_suffix(frm);
	},

	custom_return_reason_template(frm) {
		sf_apply_return_reason_template(frm);
	},
});
