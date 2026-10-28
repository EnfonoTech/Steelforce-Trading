// sf_trading/public/js/item_label.js
// Print Label for a stock item or a fixed asset (sf_trading/item_label.py): asks how many labels, showing the
// stock available, then opens the Item Label print view with that many and the browser's print dialog.
// Used by the Item list (item_label_list.js) and the Item form (item_label_form.js).
frappe.provide("sf_trading.item_label");

sf_trading.item_label.labelled = (doc) => cint(doc.is_stock_item) || cint(doc.is_fixed_asset);

sf_trading.item_label.open_print = (item_code, copies) => {
	const params = new URLSearchParams({
		doctype: "Item",
		name: item_code,
		format: "Item Label",
		no_letterhead: 1,
		copies: copies,
		trigger_print: 1,
	});
	window.open(`/printview?${params.toString()}`, "_blank");
};

sf_trading.item_label.print = (item_code) => {
	frappe.call({
		method: "sf_trading.item_label.available_qty",
		args: { item_code: item_code },
		callback(r) {
			const stock = (r.message && r.message.qty) || 0;
			const where = r.message && r.message.warehouse ? r.message.warehouse : __("all warehouses");
			const dialog = new frappe.ui.Dialog({
				title: __("Print Item Label"),
				fields: [
					{
						fieldname: "copies",
						label: __("Number of Labels"),
						fieldtype: "Int",
						default: 1,
						reqd: 1,
						description: __("Available stock: {0} ({1})", [format_number(stock, null, 0), where]),
					},
				],
				primary_action_label: __("Print"),
				primary_action(values) {
					if (!values.copies || values.copies <= 0) {
						frappe.msgprint(__("Enter a valid number"));
						return;
					}
					if (values.copies > 500) {
						frappe.msgprint(__("Print at most 500 labels at a time."));
						return;
					}
					dialog.hide();
					frappe.show_alert({ message: __("Opening print view..."), indicator: "blue" });
					sf_trading.item_label.open_print(item_code, values.copies);
				},
			});
			dialog.show();
		},
	});
};
