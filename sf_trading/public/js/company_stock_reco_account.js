// The Stock Reconciliation Difference Account is picked from this company's own chart of
// accounts, ledgers only -- the server refuses anything else (stock_reconciliation_account.py).
frappe.ui.form.on("Company", {
	setup(frm) {
		frm.set_query("custom_stock_reconciliation_difference_account", () => ({
			filters: { company: frm.doc.name, is_group: 0 },
		}));
	},
});
