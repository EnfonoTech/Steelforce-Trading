// sf_trading/public/js/alternative_cost_center.js
// An Alternative Cost Center is a cost centre of ANOTHER company (sf_trading/alternative_cost_center.py): the
// field, on the voucher and on its rows, offers only enabled cost centres of other companies.
// UI only: the server refuses the same company, a disabled one and anything that is not a mirror.

frappe.provide("sf_trading.alt_cost_center");

(function (acc) {
	acc.FIELD = "alternative_cost_center";
	acc.VOUCHERS = [
		"Journal Entry",
		"Sales Invoice",
		"Purchase Invoice",
		"Payment Entry",
		"Expense Claim",
		"Stock Entry",
		"Purchase Receipt",
		"Delivery Note",
	];

	acc.query = (frm) => () => ({
		filters: { company: ["!=", frm.doc.company || ""], disabled: 0 },
	});

	acc.setup = function (frm) {
		const meta_fields = (frm.meta && frm.meta.fields) || [];
		if (meta_fields.some((df) => df.fieldname === acc.FIELD)) {
			frm.set_query(acc.FIELD, acc.query(frm));
		}
		meta_fields
			.filter((df) => df.fieldtype === "Table")
			.forEach((table) => {
				const child = frappe.get_meta(table.options);
				if (child && (child.fields || []).some((df) => df.fieldname === acc.FIELD)) {
					frm.set_query(acc.FIELD, table.fieldname, acc.query(frm));
				}
			});
	};

	acc.VOUCHERS.forEach((doctype) => {
		frappe.ui.form.on(doctype, {
			setup(frm) {
				acc.setup(frm);
			},
		});
	});
})(sf_trading.alt_cost_center);
