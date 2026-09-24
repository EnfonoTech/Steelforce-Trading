// sf_trading/public/js/party_mobile_no_prefill.js
// Customer/Supplier form — a client can make core's own `mobile_no` mandatory straight from the
// live bench's Customize Form (e.g. Customer-mobile_no-reqd on prod, added 2026-09-20 by the
// client's own accountant, never exported to a fixture). Core never wires that field to anything,
// so for every party whose real phone lives only on a linked Contact/Address, mobile_no stays
// blank forever and the desk refuses every save with "Mobile No is required" -- even though the
// G11 phone cache (custom_mobile_no, sf_trading/party_contact_cache.py) already has that same
// number. Fill it in from there instead of asking someone to retype a number the system already
// has. Never overwrites a mobile_no someone already typed; only fills it while blank.

frappe.ui.form.on("Customer", {
	refresh: prefill_mobile_no,
});

frappe.ui.form.on("Supplier", {
	refresh: prefill_mobile_no,
});

function prefill_mobile_no(frm) {
	if (!frm.doc.mobile_no && frm.doc.custom_mobile_no) {
		frm.set_value("mobile_no", frm.doc.custom_mobile_no);
	}
}
