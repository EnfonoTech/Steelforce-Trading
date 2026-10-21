// sf_trading/public/js/row_price_list.js
// A Price List on each item row of a Quotation and a Sales Order -- the twin of the Sales Invoice's
// (public/js/sales_invoice.js, "Per-Item Price List"). A row may name its own Price List
// (custom_price_list); left blank, the document's own applies. The row's rate is looked up from the list
// it names the way ERPNext itself does it (party pricing, qty breaks, UOM fallback, currency), through
// sf_trading.api.item_row_price_list.get_row_price_list_rate, so an offer price can be picked item by item
// before the quotation or order goes out. Rows carry the choice on to the Sales Order / Sales Invoice made
// from them (same field name on all three). UI only: the minimum-price check at save already reads the
// row's list (api/selling_price_validation.py).

(function () {
	const PARENTS = { Quotation: "Quotation Item", "Sales Order": "Sales Order Item" };

	// a Quotation is made out to a Customer, a Lead or a Prospect; only a Customer has prices of its own
	function customer_of(frm) {
		if (frm.doc.doctype === "Quotation") {
			return frm.doc.quotation_to === "Customer" ? frm.doc.party_name || "" : "";
		}
		return frm.doc.customer || "";
	}

	function apply_row_price_list(frm, cdt, cdn) {
		const row = locals[cdt] && locals[cdt][cdn];
		if (!row || !row.item_code) return;
		const price_list = row.custom_price_list || frm.doc.selling_price_list;
		if (!price_list) return;

		frappe.call({
			method: "sf_trading.api.item_row_price_list.get_row_price_list_rate",
			args: {
				item_code: row.item_code,
				price_list: price_list,
				doctype: frm.doc.doctype,
				uom: row.uom,
				stock_uom: row.stock_uom,
				qty: row.qty || 1,
				transaction_date: frm.doc.transaction_date || frappe.datetime.get_today(),
				customer: customer_of(frm),
				company: frm.doc.company,
				conversion_rate: frm.doc.conversion_rate || 1,
				branch: frm.doc.branch || "",
			},
			callback: function (r) {
				// a list with no price for the item leaves the row as it is
				if (!r.message || !flt(r.message.rate)) return;
				frappe.model.set_value(cdt, cdn, "price_list_rate", r.message.rate);
				frappe.model.set_value(cdt, cdn, "rate", r.message.rate);
			},
		});
	}

	Object.keys(PARENTS).forEach(function (parent_dt) {
		const child_handlers = {
			custom_price_list: function (frm, cdt, cdn) {
				apply_row_price_list(frm, cdt, cdn);
			},
			item_code: function (frm, cdt, cdn) {
				// ERPNext's own item_code handler re-rates the row from the document's price list a moment
				// later; apply the row's own list afterwards so that does not clobber it
				const row = locals[cdt][cdn];
				if (row && row.custom_price_list) {
					setTimeout(function () {
						apply_row_price_list(frm, cdt, cdn);
					}, 800);
				}
			},
			items_add: function (frm, cdt, cdn) {
				// a new row opens on the document's list, so the column always says which list prices it
				const row = locals[cdt][cdn];
				if (row && !row.custom_price_list && frm.doc.selling_price_list) {
					frappe.model.set_value(cdt, cdn, "custom_price_list", frm.doc.selling_price_list);
				}
			},
		};
		frappe.ui.form.on(PARENTS[parent_dt], child_handlers);

		frappe.ui.form.on(parent_dt, {
			selling_price_list: function (frm) {
				// ERPNext re-rates every row from the document's list a moment after it changes; apply the
				// rows that name a list of their own afterwards
				setTimeout(function () {
					(frm.doc.items || []).forEach(function (row) {
						if (row.item_code && row.custom_price_list) {
							apply_row_price_list(frm, row.doctype, row.name);
						}
					});
				}, 700);
			},
		});
	});
})();
