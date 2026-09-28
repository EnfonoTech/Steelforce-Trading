// Copies the picked Sales Return Reason into core's own Remarks field, so every report and print
// format that already reads Remarks keeps finding a real answer there -- nothing else about
// Remarks changes, and it stays a plain free-text field the rest of the time.
//
// "Other" is the one reason that explains nothing on its own, so it is the one case Remarks is not
// auto-filled and not left alone either: it is cleared, made mandatory, and focused, so the return
// cannot be saved with the last reason's leftover text sitting under a reason that no longer
// matches it. The mandatory flag is UI only -- sf_trading.sales_return_reason.validate_return_reason
// (hooked on Sales Invoice validate) is the rule that actually holds if the browser is skipped.
//
// Reapplied on every refresh, not just on change: toggle_reqd only takes effect on the form that
// set it, so a return reopened with "Other" already saved needs the same mandatory flag put back.

const SF_OTHER_RETURN_REASON = "Other";

function sf_apply_return_reason_reqd(frm) {
	const is_other = !!frm.doc.is_return && frm.doc.custom_return_reason === SF_OTHER_RETURN_REASON;
	frm.toggle_reqd("remarks", is_other);
}

function sf_focus_remarks(frm) {
	frm.scroll_to_field("remarks");
	// the field's own section may still be expanding when this runs
	setTimeout(() => {
		const control = frm.fields_dict.remarks;
		if (control && control.$input) {
			control.$input.trigger("focus");
		}
	}, 300);
}

frappe.ui.form.on("Sales Invoice", {
	refresh(frm) {
		sf_apply_return_reason_reqd(frm);
	},

	is_return(frm) {
		if (!frm.doc.is_return) {
			frm.set_value("custom_return_reason", "");
		}
		sf_apply_return_reason_reqd(frm);
	},

	custom_return_reason(frm) {
		const reason = frm.doc.custom_return_reason;

		if (reason === SF_OTHER_RETURN_REASON) {
			frm.set_value("remarks", "");
			sf_apply_return_reason_reqd(frm);
			sf_focus_remarks(frm);
			return;
		}

		sf_apply_return_reason_reqd(frm);
		if (reason) {
			frm.set_value("remarks", reason);
		}
	},
});
