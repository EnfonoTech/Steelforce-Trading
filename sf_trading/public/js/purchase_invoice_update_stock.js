// sf_trading/public/js/purchase_invoice_update_stock.js
// Update Stock on a Purchase Invoice is ticked, and locked, when the invoice is what takes its goods into
// stock; only Administrator can untick it (sf_trading/api/purchase_order_invoice.py, require_stock_update).
// Whether the invoice is that is the server's answer (stock items, no receipt behind the rows or the order,
// not a return, not tracked) -- asked once the items settle, so the form and the save agree.
// UI only: the server ticks the box again on save, approval and submit whatever the form lets through.

(function () {
	const is_admin = () => frappe.session.user === "Administrator";
	const LOCKED_NOTE = __("On: this invoice brings the goods into stock. Only Administrator can change it.");

	function apply(frm, locked) {
		frm.sf_update_stock_locked = locked;
		frm.set_df_property("update_stock", "read_only", locked && !is_admin() ? 1 : 0);
		frm.set_df_property("update_stock", "description", locked ? LOCKED_NOTE : "");
		// Administrator may untick a saved invoice and have it stay so; a new one still opens ticked
		if (locked && !cint(frm.doc.update_stock) && (!is_admin() || frm.is_new())) {
			frm.set_value("update_stock", 1);
		}
	}

	function check(frm) {
		clearTimeout(frm.sf_update_stock_timer);
		frm.sf_update_stock_timer = setTimeout(function () {
			if (frm.doc.docstatus !== 0 || !(frm.doc.items || []).some((row) => row.item_code)) return;
			frappe.call({
				method: "sf_trading.api.purchase_order_invoice.update_stock_rule",
				args: { doc: frm.doc },
				callback(r) {
					if (r.message) apply(frm, !!r.message.locked);
				},
			});
		}, 400);
	}

	frappe.ui.form.on("Purchase Invoice", {
		refresh: check,
		is_return: check,
		is_opening: check,
		update_stock(frm) {
			// a locked box that is unticked anyway (a script, a paste) comes straight back on
			if (frm.sf_update_stock_locked && !is_admin() && !cint(frm.doc.update_stock)) {
				frm.set_value("update_stock", 1);
			}
		},
	});

	frappe.ui.form.on("Purchase Invoice Item", {
		item_code: (frm) => check(frm),
		items_remove: (frm) => check(frm),
	});
})();
