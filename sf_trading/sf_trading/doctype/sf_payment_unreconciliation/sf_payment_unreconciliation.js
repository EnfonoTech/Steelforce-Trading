// sf_trading/sf_trading/doctype/sf_payment_unreconciliation/sf_payment_unreconciliation.js
//
// Payment Reconciliation, run backwards: pick a party, see every allocation its payments carry,
// tick the ones to break. Nothing happens until Unreconcile is pressed and confirmed — undoing
// a reconciliation re-opens an invoice and may cancel an exchange gain/loss journal, so it is
// not something to trigger by mis-clicking a grid.

frappe.ui.form.on("SF Payment Unreconciliation", {
	setup(frm) {
		// the same party types frappe itself considers to have a party account, rather than a
		// hardcoded three
		frm.set_query("party_type", () => ({
			filters: { name: ["in", Object.keys(frappe.boot.party_account_types)] },
		}));
		frm.set_query("receivable_payable_account", () => ({
			filters: {
				company: frm.doc.company,
				is_group: 0,
				account_type: frappe.boot.party_account_types[frm.doc.party_type],
				root_type: frm.doc.party_type === "Customer" ? "Asset" : "Liability",
			},
		}));
		frm.set_query("cost_center", () => ({
			filters: { company: frm.doc.company, is_group: 0 },
		}));
		frm.set_query("branch", () => ({ filters: {} }));
	},

	onload(frm) {
		sf_dimension_queries(frm);
		if (!frm.doc.company) {
			frm.set_value("company", frappe.defaults.get_user_default("Company"));
		}
		if (!frm.doc.party_type) frm.set_value("party_type", "Customer");
		sf_clear(frm);
	},

	refresh(frm) {
		frm.disable_save();
		frm.page.set_title(__("Payment Unreconciliation"));

		// the grid is a result set, not something to type into: a hand-added blank row would be
		// refused server-side anyway, so do not offer it
		const grid = frm.get_field("allocations") && frm.get_field("allocations").grid;
		if (grid) {
			grid.cannot_add_rows = true;
			grid.only_sortable && grid.only_sortable(false);
		}

		// bulk ticking is the whole point when a party has been mis-applied across dozens of
		// invoices — the case this tool exists for
		// straight back to the forward tool, party already chosen
		frm.add_custom_button(__("Reconcile Again"), () => {
			frappe.route_options = {
				company: frm.doc.company, party_type: frm.doc.party_type, party: frm.doc.party,
			};
			frappe.set_route("Form", "Payment Reconciliation");
		}, __("Actions"));
		frm.add_custom_button(__("Tick All"), () => sf_tick(frm, 1));
		frm.add_custom_button(__("Untick All"), () => sf_tick(frm, 0));
		frm.add_custom_button(__("Get Allocations"), () => sf_fetch(frm));

		// The primary action is never left empty: clearing it lets frappe put Save back, and Save
		// means nothing here -- this is a tool, not a document.
		const ticked = (frm.doc.allocations || []).filter((r) => r.select_row);
		if (ticked.length) {
			frm.page.set_primary_action(__("Unreconcile ({0})", [ticked.length]),
				() => sf_confirm(frm, ticked));
		} else {
			frm.page.set_primary_action(__("Get Allocations"), () => sf_fetch(frm));
		}
		sf_summary(frm);
	},

	company(frm) {
		frm.set_value("party", null);
		frm.set_value("receivable_payable_account", null);
		sf_clear(frm);
		sf_dimension_queries(frm);
	},

	party_type(frm) {
		frm.set_value("party", null);
		frm.set_value("receivable_payable_account", null);
		sf_clear(frm);
	},

	party(frm) {
		sf_clear(frm);
		// the party account is the one filter nobody should have to look up: ask erpnext for the
		// same answer Payment Reconciliation gets, including the advance account variant
		frm.set_value("receivable_payable_account", null);
		if (!(frm.doc.party_type && frm.doc.party)) return;
		frappe.call({
			method: "erpnext.accounts.party.get_party_account",
			args: {
				company: frm.doc.company,
				party_type: frm.doc.party_type,
				party: frm.doc.party,
				include_advance: 1,
			},
			callback: (r) => {
				if (r.exc || !r.message) return;
				const account = Array.isArray(r.message) ? r.message[0] : r.message;
				if (account) frm.set_value("receivable_payable_account", account);
			},
		});
	},

	receivable_payable_account: (frm) => sf_clear(frm),
});

frappe.ui.form.on("SF Unreconciliation Row", {
	select_row: (frm) => frm.trigger("refresh"),
	allocations_add: (frm) => sf_summary(frm),
});

function sf_dimension_queries(frm) {
	// erpnext's own helper: it knows which dimensions exist on this site and how each should be
	// filtered (company-scoped, non-group for trees), so a site that adds one gets it for free
	frappe.call({
		method: "erpnext.accounts.doctype.payment_reconciliation.payment_reconciliation.get_queries_for_dimension_filters",
		args: { company: frm.doc.company },
		callback: (r) => {
			(r.message || []).forEach((dim) => {
				if (!frm.fields_dict[dim.fieldname]) return;
				frm.set_query(dim.fieldname, () => ({ filters: dim.filters }));
			});
		},
	});
}

function sf_tick(frm, value) {
	(frm.doc.allocations || []).forEach((row) => {
		// never tick a row the server is going to refuse
		row.select_row = value && !row.in_closed_period ? 1 : 0;
	});
	frm.refresh_field("allocations");
	frm.trigger("refresh");
}

function sf_clear(frm) {
	// assigned rather than set_value: set_value marks the form dirty, and a dirty form puts the
	// Save button back over the action the user actually needs
	frm.doc.allocations = [];
	frm.refresh_field("allocations");
	sf_summary(frm);
}

function sf_fetch(frm) {
	if (!(frm.doc.company && frm.doc.party_type && frm.doc.party)) {
		frappe.msgprint({
			title: __("Pick a Party"),
			message: __("Company, Party Type and Party are all needed."),
			indicator: "orange",
		});
		return;
	}
	frm.call({
		doc: frm.doc,
		method: "get_allocations",
		freeze: true,
		freeze_message: __("Reading the ledger…"),
		callback: (r) => {
			frm.refresh_field("allocations");
			frm.trigger("refresh");
			if (!r.message) {
				frappe.msgprint({
					title: __("Nothing Allocated"),
					message: __("No live allocation matches these filters for {0}.", [frm.doc.party]),
					indicator: "blue",
				});
			}
		},
	});
}

function sf_summary(frm) {
	const rows = frm.doc.allocations || [];
	const ticked = rows.filter((r) => r.select_row);
	const closed = rows.filter((r) => r.in_closed_period).length;
	const total = ticked.reduce((sum, r) => sum + flt(r.allocated_amount), 0);
	const currency = (rows[0] && rows[0].currency) || frappe.boot.sysdefaults.currency;

	let html = `<div class="text-muted">${__("{0} allocation(s) found", [rows.length])}`;
	if (ticked.length) {
		html += ` · <b>${__("{0} ticked", [ticked.length])}</b> · ${format_currency(total, currency)}`;
	}
	if (closed) {
		html += ` · <span style="color:var(--red-500)">${
			__("{0} inside a closed period and cannot be undone", [closed])}</span>`;
	}
	html += "</div>";
	frm.dashboard.clear_headline();
	frm.dashboard.set_headline(html);
}

function sf_confirm(frm, ticked) {
	const currency = ticked[0].currency || frappe.boot.sysdefaults.currency;
	const total = ticked.reduce((sum, r) => sum + flt(r.allocated_amount), 0);
	const blocked = ticked.filter((r) => r.in_closed_period);
	const lines = ticked.slice(0, 12).map((r) =>
		`<tr><td>${frappe.utils.escape_html(r.voucher_no)}</td>
		 <td>${frappe.utils.escape_html(r.against_voucher_no)}</td>
		 <td class="text-right">${format_currency(r.allocated_amount, r.currency)}</td></tr>`).join("");

	frappe.confirm(
		`<p>${__("Break {0} allocation(s) worth {1}?", [ticked.length, format_currency(total, currency)])}</p>
		 <table class="table table-bordered small"><thead><tr>
			<th>${__("Payment")}</th><th>${__("Applied To")}</th><th class="text-right">${__("Allocated")}</th>
		 </tr></thead><tbody>${lines}</tbody></table>
		 ${ticked.length > 12 ? `<p class="text-muted small">${__("…and {0} more", [ticked.length - 12])}</p>` : ""}
		 ${blocked.length ? `<p style="color:var(--red-500)">${
			__("{0} of these sit in a closed period and will be refused.", [blocked.length])}</p>` : ""}
		 <p class="text-muted small">${__("Each invoice's outstanding is recalculated and an Unreconcile Payment record is left behind. The payment itself keeps its amount and stays submitted.")}</p>`,
		() => sf_run(frm)
	);
}

function sf_run(frm) {
	frm.call({
		doc: frm.doc,
		method: "unreconcile_selected",
		freeze: true,
		freeze_message: __("Unreconciling…"),
		callback: (r) => {
			const res = r.message || {};
			// the server hands back what is still allocated; replacing the grid in place keeps
			// the filters the user typed, which a reload would throw away
			frm.doc.allocations = (res.rows || []).map((row, i) =>
				Object.assign({ doctype: "SF Unreconciliation Row", parent: frm.doc.name,
					parentfield: "allocations", parenttype: frm.doc.doctype,
					idx: i + 1, name: `new-row-${i + 1}` }, row));
			frm.refresh_field("allocations");
			frm.trigger("refresh");

			if ((res.done || []).length) {
				frappe.show_alert({
					message: __("{0} allocation(s) undone, {1}", [
						res.done.length,
						format_currency(res.total, (frm.doc.allocations[0] || {}).currency
							|| frappe.boot.sysdefaults.currency)]),
					indicator: "green",
				}, 7);
				// name the audit records, so the trail is one click away rather than a claim
				const audits = res.done.filter((d) => d.audit);
				if (audits.length) {
					frappe.msgprint({
						title: __("Undone"),
						message: audits.map((d) =>
							`<b>${frappe.utils.escape_html(d.voucher_no)}</b> → ${
								frappe.utils.escape_html(d.against_voucher_no)} · ${
								frappe.utils.escape_html(String(d.allocated_amount))} · <a href="/app/unreconcile-payment/${
								encodeURIComponent(d.audit)}">${__("audit record")}</a>`).join("<br>"),
						indicator: "green",
					});
				}
			}
			if ((res.failed || []).length) {
				// every refusal names its reason: a closed period, a cancelled payment, or an
				// allocation somebody else already broke
				frappe.msgprint({
					title: __("Some Were Not Undone"),
					message: res.failed.map((f) =>
						`<b>${frappe.utils.escape_html(f.voucher_no || "")}</b> → ${
							frappe.utils.escape_html(f.against_voucher_no || "")}: ${
							frappe.utils.escape_html(f.error)}`).join("<br>"),
					indicator: "red",
				});
			}
		},
	});
}
