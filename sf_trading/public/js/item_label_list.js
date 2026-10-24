// sf_trading/public/js/item_label_list.js
// The Item list: a green Print Label button on each stock / fixed-asset item row (public/js/item_label.js), and
// the Standard Selling Rate under the item's ID. ERPNext's own Item list settings are kept; only these are added.
(function () {
	const settings = (frappe.listview_settings["Item"] = frappe.listview_settings["Item"] || {});
	settings.add_fields = [
		...new Set([...(settings.add_fields || []), "is_stock_item", "is_fixed_asset", "standard_rate"]),
	];

	settings.formatters = Object.assign({}, settings.formatters, {
		name(value, df, doc) {
			let html = `<div>${value}</div>`;
			if (flt(doc.standard_rate)) {
				html += `<div style="font-size: 12px; color: var(--blue-600, #1a73e8); font-weight: 600">
					${format_currency(doc.standard_rate, frappe.boot.sysdefaults.currency)}
				</div>`;
			}
			return html;
		},
	});

	settings.button = {
		show: (doc) => !cint(doc.disabled) && sf_trading.item_label.labelled(doc),
		get_label: () => `<span class="sf-print-label text-nowrap">${frappe.utils.icon("printer", "xs")} ${__("Print Label")}</span>`,
		get_description: () => __("Print labels for this item"),
		action: (doc) => sf_trading.item_label.print(doc.name),
	};

	// the row button in green, white text and icon (the list gives it no class of its own to style)
	if (!document.getElementById("sf-print-label-style")) {
		$(`<style id="sf-print-label-style">
			button.btn-action:has(.sf-print-label) { background: linear-gradient(135deg, #16a34a, #22c55e) !important; border-color: #16a34a !important; color: #fff !important; font-weight: 600; box-shadow: 0 2px 6px rgba(0, 0, 0, 0.18); transition: all 0.2s ease; }
			button.btn-action:has(.sf-print-label):hover { background: linear-gradient(135deg, #15803d, #16a34a) !important; box-shadow: 0 4px 10px rgba(0, 0, 0, 0.28); transform: translateY(-1px); }
			.sf-print-label .icon { --icon-stroke: #fff; stroke: #fff; }
			/* the button's room kept on every Item row, so the time and comment count line up whether a row has one or not */
			body[data-route^="List/Item/"] .list-row .list-row-activity > div:first-child { min-width: 124px; display: inline-flex; justify-content: flex-end; }
		</style>`).appendTo("head");
	}
})();
