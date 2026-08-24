// Pick up the branch's price list the moment the branch is chosen.
//
// The server does the same thing at before_validate (sf_trading/branch_price_list.py), but only
// on save — and a cashier who picks a branch and then adds items would otherwise type against
// the wrong prices and watch them all change when they save. Setting selling_price_list /
// buying_price_list here hands the work to ERPNext's own price-list trigger, which re-prices
// every row exactly as it does when the field is changed by hand.
//
// A price list with no branches on it applies everywhere, so nothing happens on a site that has
// not filled the table in — which is the point: branch is not mandatory anywhere in this.

const SF_BRANCH_PRICE_FIELD = {
	Quotation: "selling_price_list",
	"Sales Order": "selling_price_list",
	"Delivery Note": "selling_price_list",
	"Sales Invoice": "selling_price_list",
	"Supplier Quotation": "buying_price_list",
	"Purchase Order": "buying_price_list",
	"Purchase Receipt": "buying_price_list",
	"Purchase Invoice": "buying_price_list",
};

function sf_apply_branch_price_list(frm) {
	const fieldname = SF_BRANCH_PRICE_FIELD[frm.doctype];
	if (!fieldname || frm.doc.docstatus !== 0) return;
	if (!frm.doc.branch) return;

	const kind = fieldname === "selling_price_list" ? "selling" : "buying";
	frappe.call({
		method: "sf_trading.branch_price_list.get_branch_price_list",
		args: { branch: frm.doc.branch, kind },
		callback(r) {
			const price_list = r && r.message;
			if (!price_list || price_list === frm.doc[fieldname]) return;
			frm.set_value(fieldname, price_list).then(function () {
				frappe.show_alert(
					{
						message: __("Priced from {0} for branch {1}.", [price_list, frm.doc.branch]),
						indicator: "blue",
					},
					4
				);
			});
		},
	});
}

Object.keys(SF_BRANCH_PRICE_FIELD).forEach(function (doctype) {
	frappe.ui.form.on(doctype, {
		branch(frm) {
			sf_apply_branch_price_list(frm);
		},
	});
});
