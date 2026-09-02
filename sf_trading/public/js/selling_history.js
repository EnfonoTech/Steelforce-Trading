// Selling History button for sf_trading — the third button on the Purchase Order items grid,
// beside Stock Availability and Last Purchase Rate.
//
// A buyer agreeing a purchase price wants the other half of the picture: what this branch has been
// getting for the same item lately. Opens on the order's own branch (its cost centre) and can be
// widened to every branch, which is the comparison a buyer actually asks for.
//
// Built the same way as stock_availability.js — a button injected into the grid toolbar, because
// that toolbar is where the other two live and frappe offers no supported slot for a third.

frappe.provide("sf_trading");

sf_trading.add_selling_history_button = function (frm) {
	if (!frm.fields_dict.items || !frm.fields_dict.items.grid) return;

	const grid = frm.fields_dict.items.grid;
	let $toolbar = grid.wrapper.find(".grid-buttons");
	if (!$toolbar.length) $toolbar = grid.wrapper.find(".grid-footer .grid-buttons");
	if (!$toolbar.length) return;
	if ($toolbar.find("button:contains('Selling History')").length > 0) return;

	const btn = $(
		`<button type="button" class="btn btn-secondary btn-xs" style="margin-left: 10px;">${__(
			"Selling History"
		)}</button>`
	);
	btn.on("click", function () {
		sf_trading.open_selling_history_dialog(frm);
	});

	// after Stock Availability when it is there, so the three read left to right in the order a
	// buyer uses them: what we hold, what we paid, what we sell it for
	const $after = $toolbar.find("button:contains('Stock Availability')").last();
	if ($after.length && $after.parent().is($toolbar)) btn.insertAfter($after);
	else $toolbar.append(btn);
};

sf_trading.open_selling_history_dialog = function (frm) {
	const items = (frm.doc.items || []).map((row) => row.item_code).filter(Boolean);
	if (!items.length) {
		frappe.msgprint({
			title: __("No Items"),
			message: __("Add an item to the order first."),
			indicator: "orange",
		});
		return;
	}

	// the order's own branch, read from its cost centre — the same way every branch-aware report
	// on this site reads it
	const branch_cc = frm.doc.cost_center || (frm.doc.items[0] || {}).cost_center || null;

	const dialog = new frappe.ui.Dialog({
		title: __("Selling History"),
		size: "extra-large",
		fields: [
			{
				fieldname: "from_date",
				fieldtype: "Date",
				label: __("From"),
				default: frappe.datetime.add_months(frappe.datetime.get_today(), -12),
			},
			{ fieldname: "to_date", fieldtype: "Date", label: __("To"), default: frappe.datetime.get_today() },
			{ fieldtype: "Column Break" },
			{
				fieldname: "cost_center",
				fieldtype: "Link",
				options: "Cost Center",
				label: __("Branch (Cost Center)"),
				default: branch_cc,
				get_query: () => ({ filters: { company: frm.doc.company } }),
			},
			{
				fieldname: "all_branches",
				fieldtype: "Check",
				label: __("All Branches"),
				default: 0,
				description: __("Compare what other branches are getting for the same item."),
			},
			{ fieldtype: "Column Break" },
			{ fieldname: "customer", fieldtype: "Link", options: "Customer", label: __("Customer") },
			{
				fieldname: "item_code",
				fieldtype: "Select",
				label: __("Item"),
				options: [__("All items on this order")].concat(items).join("\n"),
				default: __("All items on this order"),
			},
			{ fieldtype: "Section Break" },
			{ fieldname: "results", fieldtype: "HTML" },
		],
		primary_action_label: __("Refresh"),
		primary_action() {
			sf_trading.load_selling_history(frm, dialog, items);
		},
	});

	dialog.show();
	sf_trading.load_selling_history(frm, dialog, items);
};

sf_trading.load_selling_history = function (frm, dialog, items) {
	const values = dialog.get_values(true) || {};
	const chosen = values.item_code && items.includes(values.item_code) ? [values.item_code] : items;
	const $body = dialog.fields_dict.results.$wrapper;
	$body.html(`<div class="text-muted" style="padding: 12px">${__("Loading…")}</div>`);

	frappe.call({
		method: "sf_trading.api.selling_history.get_selling_history",
		args: {
			items: JSON.stringify(chosen),
			company: frm.doc.company,
			cost_center: values.cost_center || null,
			all_branches: values.all_branches ? 1 : 0,
			from_date: values.from_date || null,
			to_date: values.to_date || null,
			customer: values.customer || null,
		},
		callback(r) {
			sf_trading.render_selling_history(frm, $body, (r && r.message) || {});
		},
		error() {
			$body.html(
				`<div class="text-muted" style="padding: 12px">${__("Could not read the selling history.")}</div>`
			);
		},
	});
};

sf_trading.render_selling_history = function (frm, $body, data) {
	const rows = data.rows || [];
	const summary = data.summary || [];
	const currency = frm.doc.currency || frappe.defaults.get_default("currency");

	if (!rows.length) {
		$body.html(
			`<div class="text-muted" style="padding: 12px">${__(
				"Nothing sold in this window. Widen the dates, or tick All Branches."
			)}</div>`
		);
		return;
	}

	const money = (v) => format_currency(flt(v), currency);
	const esc = frappe.utils.escape_html || ((t) => t);

	const summary_rows = summary
		.map(
			(s) => `<tr>
				<td>${esc(s.item_code)}<div class="text-muted small">${esc(s.item_name || "")}</div></td>
				<td class="text-right">${s.invoices}</td>
				<td class="text-right">${format_number(s.qty, null, 3)}</td>
				<td class="text-right"><b>${money(s.last_rate)}</b><div class="text-muted small">${
					frappe.datetime.str_to_user(s.last_date) || ""
				}</div></td>
				<td class="text-right">${money(s.low_rate)}</td>
				<td class="text-right">${money(s.high_rate)}</td>
				<td class="text-right">${money(s.avg_rate)}</td>
			</tr>`
		)
		.join("");

	const detail_rows = rows
		.map(
			(row) => `<tr>
				<td>${frappe.datetime.str_to_user(row.posting_date)}</td>
				<td>${esc(row.item_code)}</td>
				<td>${esc(row.customer_name || row.customer || "")}</td>
				<td>${esc(row.cost_center || "")}</td>
				<td class="text-right">${format_number(row.qty, null, 3)} ${esc(row.uom || "")}</td>
				<td class="text-right"><b>${money(row.rate)}</b>${
					row.foreign ? `<div class="text-muted small">${esc(row.foreign)}</div>` : ""
				}</td>
				<td><a href="/app/sales-invoice/${encodeURIComponent(row.invoice)}" target="_blank">${esc(
					row.invoice
				)}</a></td>
			</tr>`
		)
		.join("");

	$body.html(`
		<div style="max-height: 62vh; overflow: auto">
			<h5 style="margin-top: 4px">${__("By Item")}</h5>
			<table class="table table-bordered table-sm">
				<thead><tr>
					<th>${__("Item")}</th><th class="text-right">${__("Invoices")}</th>
					<th class="text-right">${__("Qty")}</th><th class="text-right">${__("Last Rate")}</th>
					<th class="text-right">${__("Lowest")}</th><th class="text-right">${__("Highest")}</th>
					<th class="text-right">${__("Average")}</th>
				</tr></thead>
				<tbody>${summary_rows}</tbody>
			</table>
			<h5>${__("Recent Sales")}</h5>
			<table class="table table-bordered table-sm">
				<thead><tr>
					<th>${__("Date")}</th><th>${__("Item")}</th><th>${__("Customer")}</th>
					<th>${__("Branch")}</th><th class="text-right">${__("Qty")}</th>
					<th class="text-right">${__("Rate")}</th><th>${__("Invoice")}</th>
				</tr></thead>
				<tbody>${detail_rows}</tbody>
			</table>
			<div class="text-muted small">${__(
				"Rates are net of discount and before tax, in company currency. Returns are excluded."
			)}</div>
		</div>
	`);
};

frappe.ui.form.on("Purchase Order", {
	refresh(frm) {
		let attempts = 0;
		const tryAdd = function () {
			attempts++;
			if (frm.fields_dict.items && frm.fields_dict.items.grid) {
				sf_trading.add_selling_history_button(frm);
				const $toolbar = frm.fields_dict.items.grid.wrapper.find(".grid-buttons");
				if ($toolbar.find("button:contains('Selling History')").length > 0) return;
			}
			if (attempts < 8) setTimeout(tryAdd, 400);
		};
		setTimeout(tryAdd, 900);
	},
	items_add(frm) {
		setTimeout(() => sf_trading.add_selling_history_button(frm), 900);
	},
});
