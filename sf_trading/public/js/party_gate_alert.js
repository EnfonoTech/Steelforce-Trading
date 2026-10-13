// sf_trading/public/js/party_gate_alert.js
// A red alert the moment a customer or supplier is picked on a voucher that would refuse it, and a red
// banner on the draft for as long as it would. The refusals themselves (documents, contact details,
// approval status) still come at save / submit / Approve from the server; this asks the very same gates
// early (sf_trading/party_alert.py), so a clerk learns it before filling the voucher in and an approver
// before clicking Approve (2026-10-13: Stc Bahrain on Purchase Invoice 20020000378).
//
// The alert offers the Quick Edit dialog of the party (customer_quick_edit.js / supplier_quick_edit.js,
// which publish their openers on window), and re-asks when it is saved, so the red goes away the moment
// the party is complete. Only drafts are checked: a submitted voucher has already passed.

(function () {
	// the field that carries the party on each voucher a gate covers (party_alert.PARTY_FIELD)
	const FIELD = {
		"Purchase Order": "supplier",
		"Purchase Receipt": "supplier",
		"Purchase Invoice": "supplier",
		"Supplier Quotation": "supplier",
		"Payment Entry": "party",
		"Sales Order": "customer",
		"Sales Invoice": "customer",
	};
	// fields a gate reads besides the party: changing one can raise the alert or clear it
	const WATCH = ["is_return", "is_opening", "custom_payment_mode", "payment_type", "party_type"];

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

	function open_quick_edit(frm, party_type, party) {
		const opener = party_type === "Customer" ? window.sf_open_customer_quick_edit : window.sf_open_supplier_quick_edit;
		if (!opener) {
			// this form does not carry the dialog: the master itself is the next best place
			window.open(frappe.utils.get_form_link(party_type, party), "_blank");
			return;
		}
		// whatever the dialog saved, ask again: the banner goes when the party is complete
		opener(party, () => check(frm, { announce: true }));
	}

	function banner(frm, data) {
		const $box = frm.layout && frm.layout.message;
		if (!$box) return;
		$box.find(".sf-party-alert").remove();
		const problems = (data && data.problems) || [];
		if (!problems.length) {
			if (!$box.children().length) $box.addClass("hidden");
			return;
		}
		const fix = data.can_fix
			? ' <a href="#" class="sf-party-alert-fix">' + __("Update {0} now", [__(data.party_type)]) + "</a>"
			: " " + __("Ask someone who can edit the {0}.", [__(data.party_type)]);
		frm.layout.show_message(
			"<div><b>" +
				__("{0} {1} is incomplete", [__(data.party_type), frappe.utils.escape_html(data.party_name)]) +
				"</b> &mdash; " +
				__("this {0} will be refused until it is fixed.", [__(frm.doctype)]) +
				'<ul style="margin: 4px 0 4px 16px;">' +
				problems.map((text) => "<li>" + text + "</li>").join("") +
				"</ul>" +
				fix +
				"</div>",
			"red",
			true
		);
		const $mine = $box.children().last().addClass("sf-party-alert");
		$mine.find(".sf-party-alert-fix").on("click", (event) => {
			event.preventDefault();
			open_quick_edit(frm, data.party_type, data.party);
		});
	}

	function popup(frm, data) {
		const message =
			"<p>" +
			__("This {0} cannot go forward until the {1} is completed:", [__(frm.doctype), __(data.party_type)]) +
			"</p><ul>" +
			data.problems.map((text) => "<li>" + text + "</li>").join("") +
			"</ul>";
		const alert = { title: __("{0} Details Incomplete", [__(data.party_type)]), indicator: "red", message: message };
		if (data.can_fix) {
			alert.primary_action = {
				label: __("Complete {0} Now", [__(data.party_type)]),
				action: () => {
					frappe.hide_msgprint();
					open_quick_edit(frm, data.party_type, data.party);
				},
			};
		}
		frappe.msgprint(alert);
	}

	// opts.picked: the user has just chosen the party (alert); otherwise the draft was opened or a
	// flag changed (banner only). opts.announce: say so when the party turns out complete.
	function check(frm, opts) {
		opts = opts || {};
		const field = FIELD[frm.doctype];
		const party = field && frm.doc[field];
		if (!party || frm.doc.docstatus !== 0 || (frm.doctype === "Payment Entry" && frm.doc.party_type !== "Supplier")) {
			banner(frm, null);
			return;
		}
		// the same question already out (a script loaded twice registers its handlers twice)
		const token = [party, opts.picked ? 1 : 0].concat(WATCH.map((f) => frm.doc[f])).join("|");
		if (frm.__sf_party_alert_token === token) return;
		frm.__sf_party_alert_token = token;

		frappe.call({
			method: "sf_trading.party_alert.get_party_alert",
			args: { doctype: frm.doctype, doc: header(frm.doc) },
			// a preview must never be what interrupts the user
			silent: true,
			error: () => {},
			always: () => {
				frm.__sf_party_alert_token = null;
			},
			callback: (r) => {
				const data = r.message || {};
				if (frm.doc[field] !== party || data.party !== party) return; // they have moved on
				banner(frm, data);
				if ((data.problems || []).length) {
					if (opts.picked) popup(frm, data);
				} else if (opts.announce) {
					frappe.show_alert({ message: __("{0} is complete", [frappe.utils.escape_html(data.party_name)]), indicator: "green" }, 5);
				}
			},
		});
	}

	Object.keys(FIELD).forEach((doctype) => {
		const events = { refresh: (frm) => check(frm) };
		events[FIELD[doctype]] = (frm) => check(frm, { picked: true });
		WATCH.forEach((fieldname) => {
			events[fieldname] = (frm) => check(frm);
		});
		frappe.ui.form.on(doctype, events);
	});
})();
