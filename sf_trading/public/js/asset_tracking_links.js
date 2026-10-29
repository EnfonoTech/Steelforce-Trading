// Copyright (c) 2026, Enfono Technologies and contributors
// For license information, please see license.txt

// Tracking-only Assets (sf_trading/asset_tracking_item.py): an Asset raised for an expensed item
// ("Track as Asset (no capitalization)") so its location, custodian and movements can be followed.
//
// 1. Purchase Invoice / Purchase Receipt: an "Asset Tracking" section under the form's own
//    Connections, listing the tracking Assets the document raised -- Asset, item, location,
//    custodian, status -- each a link that opens the Asset. It is its own section on purpose: core's
//    Assets connection is the capitalized Fixed Asset one and never lists these.
// 2. Asset: a tracking Asset is not capitalized and not depreciated, so the Manage actions that need
//    a Fixed Asset Account (scrap, sell, restore, split, adjust value, depreciation entry) are not
//    offered, and the form says what kind of Asset it is. Transfer Asset (the movement) stays.

frappe.ui.form.on("Purchase Invoice", { refresh: render_asset_tracking });
frappe.ui.form.on("Purchase Receipt", { refresh: render_asset_tracking });

frappe.ui.form.on("Asset", {
	setup: guard_capitalization_actions,
	refresh(frm) {
		if (is_tracking_asset(frm)) {
			frm.dashboard.set_headline_alert(
				__(
					"Tracking-only Asset: it follows location, custodian and movement. It is not capitalized and not depreciated; the purchase stays on its expense account."
				),
				"blue"
			);
		}
	},
});

function is_tracking_asset(frm) {
	return !!(frm.doc && frm.doc.custom_source_row);
}

// Core adds its Manage buttons inside its own refresh and rebuilds the toolbar on every paint, so a
// remove_custom_button after the fact loses to the re-paint: decline to paint them instead.
// (var, not const: this one file serves three doctypes, so the script runs more than once)
var CAPITALIZATION_ACTIONS = [
	"Scrap Asset",
	"Sell Asset",
	"Restore Asset",
	"Split Asset",
	"Adjust Asset Value",
	"Create Depreciation Entry",
];

function guard_capitalization_actions(frm) {
	if (frm.__sf_tracking_guarded) return;
	frm.__sf_tracking_guarded = true;
	const original = frm.page.add_inner_button.bind(frm.page);
	frm.page.add_inner_button = function (label, action, group, type) {
		if (is_tracking_asset(frm) && CAPITALIZATION_ACTIONS.some((l) => label === __(l))) return;
		return original(label, action, group, type);
	};
}

function render_asset_tracking(frm) {
	const section = ".sf-asset-tracking-section";
	frm.dashboard.parent.find(section).remove();
	// a tracking Asset is raised on submit: a draft or an unsaved form has none yet
	if (frm.is_new() || frm.doc.docstatus === 0) return;

	// stale-response guard: only the latest request may draw
	const seq = (frm._sf_asset_tracking_seq = (frm._sf_asset_tracking_seq || 0) + 1);

	frappe.call({
		method: "sf_trading.asset_tracking_item.get_tracking_assets",
		args: { doctype: frm.doc.doctype, docname: frm.doc.name },
		callback(r) {
			if (seq !== frm._sf_asset_tracking_seq) return;
			// replace, never stack: queried fresh from the dashboard, not from a kept reference
			frm.dashboard.parent.find(section).remove();
			const assets = r.message || [];
			if (!assets.length) return;
			frm.dashboard.add_section(build_html(frm, assets), __("Asset Tracking"), "sf-asset-tracking-section");
			frm.dashboard.show();
		},
	});
}

function build_html(frm, assets) {
	const esc = (value) => frappe.utils.escape_html(value || "");
	const rows = assets
		.map((asset) => {
			const status = asset.docstatus === 2 ? __("Cancelled") : asset.status;
			const custodian = asset.custodian
				? esc(asset.custodian_name || asset.custodian)
				: '<span class="text-muted">-</span>';
			// which document raised it, when that is not this one (an invoice billing a tracked receipt line)
			const via =
				asset.source && asset.source !== frm.doc.name
					? `<div class="text-muted small">${__(asset.source_doctype)} ${esc(asset.source)}</div>`
					: "";
			return `<tr>
				<td>${frappe.utils.get_form_link("Asset", asset.name, true)}</td>
				<td>${esc(asset.item_code)}${asset.item_name && asset.item_name !== asset.item_code ? `<div class="text-muted small">${esc(asset.item_name)}</div>` : ""}${via}</td>
				<td>${esc(asset.location)}</td>
				<td>${custodian}</td>
				<td><span class="indicator-pill ${asset.docstatus === 2 ? "red" : asset.docstatus === 1 ? "green" : "orange"}">${esc(status)}</span></td>
			</tr>`;
		})
		.join("");

	return `<div class="form-dashboard-section-body">
		<div class="text-muted small" style="margin-bottom:6px">${__(
			"Raised for location, custodian and movement tracking only: not capitalized, not depreciated."
		)}</div>
		<table class="table table-bordered table-sm" style="margin:0">
			<thead><tr>
				<th>${__("Asset")}</th><th>${__("Item")}</th><th>${__("Location")}</th>
				<th>${__("Custodian")}</th><th>${__("Status")}</th>
			</tr></thead>
			<tbody>${rows}</tbody>
		</table>
	</div>`;
}
