// Copyright (c) 2026, Enfono Technologies and contributors
// For license information, please see license.txt

// Accounting Clearance: pick who is being cleared and the pending transactions load at once.
// The server refetches them on every save as well (accounting_clearance.py), so the saved record is
// never older than its last save.

const SF_CLEARANCE_PARTY_DOCTYPE = {
	Employee: "Employee",
	"Sales Person": "Sales Person",
	"Delivery Person": "Driver",
	User: "User",
	Branch: "Branch",
};

frappe.ui.form.on("Accounting Clearance", {
	setup(frm) {
		if (!frm.doc.company) frm.set_value("company", frappe.defaults.get_user_default("Company"));
		// User is the usual pick. Not a field default: frappe turns a default of "User" into the logged-in user
		if (frm.is_new() && !frm.doc.clearance_for) frm.doc.clearance_for = "User";
	},

	onload(frm) {
		// the party field is a Dynamic Link: without party_type its dropdown has nothing to list
		sf_clearance_sync_party_type(frm);
	},

	refresh(frm) {
		sf_clearance_sync_party_type(frm);
		sf_clearance_render_summary(frm);
		frm.set_df_property("party", "label", frm.doc.clearance_for || __("Name"));
		// refresh runs more than once per load: clear the message before setting it, or it stacks up
		frm.set_intro();
		sf_clearance_set_indicator(frm);
		if (frm.doc.docstatus === 1) {
			frm.set_intro(
				__("Approved on {0} by {1}: nothing was pending.", [frappe.datetime.str_to_user(frm.doc.approved_on), frm.doc.approved_by]),
				"green"
			);
			return;
		}
		if (frm.doc.docstatus === 0 && !frm.is_new()) {
			frm.set_intro(
				frm.doc.pending_count
					? __("Approve (Submit) once every pending transaction below is settled.")
					: __("Nothing is pending. Submit to approve the clearance."),
				frm.doc.pending_count ? "orange" : "green"
			);
		}
		if (frm.doc.party && frm.doc.docstatus === 0) {
			frm.add_custom_button(__("Refresh Pending"), () => sf_clearance_fetch(frm));
		}
		// the server refetched the list on opening and it differs from the saved one: let Save keep it
		if (frm.doc.__onload && frm.doc.__onload.pending_changed && !frm.is_dirty()) {
			frm.dirty();
			frm.set_intro(__("The pending list has changed since it was last saved. Save to keep the updated list."), "orange");
		}
	},

	clearance_for(frm) {
		frm.set_value("party_type", SF_CLEARANCE_PARTY_DOCTYPE[frm.doc.clearance_for] || "");
		frm.set_value("party", "");
		frm.set_df_property("party", "label", frm.doc.clearance_for || __("Name"));
		sf_clearance_fill(frm, []);
	},

	party(frm) {
		sf_clearance_fetch(frm);
	},

	company(frm) {
		sf_clearance_fetch(frm);
	},

	as_on_date(frm) {
		sf_clearance_fetch(frm);
	},
});

function sf_clearance_sync_party_type(frm) {
	const doctype = SF_CLEARANCE_PARTY_DOCTYPE[frm.doc.clearance_for] || "";
	// set on the doc directly: it is derived, and a set_value here would mark a saved record unsaved
	if (frm.doc.party_type !== doctype) frm.doc.party_type = doctype;
}

// the form header shows the clearance's own status (Pending / Cleared / Approved), not frappe's "Draft"
function sf_clearance_set_indicator(frm) {
	if (frm.is_new() || frm.is_dirty()) return;
	const colour = { Pending: "orange", Cleared: "blue", Approved: "green", Cancelled: "red" }[frm.doc.status];
	if (colour) frm.page.set_indicator(__(frm.doc.status), colour);
}

function sf_clearance_fetch(frm) {
	if (!frm.doc.clearance_for || !frm.doc.party || !frm.doc.company) {
		sf_clearance_fill(frm, []);
		return;
	}
	frappe.call({
		method: "sf_trading.sf_trading.doctype.accounting_clearance.accounting_clearance.get_pending_transactions",
		args: {
			clearance_for: frm.doc.clearance_for,
			party: frm.doc.party,
			company: frm.doc.company,
			as_on_date: frm.doc.as_on_date,
		},
		freeze: true,
		freeze_message: __("Fetching pending transactions..."),
		callback(r) {
			sf_clearance_fill(frm, r.message || []);
			if (!(r.message || []).length) {
				frappe.show_alert({ message: __("Nothing pending."), indicator: "green" });
			}
		},
	});
}

function sf_clearance_fill(frm, rows) {
	frm.clear_table("items");
	let total = 0;
	rows.forEach((row) => {
		frm.add_child("items", row);
		// drafts, approvals and unbilled orders are listed but are not money owed (accounting_clearance.py)
		if (row.in_total) total += flt(row.outstanding);
	});
	frm.set_value("pending_count", rows.length);
	frm.set_value("total_outstanding", total);
	frm.refresh_field("items");
	sf_clearance_render_summary(frm);
}

function sf_clearance_render_summary(frm) {
	const wrapper = frm.get_field("summary_html").$wrapper;
	const groups = {};
	(frm.doc.items || []).forEach((row) => {
		const g = (groups[row.category] = groups[row.category] || { count: 0, amount: 0, in_total: 0 });
		g.count += 1;
		g.amount += flt(row.outstanding);
		if (row.in_total) g.in_total = 1;
	});
	const names = Object.keys(groups);
	if (!names.length) {
		wrapper.html(
			frm.doc.party
				? `<div class="text-success" style="padding-top: 6px">${__("Nothing pending.")}</div>`
				: ""
		);
		return;
	}
	const esc = frappe.utils.escape_html;
	const rows = names
		.map(
			(name) => `<tr>
				<td>${esc(name)}</td>
				<td class="text-right">${groups[name].count}</td>
				<td class="text-right">${
					groups[name].in_total
						? format_currency(groups[name].amount)
						: groups[name].amount
						? `<span class="text-muted">${format_currency(groups[name].amount)}</span><div class="text-muted small">${__("not in total")}</div>`
						: `<span class="text-muted">${__("not in total")}</span>`
				}</td>
			</tr>`
		)
		.join("");
	wrapper.html(`
		<table class="table table-sm table-bordered" style="margin: 0">
			<thead><tr><th>${__("Category")}</th><th class="text-right">${__("Count")}</th>
			<th class="text-right">${__("Pending")}</th></tr></thead>
			<tbody>${rows}</tbody>
		</table>`);
}
