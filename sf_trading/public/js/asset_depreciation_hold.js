// Copyright (c) 2026, Enfono Technologies and contributors
// For license information, please see license.txt

// Depreciation On Hold (sf_trading/asset_depreciation_hold.py): a submitted Asset whose depreciation
// has not started says so, and offers Start Depreciation under Manage -- the Available-for-use Date
// and the depreciation details, entered when the asset is put to use.

// The stored status stays core's own; "On Hold" is the tick on a submitted Asset not yet depreciating.
// var, not const: a doctype_js file can run more than once on one page
var SF_HOLD_STATUSES = ["Submitted", "Partially Depreciated"];

function sf_depreciation_on_hold(doc) {
	return doc.docstatus === 1 && cint(doc.custom_depreciation_on_hold) && !cint(doc.calculate_depreciation)
		&& SF_HOLD_STATUSES.includes(doc.status);
}

frappe.ui.form.on("Asset", {
	refresh(frm) {
		if (!sf_depreciation_on_hold(frm.doc)) return;
		frm.page.set_indicator(__("On Hold"), "orange");
		frm.dashboard.set_headline_alert(
			__("Depreciation On Hold: the purchase is booked, depreciation has not started. Start it with Manage > Start Depreciation once the asset is put to use."),
			"orange"
		);
		frm.add_custom_button(__("Start Depreciation"), () => start_depreciation(frm), __("Manage"));
	},
});

function start_depreciation(frm) {
	frappe
		.call({
			method: "sf_trading.asset_depreciation_hold.get_depreciation_defaults",
			args: { asset: frm.doc.name },
		})
		.then((r) => {
			const defaults = r.message || {};
			const dialog = new frappe.ui.Dialog({
				title: __("Start Depreciation"),
				fields: [
					{
						fieldname: "available_for_use_date",
						label: __("Available-for-use Date"),
						fieldtype: "Date",
						reqd: 1,
						default: defaults.available_for_use_date,
						description: __("The day the asset was put to use; depreciation is counted from here."),
					},
					{
						fieldname: "depreciation_start_date",
						label: __("Depreciation Posting Date"),
						fieldtype: "Date",
						reqd: 1,
						default: defaults.depreciation_start_date,
						description: __("The date of the first depreciation entry."),
					},
					{ fieldtype: "Column Break" },
					{
						fieldname: "depreciation_method",
						label: __("Depreciation Method"),
						fieldtype: "Select",
						options: ["Straight Line", "Double Declining Balance", "Written Down Value", "Manual"],
						reqd: 1,
						default: defaults.depreciation_method,
					},
					{
						fieldname: "total_number_of_depreciations",
						label: __("Total Number of Depreciations"),
						fieldtype: "Int",
						reqd: 1,
						default: defaults.total_number_of_depreciations,
					},
					{
						fieldname: "frequency_of_depreciation",
						label: __("Frequency of Depreciation (Months)"),
						fieldtype: "Int",
						reqd: 1,
						default: defaults.frequency_of_depreciation,
					},
					{
						fieldname: "expected_value_after_useful_life",
						label: __("Expected Value After Useful Life"),
						fieldtype: "Currency",
						default: 0,
					},
				],
				primary_action_label: __("Start Depreciation"),
				primary_action(values) {
					frappe
						.call({
							method: "sf_trading.asset_depreciation_hold.start_depreciation",
							args: Object.assign({ asset: frm.doc.name, finance_book: defaults.finance_book }, values),
							freeze: true,
						})
						.then(() => {
							dialog.hide();
							frappe.show_alert({ message: __("Depreciation started"), indicator: "green" });
							frm.reload_doc();
						});
				},
			});
			dialog.show();
		});
}
