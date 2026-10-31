// Copyright (c) 2026, Enfono Technologies and contributors
// For license information, please see license.txt

// View > Supplier Quotation Comparison Extended on a submitted Request for Quotation: opens the report already
// filtered to this request, from the day it was raised to today, so the quotations that came back are one click away.
// The report is sf_trading/sf_trading/report/supplier_quotation_comparison_extended.
frappe.ui.form.on("Request for Quotation", {
	refresh(frm) {
		if (frm.doc.docstatus !== 1) return;
		frm.add_custom_button(
			__("Supplier Quotation Comparison Extended"),
			() => {
				frappe.set_route("query-report", "Supplier Quotation Comparison Extended", {
					company: frm.doc.company,
					request_for_quotation: frm.doc.name,
					from_date: frm.doc.transaction_date,
					to_date: frappe.datetime.get_today(),
				});
			},
			__("View")
		);
	},
});
