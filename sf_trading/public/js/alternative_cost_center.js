// sf_trading/public/js/alternative_cost_center.js
// An Alternative Cost Center is a cost centre of ANOTHER company (sf_trading/alternative_cost_center.py): the
// field, on the document and on its rows, offers only enabled cost centres of other companies. The forms it is
// offered on come from the boot (frappe.boot.sf_alt_cc_doctypes: every document type that carries the field),
// not from a list kept here, so no form is missed; the short list below is only what a stale boot falls back to.
// UI only: the server refuses the same company, a disabled one and anything that is not a mirror.

frappe.provide("sf_trading.alt_cost_center");

(function (acc) {
	acc.FIELD = "alternative_cost_center";
	acc.FALLBACK = [
		"Journal Entry",
		"Sales Invoice",
		"Purchase Invoice",
		"Payment Entry",
		"Expense Claim",
		"Stock Entry",
		"Purchase Receipt",
		"Delivery Note",
	];

	acc.doctypes = function () {
		const booted = frappe.boot && frappe.boot.sf_alt_cc_doctypes;
		return booted && booted.length ? booted : acc.FALLBACK;
	};

	// another company's cost centres, enabled; a document with no company yet is offered all of them
	acc.query = (frm) => () => ({
		filters: { cc_company: ["!=", frm.doc.company || ""], disabled: 0 },
	});

	acc.setup = function (frm) {
		const meta_fields = (frm.meta && frm.meta.fields) || [];
		if (meta_fields.some((df) => df.fieldname === acc.FIELD)) {
			frm.set_query(acc.FIELD, acc.query(frm));
		}
		meta_fields
			.filter((df) => df.fieldtype === "Table" || df.fieldtype === "Table MultiSelect")
			.forEach((table) => {
				const child = frappe.get_meta(table.options);
				if (child && (child.fields || []).some((df) => df.fieldname === acc.FIELD)) {
					frm.set_query(acc.FIELD, table.fieldname, acc.query(frm));
				}
			});
	};

	acc.register = function (doctype) {
		frappe.ui.form.on(doctype, {
			setup(frm) {
				acc.setup(frm);
			},
		});
	};

	acc.doctypes().forEach(acc.register);
})(sf_trading.alt_cost_center);
