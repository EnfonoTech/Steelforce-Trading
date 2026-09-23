// sf_trading/public/js/duplicate_check_popup.js
// GS Issue 15: warn while typing, on every master -- Item, Customer, Supplier, ledger (Account).
//
// Non-blocking: shows what already exists, does not stop the save. The real uniqueness rule is
// server-side (party_completeness.py); this is UX so a duplicate is spotted before it is typed in
// full, not just rejected after.
(function () {
	const FIELD_BY_DOCTYPE = {
		Item: "item_name",
		Customer: "customer_name",
		Supplier: "supplier_name",
		Account: "account_name",
	};
	const DEBOUNCE_MS = 400;
	const timers = {};

	function show_matches(frm, doctype, matches) {
		frm.dashboard.clear_headline();
		if (!matches || !matches.length) {
			return;
		}
		const names = matches.map((m) => m.label || m.name).join(", ");
		frm.dashboard.set_headline_alert(
			__("{0} similar {1} already exist: {2}", [matches.length, doctype, names]),
			"orange"
		);
	}

	Object.keys(FIELD_BY_DOCTYPE).forEach((doctype) => {
		const fieldname = FIELD_BY_DOCTYPE[doctype];
		const handlers = {};
		handlers[fieldname] = function (frm) {
			const value = frm.doc[fieldname];
			clearTimeout(timers[doctype]);
			if (!frm.doc.__islocal) {
				// Editing an existing record isn't where a duplicate gets created -- only warn
				// on a new one being typed in.
				return;
			}
			timers[doctype] = setTimeout(() => {
				frappe.call({
					method: "sf_trading.api.duplicate_check.similar_records",
					args: { doctype: doctype, value: value },
					callback: (r) => show_matches(frm, doctype, r.message),
				});
			}, DEBOUNCE_MS);
		};
		frappe.ui.form.on(doctype, handlers);
	});
})();
