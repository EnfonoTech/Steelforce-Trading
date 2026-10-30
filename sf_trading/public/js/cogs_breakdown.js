// sf_trading/public/js/cogs_breakdown.js
// The doors into the two cost reports: COGS Calculation on a submitted Delivery Note or Sales Invoice,
// Transfer Cost Breakdown on a submitted Material Transfer. Each opens its report with the voucher's
// Company, number and date already filled -- see sf_trading/cogs_breakdown.py.

function sf_open_cogs_breakdown(frm) {
	frappe.set_route("query-report", "COGS Cost Breakdown", {
		company: frm.doc.company,
		voucher_type: frm.doctype,
		voucher_no: frm.doc.name,
		from_date: frm.doc.posting_date,
		to_date: frm.doc.posting_date,
	});
}

["Delivery Note", "Sales Invoice"].forEach((doctype) => {
	frappe.ui.form.on(doctype, {
		refresh(frm) {
			if (frm.doc.docstatus !== 1) return;
			frm.add_custom_button(
				__("COGS Calculation"),
				() => sf_open_cogs_breakdown(frm),
				__("View")
			);
		},
	});
});

frappe.ui.form.on("Stock Entry", {
	refresh(frm) {
		if (frm.doc.docstatus !== 1 || frm.doc.purpose !== "Material Transfer") return;
		frm.add_custom_button(
			__("Transfer Cost Breakdown"),
			() =>
				frappe.set_route("query-report", "Transfer Cost Breakdown", {
					company: frm.doc.company,
					stock_entry: frm.doc.name,
					from_date: frm.doc.posting_date,
					to_date: frm.doc.posting_date,
				}),
			__("View")
		);
	},
});
