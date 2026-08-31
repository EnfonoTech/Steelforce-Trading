// sf_trading: a Journal Entry row's Cost Center follows the Branch on that row.
//
// Core seeds `cost_center` on every new Journal Entry Account row from the child field's own
// default, `:Company`, which is the company's cost center -- Main - SFB here. The row already
// knows its Branch (Frappe fills it from the user's default User Permission), so the cost center
// is derivable; it was simply never derived.
//
// For a user who belongs to no branch, the pre-filled company default is CLEARED rather than
// replaced. The field is mandatory, so the desk then asks for a real choice instead of quietly
// booking head office. Main - SFB is still available -- it just has to be chosen.
//
// The server repeats both rules in sf_trading/journal_entry_cost_center.py, which is what covers
// rows added by the API, by Get Outstanding Invoices, and by any path that never fires _add.

(function () {
	function load(frm) {
		if (frm.__sf_je_cc) return Promise.resolve(frm.__sf_je_cc);
		return frappe
			.call({ method: "sf_trading.journal_entry_cost_center.form_defaults" })
			.then(function (r) {
				frm.__sf_je_cc = r.message || {};
				return frm.__sf_je_cc;
			});
	}

	function apply(frm, cdt, cdn) {
		var row = locals[cdt] && locals[cdt][cdn];
		if (!row) return;
		load(frm).then(function (cfg) {
			var map = cfg.branch_cost_centers || {};
			var company_default = (cfg.company_defaults || {})[frm.doc.company];
			var target = row.branch ? map[row.branch] : cfg.user_cost_center;

			if (target) {
				if (row.cost_center !== target) {
					frappe.model.set_value(cdt, cdn, "cost_center", target);
				}
			} else if (row.cost_center && row.cost_center === company_default) {
				// nobody chose this -- core pre-filled it. Clear it so the mandatory field asks.
				frappe.model.set_value(cdt, cdn, "cost_center", "");
			}
		});
	}

	frappe.ui.form.on("Journal Entry", {
		onload: function (frm) {
			load(frm);
		},
		accounts_add: function (frm, cdt, cdn) {
			apply(frm, cdt, cdn);
		},
	});

	frappe.ui.form.on("Journal Entry Account", {
		branch: function (frm, cdt, cdn) {
			apply(frm, cdt, cdn);
		},
	});
})();
