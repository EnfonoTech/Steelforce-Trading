// Quick Entry feature for sf_trading
// Adds a button to the items grid that opens a dialog listing items with stock,
// allowing the user to select items via checkboxes and add them to the items table.

frappe.provide("sf_trading");

console.log("sf_trading: quick_entry.js loaded");

sf_trading.add_quick_entry_button = function (frm) {
	if (!frm.fields_dict.items || !frm.fields_dict.items.grid) return;

	const grid = frm.fields_dict.items.grid;

	let $toolbar = grid.wrapper.find(".grid-buttons");
	if (!$toolbar.length) {
		const $footer = grid.wrapper.find(".grid-footer");
		if ($footer.length) $toolbar = $footer.find(".grid-buttons");
	}
	if (!$toolbar.length) {
		const $addRowBtn = grid.wrapper.find("button:contains('Add Row')");
		if ($addRowBtn.length) $toolbar = $addRowBtn.closest(".grid-buttons");
	}
	if (!$toolbar.length) return;

	if ($toolbar.find("button:contains('Quick Entry')").length > 0) return;

	// Position after Last Selling Rate button if present
	let $target = $toolbar.find("button:contains('Last Selling Rate')").last();
	if ($target.length === 0) $target = $toolbar.find("button:contains('Add Row')").last();

	const btn = $(`<button type="button" class="btn btn-secondary btn-xs" style="margin-left: 10px;">
		${__('Quick Entry')}
	</button>`);

	btn.on('click', function () {
		sf_trading.open_quick_entry_dialog(frm);
	});

	if ($target.length > 0 && $target.parent().is($toolbar)) {
		btn.insertAfter($target);
	} else {
		$toolbar.append(btn);
	}
};

sf_trading.open_quick_entry_dialog = function (frm) {
	const company = frm.doc.company || frappe.defaults.get_default("company");
	const price_list = frm.doc.selling_price_list || frappe.defaults.get_default("selling_price_list");

	const d = new frappe.ui.Dialog({
		title: __('Quick Entry'),
		size: 'extra-large',
		fields: [
			{
				fieldname: 'item_code',
				label: __('Item Code'),
				fieldtype: 'Link',
				options: 'Item',
				get_query: function () {
					return { filters: { "is_sales_item": 1, "disabled": 0 } };
				}
			},
			{ fieldname: 'results', fieldtype: 'HTML' }
		],
		primary_action_label: __('Add'),
		primary_action: function () {
			sf_trading.add_quick_entry_selection(frm, d);
		},
		secondary_action_label: __('Close'),
		secondary_action: function () {
			d.hide();
		}
	});

	d._frm = frm;
	d._company = company;
	d._price_list = price_list;

	d.show();

	// Fetch full list initially; re-fetch when an item is picked
	sf_trading.fetch_quick_entry_items(d);

	setTimeout(() => {
		if (d.fields_dict.item_code) {
			d.fields_dict.item_code.df.onchange = function () {
				sf_trading.fetch_quick_entry_items(d);
			};
			d.fields_dict.item_code.refresh();
		}
	}, 200);
};

sf_trading.fetch_quick_entry_items = function (dialog) {
	const $wrap = dialog.fields_dict.results.$wrapper;
	$wrap.html('<div class="text-muted">' + __('Loading…') + '</div>');

	const item_code = dialog.get_value('item_code') || '';

	frappe.call({
		method: 'sf_trading.api.quick_entry.get_items_with_stock',
		args: {
			company: dialog._company,
			price_list: dialog._price_list,
			item_code: item_code,
			limit: 200
		},
		callback: function (r) {
			const rows = r.message || [];
			if (!rows.length) {
				$wrap.html('<div class="text-muted">' + __('No items with stock found.') + '</div>');
				return;
			}
			$wrap.html(sf_trading.render_quick_entry_table(rows));
			sf_trading.bind_quick_entry_table(dialog);
		},
		error: function (err) {
			$wrap.html('<div class="text-danger">' + __('Error: {0}', [err.message || err]) + '</div>');
		}
	});
};

sf_trading.render_quick_entry_table = function (rows) {
	const th_style = 'border-top: 1px solid #d1d8dd; border-bottom: 1px solid #d1d8dd; background: #fff;';
	let out = [
		'<style>',
		'  .qe-row-qty::-webkit-outer-spin-button,',
		'  .qe-row-qty::-webkit-inner-spin-button { -webkit-appearance: none; margin: 0; }',
		'  .qe-row-qty { -moz-appearance: textfield; }',
		'</style>',
		'<div class="mt-2" style="max-height: 420px; overflow-y: auto; border: 1px solid #d1d8dd; border-radius: 4px;">',
		'<table class="table table-bordered table-sm mb-0" id="quick-entry-table">',
		'<thead style="position: sticky; top: 0; z-index: 1;">',
		'<tr>',
		`<th style="width: 40px; ${th_style}"><input type="checkbox" id="qe-check-all"></th>`,
		`<th style="${th_style}">` + __('Item Code') + '</th>',
		`<th style="${th_style}">` + __('Item Name') + '</th>',
		`<th style="${th_style}">` + __('Warehouse') + '</th>',
		`<th class="text-right" style="${th_style}">` + __('Warehouse Stock') + '</th>',
		`<th style="${th_style}">` + __('UOM') + '</th>',
		`<th class="text-right" style="${th_style}">` + __('Selling Rate') + '</th>',
		`<th class="text-right" style="width: 90px; ${th_style}">` + __('Qty') + '</th>',
		'</tr>',
		'</thead>',
		'<tbody>'
	].join('');

	rows.forEach(function (r, idx) {
		const item_code = frappe.utils.escape_html(r.item_code || '');
		const item_name = frappe.utils.escape_html(r.item_name || '');
		const warehouse = frappe.utils.escape_html(r.warehouse || '');
		const stock_qty_raw = Number(r.stock_qty || 0);
		const stock_qty = format_number(stock_qty_raw, null, { precision: 2 });
		const uom = frappe.utils.escape_html(r.stock_uom || '');
		const rate_val = Number(r.selling_rate || 0);
		const rate_display = rate_val
			? format_currency(rate_val, r.price_currency || '')
			: '<span class="text-muted">-</span>';
		out += [
			`<tr data-item-code="${item_code}" data-warehouse="${warehouse}" data-rate="${rate_val}" data-stock="${stock_qty_raw}">`,
			`<td><input type="checkbox" class="qe-row-check" data-idx="${idx}"></td>`,
			`<td>${item_code}</td>`,
			`<td>${item_name}</td>`,
			`<td>${warehouse}</td>`,
			`<td class="text-right">${stock_qty}</td>`,
			`<td>${uom}</td>`,
			`<td class="text-right">${rate_display}</td>`,
			`<td class="text-right"><input type="number" class="form-control input-sm qe-row-qty" min="0" max="${stock_qty_raw}" step="any" value="" data-idx="${idx}"></td>`,
			'</tr>'
		].join('');
	});

	out += '</tbody></table></div>';
	return out;
};

sf_trading.bind_quick_entry_table = function (dialog) {
	const $wrap = dialog.fields_dict.results.$wrapper;
	const table = $wrap.find('#quick-entry-table')[0];
	if (!table) return;

	// Check-all toggle
	const $checkAll = $wrap.find('#qe-check-all');
	$checkAll.off('change').on('change', function () {
		$wrap.find('.qe-row-check').prop('checked', this.checked);
	});

	// Auto-tick when qty is edited; clamp to available stock
	$wrap.find('.qe-row-qty').off('input').on('input', function () {
		const $row = $(this).closest('tr');
		$row.find('.qe-row-check').prop('checked', true);

		const stock = parseFloat($row.data('stock')) || 0;
		const entered = parseFloat(this.value) || 0;
		if (entered > stock) {
			this.value = stock;
			frappe.show_alert({
				message: __('Qty cannot exceed warehouse stock ({0})', [stock]),
				indicator: 'orange'
			});
		}
	});
};

sf_trading.add_quick_entry_selection = function (frm, dialog) {
	const $wrap = dialog.fields_dict.results.$wrapper;
	const $checks = $wrap.find('.qe-row-check:checked');

	if (!$checks.length) {
		frappe.show_alert({ message: __('Please select at least one item.'), indicator: 'orange' });
		return;
	}

	// Re-fetch the data we rendered to read item_code, warehouse, rate from current rows
	const rows_to_add = [];
	let invalid_row = null;
	$checks.each(function () {
		const $row = $(this).closest('tr');
		const item_code = $row.data('item-code');
		const warehouse = $row.data('warehouse');
		const rate = parseFloat($row.data('rate')) || 0;
		const stock = parseFloat($row.data('stock')) || 0;
		const qty = parseFloat($row.find('.qe-row-qty').val()) || 0;

		if (qty <= 0) {
			invalid_row = invalid_row || { reason: __('Qty must be greater than 0 for {0}', [item_code]) };
			return;
		}
		if (qty > stock) {
			invalid_row = invalid_row || { reason: __('Qty {0} exceeds warehouse stock {1} for {2}', [qty, stock, item_code]) };
			return;
		}
		rows_to_add.push({ item_code, warehouse, rate, qty });
	});

	if (invalid_row) {
		frappe.show_alert({ message: invalid_row.reason, indicator: 'red' });
		return;
	}
	if (!rows_to_add.length) {
		frappe.show_alert({ message: __('Nothing to add.'), indicator: 'orange' });
		return;
	}

	(async function () {
		for (const row of rows_to_add) {
			const child = frm.add_child('items');
			await frappe.model.set_value(child.doctype, child.name, 'item_code', row.item_code);
			if (row.warehouse) {
				await frappe.model.set_value(child.doctype, child.name, 'warehouse', row.warehouse);
			}
			await frappe.model.set_value(child.doctype, child.name, 'qty', row.qty);
			if (row.rate) {
				await frappe.model.set_value(child.doctype, child.name, 'rate', row.rate);
			}
		}
		frm.refresh_field('items');
		dialog.hide();
		frappe.show_alert({
			message: __('Added {0} item(s)', [rows_to_add.length]),
			indicator: 'green'
		});
	})();
};

// Hook into common doctypes with item tables
const quick_entry_doctypes = ["Sales Order", "Sales Invoice", "Quotation", "Delivery Note"];

quick_entry_doctypes.forEach(function (doctype) {
	frappe.ui.form.on(doctype, {
		refresh: function (frm) {
			let attempts = 0;
			const maxAttempts = 8;
			const tryAddButton = function () {
				attempts++;
				if (frm.fields_dict.items && frm.fields_dict.items.grid) {
					sf_trading.add_quick_entry_button(frm);
					const $toolbar = frm.fields_dict.items.grid.wrapper.find(".grid-buttons");
					if ($toolbar.find("button:contains('Quick Entry')").length > 0) return;
				}
				if (attempts < maxAttempts) setTimeout(tryAddButton, 400);
			};
			setTimeout(tryAddButton, 800);
		},

		items_add: function (frm) {
			setTimeout(function () {
				sf_trading.add_quick_entry_button(frm);
			}, 800);
		}
	});
});
