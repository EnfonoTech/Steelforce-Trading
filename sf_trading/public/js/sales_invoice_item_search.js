// Override the item_code autocomplete in the Sales Invoice items grid so each
// suggestion in the dropdown shows the user's stock and the price-list selling
// rate next to the item name — same data Quick Entry shows.
//
// ERPNext's sales controller (sales_common.js) registers
// erpnext.controllers.queries.item_query for the same field via its
// setup_queries() method, so we override in multiple lifecycle events AND
// patch the field's get_query directly to be sure we win the race.

console.log("[sf_trading] sales_invoice_item_search.js loaded");

(function () {
	const CUSTOM_QUERY = "sf_trading.api.item_search.search_items_with_stock_and_rate";

	function build_filters(frm) {
		return {
			is_sales_item: 1,
			has_variants: 0,
			disabled: 0,
			customer: frm.doc.customer,
			company: frm.doc.company,
			price_list: frm.doc.selling_price_list,
		};
	}

	function apply_override(frm) {
		if (!frm || !frm.fields_dict || !frm.fields_dict.items) return;
		const grid = frm.fields_dict.items.grid;
		if (!grid) return;

		// 1) Standard set_query — registers our query at the form level.
		frm.set_query("item_code", "items", function (doc) {
			return {
				query: CUSTOM_QUERY,
				filters: build_filters(frm),
			};
		});

		// 2) Direct df.get_query patch — wins regardless of who registered last.
		const field = grid.get_field && grid.get_field("item_code");
		if (field && field.df) {
			field.df.get_query = function () {
				return {
					query: CUSTOM_QUERY,
					filters: build_filters(frm),
				};
			};
		}

		attach_cache_buster(frm);
	}

	// Frappe's Link control caches autocomplete results per (doctype, term) on
	// the input element so re-searches show instantly. That means stock numbers
	// in the dropdown can be stale on the first paint after a stock movement.
	// Wipe that cache on focus so the next search always hits the API fresh.
	function attach_cache_buster(frm) {
		const grid = frm.fields_dict.items && frm.fields_dict.items.grid;
		if (!grid || !grid.wrapper) return;

		grid.wrapper
			.off("focusin.sf_item_search_cache")
			.on(
				"focusin.sf_item_search_cache",
				"[data-fieldname='item_code'] input",
				function () {
					(grid.grid_rows || []).forEach(function (gr) {
						const ctrl = gr && gr.columns && gr.columns.item_code;
						if (ctrl && ctrl.$input) {
							ctrl.$input.cache = {};
						}
					});
				}
			);
	}

	frappe.ui.form.on("Sales Invoice", {
		setup: function (frm) {
			apply_override(frm);
		},
		onload: function (frm) {
			apply_override(frm);
		},
		refresh: function (frm) {
			apply_override(frm);
		},
		// Re-apply when these change so the next dropdown opens reflect the
		// current form state (different price list / customer / company).
		customer: function (frm) {
			apply_override(frm);
		},
		company: function (frm) {
			apply_override(frm);
		},
		selling_price_list: function (frm) {
			apply_override(frm);
		},
	});
})();
