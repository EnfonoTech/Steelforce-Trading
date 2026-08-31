// sf_trading: a Journal Entry row's Cost Center follows the Branch on that row.
//
// Core seeds `cost_center` on every new Journal Entry Account row from the child field's own
// default, `:Company`, which is the company's cost center -- Main - SFB here. The row already
// knows its Branch (Frappe fills it from the user's default User Permission), so the cost center
// is derivable; it was simply never derived.
//
// Two pieces of desk plumbing decide how this has to be wired, both verified in a live browser
// against the running site rather than assumed:
//
//   1. `grid.add_new_row()` dispatches the add event as
//      `trigger("accounts_add", <CHILD doctype>, name)`, and `ScriptManager.get_handlers()` only
//      looks in `frappe.ui.form.handlers[<that doctype>]`. So `accounts_add` MUST be registered on
//      "Journal Entry Account". Registered on "Journal Entry" it is never found -- erpnext's own
//      `items_add` works only because it is an old-style cscript method, which get_handlers picks
//      up from `frm.cscript` whatever the doctype.
//   2. erpnext copies the accounting dimensions AND the cost center from ROW 1 onto each new row
//      (`copy_from_first_row`), from an old-style cscript handler -- and old-style handlers run
//      AFTER the new-style ones. So a value written in `accounts_add` is overwritten a moment
//      later by row 1's. The sweep is therefore deferred to the next tick, and it fixes row 1
//      too, which is what makes erpnext's own copying land on the right value.
//
// For a user who belongs to no branch, the pre-filled company default is CLEARED rather than
// replaced. The field is mandatory, so the desk then asks for a real choice instead of quietly
// booking head office. Main - SFB is still available -- it just has to be chosen. Clearing happens
// while the user is editing, never at save time, where an emptied mandatory field would read as
// the form refusing to save for no visible reason.
//
// The server repeats the same rules in sf_trading/journal_entry_cost_center.py, which is what
// covers rows added by the API, by an import, and by any path that fires no client event at all.

(function () {
	function load(frm) {
		if (frm.__sf_je_cc) return Promise.resolve(frm.__sf_je_cc);
		if (frm.__sf_je_cc_pending) return frm.__sf_je_cc_pending;
		frm.__sf_je_cc_pending = frappe
			.call({ method: "sf_trading.journal_entry_cost_center.form_defaults" })
			.then(function (r) {
				frm.__sf_je_cc = r.message || {};
				return frm.__sf_je_cc;
			});
		return frm.__sf_je_cc_pending;
	}

	// `may_clear` is false at save time: emptying a mandatory field there would block the save
	// with no visible cause. While editing it is the whole point -- the desk asks for a choice.
	function sweep(frm, may_clear) {
		if (!frm || !frm.doc || frm.doc.docstatus > 1) return;
		load(frm).then(function (cfg) {
			var map = cfg.branch_cost_centers || {};
			var mine = cfg.user_cost_center;
			var company_default = (cfg.company_defaults || {})[frm.doc.company];
			var changed = false;

			(frm.doc.accounts || []).forEach(function (row) {
				var target = row.branch ? map[row.branch] : mine;
				if (target) {
					if (row.cost_center !== target) {
						row.cost_center = target;
						changed = true;
					}
					if (!row.branch && mine && cfg.user_branch) {
						row.branch = cfg.user_branch;
						changed = true;
					}
				} else if (may_clear && row.cost_center && row.cost_center === company_default) {
					// nobody chose this -- core pre-filled it. Clear it so the mandatory field asks.
					row.cost_center = "";
					changed = true;
				}
			});

			if (changed) frm.refresh_field("accounts");
		});
	}

	// deferred: erpnext's old-style accounts_add copies row 1's dimensions and cost centre over
	// this row right after the new-style handlers finish, so the last word has to come later
	function sweep_later(frm) {
		setTimeout(function () {
			sweep(frm, true);
		}, 0);
	}

	frappe.ui.form.on("Journal Entry", {
		onload: function (frm) {
			load(frm);
		},
		refresh: function (frm) {
			// only while the entry is still being created: writing values into a saved document on
			// every refresh would mark a clean form dirty for no reason the user asked for
			if (frm.is_new()) sweep_later(frm);
		},
		company: function (frm) {
			if (frm.is_new()) sweep_later(frm);
		},
		before_save: function (frm) {
			sweep(frm, false);
		},
	});

	frappe.ui.form.on("Journal Entry Account", {
		// registered here, not on the parent -- see note 1 above
		accounts_add: function (frm) {
			sweep_later(frm);
		},
		branch: function (frm) {
			sweep_later(frm);
		},
		// a row opened in the grid's detail form: catches rows that arrived by any other path
		form_render: function (frm) {
			if (frm.is_new()) sweep_later(frm);
		},
	});
})();
