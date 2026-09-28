// Restricts the Sales Team grid's "Sales Person" Link to salespersons valid for the document's
// Company (sf_trading/salesperson_by_company.py). A Salesperson with no "Applicable Companies"
// rows is unrestricted and stays offered everywhere -- see that module's docstring for why an
// empty child table must mean "no restriction", not "restricted to nothing".
//
// Loaded on Sales Invoice, Sales Order and Delivery Note via hooks.py doctype_js. Quotation carries
// the same custom_sales_person header field (sales_team_sync.py) but has no sales_team child table
// of its own on this bench, so there is nothing to restrict there.

function sf_restrict_sales_person_by_company(frm) {
	frm.set_query("sales_person", "sales_team", function (doc) {
		return {
			query: "sf_trading.salesperson_by_company.get_salesperson_query",
			filters: { company: doc.company || "" },
		};
	});
}

["Sales Invoice", "Sales Order", "Delivery Note"].forEach(function (doctype) {
	frappe.ui.form.on(doctype, {
		onload: function (frm) {
			sf_restrict_sales_person_by_company(frm);
		},
		refresh: function (frm) {
			sf_restrict_sales_person_by_company(frm);
		},
	});
});
