// sf_trading/sf_trading/doctype/ticket/ticket.js
// The ticket's buttons: Assign / Hand Over, Start Work, Put On Hold, Resolve, Confirm & Close,
// Reopen. They are offers only -- who may do which is decided again on the server
// (sf_trading/ticket.py validate_change), so a hidden button is never the gate.
(() => {
	const DONE = ["Resolved", "Closed"];
	const desk_users = () => ({ filters: { user_type: "System User", enabled: 1 } });

	frappe.ui.form.on("Ticket", {
		setup(frm) {
			frm.set_query("assignee", desk_users);
			frm.set_query("raised_by", desk_users);
			frm.set_query("user", "watchers", desk_users);
			frm.set_query("reference_doctype", () => ({ filters: { istable: 0, issingle: 0 } }));
		},

		refresh(frm) {
			const role = ticket_role(frm);
			frm.toggle_enable("raised_by", role.manager);
			frm.toggle_enable("assignee", role.manager || role.assignee);
			frm.toggle_enable("status", role.manager || role.assignee);
			set_intro(frm, role);
			if (!frm.is_new()) {
				add_buttons(frm, role);
			}
		},

		reference_doctype(frm) {
			frm.set_value("reference_name", null);
		},
	});

	function ticket_role(frm) {
		const me = frappe.session.user;
		const doc = frm.doc;
		const docinfo = (!frm.is_new() && frm.get_docinfo && frm.get_docinfo()) || {};
		return {
			manager:
				me === "Administrator" ||
				frappe.user.has_role("Ticket Manager") ||
				frappe.user.has_role("System Manager"),
			assignee: doc.assignee === me || (docinfo.assignments || []).some((a) => a.owner === me),
			reporter: doc.raised_by === me || doc.owner === me,
		};
	}

	function set_intro(frm, role) {
		frm.set_intro("");
		const doc = frm.doc;
		if (frm.is_new()) {
			frm.set_intro(
				__("Tell us what went wrong. A Ticket Manager will allocate it, and you will be notified at every step."),
				"blue"
			);
			return;
		}
		const reply_hint = __("Reply in the comment box at the bottom. Everyone on this ticket is notified.");
		const with_whom = frappe.utils.escape_html(doc.assignee_name || doc.assignee || "");
		if (doc.status === "Resolved") {
			const ask = role.reporter ? " " + __("Confirm & Close if it is fixed, or Reopen it.") : "";
			frm.set_intro(__("Resolved.") + ask + " " + reply_hint, "green");
		} else if (doc.status === "Closed") {
			frm.set_intro(__("Closed. Reopen it if the problem comes back."), "gray");
		} else if (!doc.assignee) {
			frm.set_intro(__("Waiting to be allocated.") + " " + reply_hint, "orange");
		} else {
			frm.set_intro(__("With {0}.", [with_whom]) + " " + reply_hint, "blue");
		}
	}

	function add_buttons(frm, role) {
		const doc = frm.doc;
		const done = DONE.includes(doc.status);
		const works_it = role.manager || role.assignee;
		const actions = __("Actions");

		if (!done && role.manager) {
			frm.add_custom_button(doc.assignee ? __("Reassign") : __("Assign"), () => assign_dialog(frm));
			if (doc.assignee !== frappe.session.user) {
				frm.add_custom_button(
					__("Assign to Me"),
					() => run(frm, "assign", { user: frappe.session.user }),
					actions
				);
			}
		} else if (!done && role.assignee) {
			frm.add_custom_button(__("Hand Over"), () => assign_dialog(frm));
		}

		if (!done && works_it) {
			if (doc.status !== "In Progress") {
				frm.add_custom_button(__("Start Work"), () => run(frm, "set_status", { status: "In Progress" }), actions);
			}
			if (doc.status !== "On Hold") {
				frm.add_custom_button(
					__("Put On Hold"),
					() => note_dialog(frm, "On Hold", __("Put On Hold"), __("What are you waiting for?"), false),
					actions
				);
			}
			frm.add_custom_button(__("Resolve"), () => resolve_dialog(frm));
		}

		if (doc.status === "Resolved" && (role.reporter || role.manager)) {
			frm.add_custom_button(__("Confirm & Close"), () => run(frm, "set_status", { status: "Closed" }));
		}
		if (done && (role.reporter || works_it)) {
			frm.add_custom_button(__("Reopen"), () =>
				note_dialog(frm, "Open", __("Reopen Ticket"), __("Why does it need reopening?"), true)
			);
		}
		if (!done && role.reporter && !works_it) {
			frm.add_custom_button(
				__("Withdraw"),
				() => note_dialog(frm, "Closed", __("Withdraw Ticket"), __("Why is it no longer needed?"), false),
				actions
			);
		}
	}

	// An action works on the saved ticket, so unsaved edits are saved first rather than lost
	// to the reload that follows it.
	function run(frm, method, args) {
		const saved = frm.is_dirty() ? frm.save() : Promise.resolve();
		return saved
			.then(() =>
				frappe.call({
					method: "sf_trading.ticket." + method,
					args: Object.assign({ ticket: frm.doc.name }, args),
					freeze: true,
				})
			)
			.then(() => frm.reload_doc());
	}

	function assign_dialog(frm) {
		const d = new frappe.ui.Dialog({
			title: frm.doc.assignee ? __("Reassign Ticket") : __("Assign Ticket"),
			fields: [
				{
					fieldname: "user",
					fieldtype: "Link",
					options: "User",
					label: __("Assign To"),
					reqd: 1,
					get_query: desk_users,
				},
			],
			primary_action_label: __("Assign"),
			primary_action(values) {
				d.hide();
				run(frm, "assign", { user: values.user });
			},
		});
		d.show();
	}

	function resolve_dialog(frm) {
		const d = new frappe.ui.Dialog({
			title: __("Resolve Ticket"),
			fields: [
				{
					fieldname: "note",
					fieldtype: "Text Editor",
					label: __("How was it resolved?"),
					reqd: 1,
					default: frm.doc.resolution_details || "",
				},
			],
			primary_action_label: __("Mark Resolved"),
			primary_action(values) {
				d.hide();
				run(frm, "set_status", { status: "Resolved", note: values.note });
			},
		});
		d.show();
	}

	function note_dialog(frm, status, title, label, reqd) {
		const d = new frappe.ui.Dialog({
			title: title,
			fields: [{ fieldname: "note", fieldtype: "Small Text", label: label, reqd: reqd ? 1 : 0 }],
			primary_action_label: __("Confirm"),
			primary_action(values) {
				d.hide();
				run(frm, "set_status", { status: status, note: values.note || "" });
			},
		});
		d.show();
	}
})();
