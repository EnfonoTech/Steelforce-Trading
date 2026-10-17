// The branch decides which price lists a document may use, and which one it opens on.
//
// The mapping lives on Branch Configuration (sf_trading/branch_price_list.py). Two things happen
// here, both the moment a branch is chosen:
//
//   * the Price List field is restricted to the lists that branch is configured for — a branch with
//     none configured keeps every list, exactly as before;
//   * the branch's default list is applied, which hands the re-pricing of every row to ERPNext's
//     own price-list trigger.
//
// The server does the same at before_validate, for anything that does not come through a form.

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

function sf_price_kind(frm) {
	return SF_BRANCH_PRICE_FIELD[frm.doctype] === "buying_price_list" ? "buying" : "selling";
}

// Only the branch's own lists are offered. Set once per form; the query re-reads frm.doc.branch
// every time the field is opened, so switching branch needs no re-registration.
function sf_restrict_price_list_field(frm) {
	const fieldname = SF_BRANCH_PRICE_FIELD[frm.doctype];
	if (!fieldname || frm.__sf_price_query_set) return;
	frm.__sf_price_query_set = true;

	frm.set_query(fieldname, function () {
		return {
			query: "sf_trading.branch_price_list.branch_price_list_query",
			filters: { branch: frm.doc.branch || "", kind: sf_price_kind(frm) },
		};
	});
}

function sf_apply_branch_price_list(frm) {
	const fieldname = SF_BRANCH_PRICE_FIELD[frm.doctype];
	if (!fieldname || frm.doc.docstatus !== 0) return;
	if (!frm.doc.branch) return;

	frappe.call({
		method: "sf_trading.branch_price_list.get_branch_price_list",
		args: { branch: frm.doc.branch, kind: sf_price_kind(frm) },
		callback(r) {
			const state = (r && r.message) || {};
			const wanted = state.default;
			if (!wanted) return;
			// a list from this branch's own set was chosen on purpose; leave it
			if ((state.allowed || []).includes(frm.doc[fieldname])) return;
			if (wanted === frm.doc[fieldname]) return;

			frm.set_value(fieldname, wanted).then(function () {
				frappe.show_alert(
					{
						message: __("Priced from {0} for branch {1}.", [wanted, frm.doc.branch]),
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
		onload(frm) {
			sf_restrict_price_list_field(frm);
		},
		refresh(frm) {
			sf_restrict_price_list_field(frm);
		},
		branch(frm) {
			sf_apply_branch_price_list(frm);
		},
	});
});
