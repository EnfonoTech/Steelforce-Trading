// Copyright (c) 2026, Enfono Technologies and contributors
// For license information, please see license.txt

// View > Supplier Quotation Comparison Extended on a Supplier Quotation and on a purchase Material Request: the report
// opened already filtered to what this document belongs to, from the day it was raised to today.
// The report is sf_trading/sf_trading/report/supplier_quotation_comparison_extended.
(function () {
	const REPORT = "Supplier Quotation Comparison Extended";

	function open_report(filters) {
		frappe.set_route("query-report", REPORT, filters);
	}

	frappe.ui.form.on("Supplier Quotation", {
		refresh(frm) {
			if (frm.doc.docstatus === 2 || frm.is_new()) return;
			frm.add_custom_button(
				__(REPORT),
				() => {
					const filters = {
						company: frm.doc.company,
						from_date: frm.doc.transaction_date,
						to_date: frappe.datetime.get_today(),
						status: frm.doc.docstatus === 0 ? "Draft and Submitted" : "Submitted",
					};
					// the Request for Quotation it answers, so it is compared with the others; failing that, on its own
					const rfq = (frm.doc.items || []).map((row) => row.request_for_quotation).find((name) => name);
					if (rfq) {
						filters.request_for_quotation = rfq;
					} else {
						filters.supplier_quotation = [frm.doc.name];
					}
					open_report(filters);
				},
				__("View")
			);
		},
	});

	frappe.ui.form.on("Material Request", {
		refresh(frm) {
			if (frm.doc.docstatus !== 1 || frm.doc.material_request_type !== "Purchase") return;
			frm.add_custom_button(
				__(REPORT),
				() =>
					open_report({
						company: frm.doc.company,
						material_request: frm.doc.name,
						from_date: frm.doc.transaction_date,
						to_date: frappe.datetime.get_today(),
					}),
				__("View")
			);
		},
	});
})();
