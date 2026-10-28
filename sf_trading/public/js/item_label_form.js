// sf_trading/public/js/item_label_form.js
// Print Label on the Item form, for a stock item or a fixed asset (public/js/item_label.js).
frappe.ui.form.on("Item", {
	refresh(frm) {
		if (frm.is_new() || !sf_trading.item_label.labelled(frm.doc)) return;
		frm.add_custom_button(__("Print Label"), () => sf_trading.item_label.print(frm.doc.name));
	},
});
