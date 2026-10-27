// sf_trading/public/js/party_gate_alert.js
// A red popup when a customer or supplier that would be refused is picked on a NEW voucher. Only that:
// no banner, nothing on a draft that is already saved, and no way to edit the party from the popup.
// The refusals themselves (documents, contact details, approval status) still come at save / submit /
// Approve from the server; this asks the very same gates early (sf_trading/party_alert.py), so a clerk
// learns it before filling the voucher in (2026-10-13: Stc Bahrain on Purchase Invoice 20020000378).
//
// A credit customer's documents are only demanded of a Credit or Cheque sale when the customer is
// allowed Cash sales without them. The Payment Mode comes after the customer, so a selling voucher with
// none yet is asked as a Cash one (only what refuses in every mode is raised at once), and asked again
// when the mode is picked. The same refusal is told once: switching between Credit and Cheque does not
// repeat it.

(function () {
	// the field that carries the party on each voucher a gate covers (party_alert.PARTY_FIELD)
	const FIELD = {
		"Purchase Order": "supplier",
		"Purchase Receipt": "supplier",
		"Purchase Invoice": "supplier",
		"Payment Entry": "party",
		"Sales Order": "customer",
		"Sales Invoice": "customer",
		"Delivery Note": "customer",
	};
	const SELLING = ["Sales Order", "Sales Invoice"];
	const MODE = "custom_payment_mode";

	// the header as the form holds it: scalars only, no child tables
	function header(doc) {
		const out = {};
		Object.keys(doc).forEach((key) => {
			const value = doc[key];
			const scalar = value === null || ["string", "number", "boolean"].includes(typeof value);
			if (scalar && (!key.startsWith("__") || key === "__islocal")) out[key] = value;
		});
		return out;
	}

	function popup(frm, data) {
		frappe.msgprint({
			title: __("{0} Details Incomplete", [__(data.party_type)]),
			indicator: "red",
			message:
				"<p>" +
				__("This {0} cannot go forward until the {1} is completed:", [__(frm.doctype), __(data.party_type)]) +
				"</p><ul>" +
				data.problems.map((text) => "<li>" + text + "</li>").join("") +
				"</ul>" +
				(data.hints || []).map((text) => "<p>" + text + "</p>").join(""),
		});
	}

	function check(frm) {
		const field = FIELD[frm.doctype];
		const party = field && frm.doc[field];
		// only a voucher being raised: a saved draft has been through this already
		if (!party || !frm.is_new() || (frm.doctype === "Payment Entry" && frm.doc.party_type !== "Supplier")) return;
		const doc = header(frm.doc);
		// no mode picked yet: ask as the most permissive one
		if (SELLING.includes(frm.doctype) && !String(doc[MODE] || "").trim()) doc[MODE] = "Cash";
		// the same question already out (a script loaded twice registers its handlers twice)
		const token = [party, doc[MODE] || ""].join("|");
		if (frm.__sf_party_alert_token === token) return;
		frm.__sf_party_alert_token = token;

		frappe.call({
			method: "sf_trading.party_alert.get_party_alert",
			args: { doctype: frm.doctype, doc: doc },
			// a preview must never be what interrupts the user
			silent: true,
			error: () => {},
			always: () => {
				frm.__sf_party_alert_token = null;
			},
			callback: (r) => {
				const data = r.message || {};
				if (frm.doc[field] !== party || data.party !== party) return; // they have moved on
				const problems = data.problems || [];
				const told = problems.length ? party + "|" + problems.join("|") : "";
				if (told && told !== frm.__sf_party_alert_told) popup(frm, data);
				frm.__sf_party_alert_told = told;
			},
		});
	}

	Object.keys(FIELD).forEach((doctype) => {
		const events = {};
		events[FIELD[doctype]] = (frm) => check(frm);
		if (SELLING.includes(doctype)) events[MODE] = (frm) => check(frm);
		frappe.ui.form.on(doctype, events);
	});
})();
