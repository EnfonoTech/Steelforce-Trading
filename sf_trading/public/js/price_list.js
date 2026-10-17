// Price List form: one click to the items this list does not price.
//
// The report is Items Without Price List (sf_trading/report). It opens on this list and on the side
// of the business the list serves; the Applicable Branches table itself needs no script -- it is a
// plain table, and sf_trading/branch_price_list.py does the rest on save.

frappe.ui.form.on("Price List", {
	refresh(frm) {
		if (frm.is_new()) return;

		frm.add_custom_button(
			__("Items Without Price"),
			function () {
				frappe.set_route("query-report", "Items Without Price List", {
					price_type: frm.doc.selling ? "Selling" : "Buying",
					price_list: frm.doc.name,
				});
			},
			__("View")
		);
	},
});
