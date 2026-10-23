// sf_trading/public/js/po_stock_count.js
// The physical stock count on a Purchase Order row (sf_trading/po_stock_count.py).
//
// The count is typed as text so that blank (not counted) and 0 (counted, shelf empty) stay apart; the
// save checks it is a number. As it is typed, the variance is shown against the warehouse stock ERPNext
// put on the row when the item was picked; the save replaces both with the stock as it stands then.
frappe.ui.form.on("Purchase Order Item", {
	custom_physical_stock_count(frm, cdt, cdn) {
		const row = locals[cdt][cdn];
		const raw = (row.custom_physical_stock_count || "").toString().trim().replace(/,/g, "");
		const value = raw === "" ? null : Number(raw);
		if (value === null || isNaN(value)) {
			frappe.model.set_value(cdt, cdn, "custom_stock_variance", 0);
			return;
		}
		const system = flt(row.actual_qty);
		frappe.model.set_value(cdt, cdn, "custom_system_stock", system);
		frappe.model.set_value(cdt, cdn, "custom_stock_variance", value - system);
	},
});
