// sf_trading/public/js/item_uppercase.js
// The Item master is kept in CAPITALS. The server (sf_trading/item_case.py) is the authority and runs on every save; this only shows the
// capitals on the form as the user leaves the field. The Item Code of an existing item is its record name and is never changed here.
frappe.ui.form.on("Item", {
	item_name(frm) {
		sf_item_capitals(frm, "item_name");
	},
	item_code(frm) {
		if (frm.is_new()) sf_item_capitals(frm, "item_code");
	},
});

function sf_item_capitals(frm, fieldname) {
	const value = frm.doc[fieldname];
	if (typeof value === "string" && value !== value.toUpperCase()) {
		frm.set_value(fieldname, value.toUpperCase());
	}
}
