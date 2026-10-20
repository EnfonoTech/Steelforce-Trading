// sf_trading/public/js/purchase_receipt_gate.js
// A Purchase Receipt while GRN is disabled for this user (SF Trading Settings > Disable Purchase Receipt;
// sf_trading/purchase_receipt_gate.py): the form is for looking at it, billing from it and costing it with a
// Landed Cost Voucher -- not for changing it. The server refuses Submit, Cancel, Amend, Edit and Return
// whatever the form offers; this only stops the form offering them. ERPNext adds its own buttons after the
// frappe.ui.form.on handlers have run, so they are taken away on the next tick.
frappe.ui.form.on("Purchase Receipt", {
	refresh(frm) {
		if (!frappe.boot.sf_grn_disabled || frm.is_new()) return;
		setTimeout(() => {
			// the primary button is Submit on a draft, Cancel on a submitted receipt, Amend on a cancelled one
			frm.page.clear_primary_action();
			frm.remove_custom_button(__("Purchase Return"), __("Create"));
			frm.disable_save();
		}, 0);
		frm.dashboard.add_comment(
			__("Purchase Receipt is switched off: this receipt can be looked at, billed from and costed by a Landed Cost Voucher, but not changed or cancelled."),
			"blue",
			true
		);
	},
});
