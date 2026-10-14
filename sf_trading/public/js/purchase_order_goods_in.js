// Purchase Order: Create -> Goods In, while anything is still to be received. Goods In records the
// arrival for quantity only; see sf_trading/goods_in.py.
frappe.ui.form.on("Purchase Order", {
	refresh(frm) {
		if (
			frm.doc.docstatus === 1 &&
			flt(frm.doc.per_received) < 100 &&
			!["Closed", "On Hold", "Completed"].includes(frm.doc.status)
		) {
			frm.add_custom_button(
				__("Goods In"),
				() =>
					frappe.model.open_mapped_doc({
						method: "sf_trading.sf_trading.doctype.goods_in.goods_in.make_goods_in",
						frm,
					}),
				__("Create")
			);
		}
	},
});
