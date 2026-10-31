// Copyright (c) 2026, Enfono Technologies and contributors
// For license information, please see license.txt

// "On Hold" -- a submitted Asset whose depreciation has not started (asset_depreciation_hold.py) --
// beside core's own Asset statuses in the list. The stored status stays core's ("Submitted"); the
// tick says it is on hold.

(function () {
	const settings = frappe.listview_settings["Asset"];
	if (!settings) return;
	settings.add_fields = (settings.add_fields || []).concat(["custom_depreciation_on_hold", "calculate_depreciation"]);
	const core_indicator = settings.get_indicator;
	settings.get_indicator = function (doc) {
		if (doc.docstatus === 1 && cint(doc.custom_depreciation_on_hold) && !cint(doc.calculate_depreciation)
			&& ["Submitted", "Partially Depreciated"].includes(doc.status)) {
			return [__("On Hold"), "orange", "custom_depreciation_on_hold,=,1|calculate_depreciation,=,0"];
		}
		return core_indicator ? core_indicator(doc) : undefined;
	};
})();
