// sf_trading/public/js/master_approval.js
// The Customer / Supplier / Item approval on the master's form (sf_trading/master_approval.py).
// Send for Approval, Approve and Reject are permission_manager's PM Workflow buttons; this adds only what
// the workflow cannot say: what the master still lacks before it can go for approval, an Approve for an
// approver on a master nobody sent (an older one under Strict Validation), and Bypass Approval for a
// System Manager or someone on the bypass roster. The server re-checks every one -- a button is an offer.

(function () {
	const GROUP = __("Approval");

	function bypass(frm) {
		frappe.prompt(
			{ fieldname: "reason", fieldtype: "Small Text", label: __("Reason"), reqd: 1 },
			(values) =>
				frappe.call({
					method: "sf_trading.master_approval.bypass_approval",
					args: { doctype: frm.doctype, names: [frm.doc.name], reason: values.reason },
					freeze: true,
					callback: () => {
						frappe.show_alert({ message: __("Approval bypassed"), indicator: "orange" });
						frm.reload_doc();
					},
				}),
			__("Bypass Approval"),
			__("Bypass")
		);
	}

	function remove_bypass(frm) {
		frappe.confirm(__("Hold {0} to the approval again?", [frm.doc.name]), () =>
			frappe.call({
				method: "sf_trading.master_approval.remove_bypass",
				args: { doctype: frm.doctype, name: frm.doc.name },
				freeze: true,
				callback: () => frm.reload_doc(),
			})
		);
	}

	function approve(frm) {
		frappe.call({
			method: "sf_trading.master_approval.approve_masters",
			args: { doctype: frm.doctype, names: [frm.doc.name] },
			freeze: true,
			callback: (r) => {
				const skipped = (r.message || {}).skipped || [];
				if (skipped.length) {
					frappe.msgprint({ title: __("Not Approved"), indicator: "red", message: skipped[0].reason });
					return;
				}
				frappe.show_alert({ message: __("Approved"), indicator: "green" });
				frm.reload_doc();
			},
		});
	}

	function render(frm, state) {
		if (!state.enabled) return;
		if (state.bypassed) {
			frm.dashboard.add_comment(__("Approval bypassed: this {0} is usable without the approval.", [__(frm.doctype)]), "orange", true);
		} else if (state.covered && !state.approved) {
			let note = __("Not approved yet: this {0} cannot be used on orders, deliveries, invoices or payments until it is.", [__(frm.doctype)]);
			if ((state.missing || []).length) {
				note += " " + __("Before it can go for approval it needs: {0}.", [state.missing.join(", ")]);
			}
			frm.dashboard.add_comment(note, "red", true);
		}

		// an approver on a master nobody sent (an older one under Strict Validation), or a System Manager; a
		// workflow approver has PM's own Approve / Reject on a Pending Approval master
		const pm_offers = state.status === "Pending Approval" && state.workflow_approver;
		if (state.covered && !state.approved && !state.bypassed && state.can_approve && !pm_offers) {
			frm.add_custom_button(__("Approve"), () => approve(frm), GROUP);
		}
		if (state.can_bypass) {
			if (state.bypassed) frm.add_custom_button(__("Remove Bypass"), () => remove_bypass(frm), GROUP);
			else if (!state.approved) frm.add_custom_button(__("Bypass Approval"), () => bypass(frm), GROUP);
		}
	}

	function refresh(frm) {
		if (frm.is_new()) return;
		const name = frm.doc.name;
		frappe.call({
			method: "sf_trading.master_approval.get_approval_state",
			args: { doctype: frm.doctype, name: name },
			callback: (r) => {
				// the user may have moved to another record while this was out
				if (frm.doc.name !== name) return;
				render(frm, r.message || {});
			},
		});
	}

	["Customer", "Supplier", "Item"].forEach((doctype) => frappe.ui.form.on(doctype, { refresh }));
})();
