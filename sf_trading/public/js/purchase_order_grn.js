// Purchase Order: no Create > Purchase Receipt while GRN is disabled for this user
// (SF Trading Settings > Disable Purchase Receipt; see sf_trading/purchase_receipt_gate.py).
// ERPNext's own controller adds the button after the frappe.ui.form.on handlers have run, so it
// is taken away on the next tick.
frappe.ui.form.on("Purchase Order", {
	refresh(frm) {
		if (!frappe.boot.sf_grn_disabled) return;
		setTimeout(() => frm.remove_custom_button(__("Purchase Receipt"), __("Create")), 0);
	},
});
