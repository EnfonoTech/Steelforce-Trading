// sf_trading/sf_trading/doctype/party_master_rule_set/party_master_rule_set.js
// Test Rules: pick a Customer or Supplier and see what this rule set says of it at each stage -- and, for the
// approval, whether it agrees with the code's own gate (sf_trading/party_rules.py).

frappe.ui.form.on("Party Master Rule Set", {
	refresh(frm) {
		if (frm.is_new()) return;
		frm.dashboard.add_comment(
			frm.doc.enabled
				? __("Enabled: this rule set answers for {0}.", [__(frm.doc.party_type)])
				: __("Not enabled: the code's own rules still answer for {0}. This set is read only by Test Rules and the Party Rule Set Diff report.", [__(frm.doc.party_type)]),
			frm.doc.enabled ? "green" : "orange",
			true
		);
		frm.add_custom_button(__("Test Rules"), () => test_rules(frm));
	},
});

function test_rules(frm) {
	frappe.prompt(
		{ fieldname: "party", fieldtype: "Link", options: frm.doc.party_type, label: __(frm.doc.party_type), reqd: 1 },
		(values) =>
			frappe.call({
				method: "sf_trading.party_rules.test_rules",
				args: { party_type: frm.doc.party_type, party: values.party },
				freeze: true,
				callback: (r) => show_result(r.message || {}),
			}),
		__("Test Rules"),
		__("Test")
	);
}

function list(title, items) {
	const body = items.length
		? "<ul>" + items.map((t) => "<li>" + frappe.utils.escape_html(t) + "</li>").join("") + "</ul>"
		: "<p class='text-muted'>" + __("Nothing missing.") + "</p>";
	return "<h6>" + frappe.utils.escape_html(title) + "</h6>" + body;
}

function show_result(data) {
	const stages = data.stages || {};
	let html = list(__("At Save"), stages.save || []) + list(__("At Approval"), stages.approval || []) + list(__("At Voucher"), stages.voucher || []);
	html += list(__("Format checks failed"), data.format || []);
	html += list(__("Unique checks failed"), (data.unique || []).map((u) => u.field + " " + u.value + " -> " + u.clash));
	html +=
		"<hr>" +
		list(__("The code's own approval gate says"), data.legacy_approval || []) +
		"<p><b>" +
		(data.approval_agrees ? __("The rule set agrees with the code at approval.") : __("The rule set and the code DIFFER at approval.")) +
		"</b></p>";
	frappe.msgprint({
		title: __("Rules for {0}", [data.party]),
		indicator: data.approval_agrees ? "green" : "red",
		message: html,
	});
}
