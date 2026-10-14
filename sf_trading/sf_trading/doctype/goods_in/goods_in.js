// Copyright (c) 2026, Enfono Technologies and contributors
// For license information, please see license.txt

frappe.ui.form.on("Goods In", {
	setup(frm) {
		frm.set_query("purchase_order", () => ({
			filters: { docstatus: 1, status: ["not in", ["Closed", "On Hold", "Completed"]] },
		}));
	},

	refresh(frm) {
		if (frm.doc.docstatus === 1 && frm.doc.status === "To Bill") {
			frm.add_custom_button(
				__("Purchase Invoice"),
				() =>
					frappe.model.open_mapped_doc({
						method: "sf_trading.sf_trading.doctype.goods_in.goods_in.make_purchase_invoice",
						frm,
					}),
				__("Create")
			);
		}
	},

	purchase_order(frm) {
		if (!frm.doc.purchase_order) return;
		// fill the rows from the order, each with what is still to be received
		frappe.call({
			method: "sf_trading.sf_trading.doctype.goods_in.goods_in.make_goods_in",
			args: { source_name: frm.doc.purchase_order },
			callback(r) {
				if (!r.message) return;
				frm.clear_table("items");
				(r.message.items || []).forEach((row) => {
					const child = frm.add_child("items");
					[
						"item_code", "item_name", "uom", "warehouse", "ordered_qty",
						"pending_qty", "qty", "purchase_order_item",
					].forEach((f) => (child[f] = row[f]));
				});
				frm.refresh_field("items");
			},
		});
	},
});
