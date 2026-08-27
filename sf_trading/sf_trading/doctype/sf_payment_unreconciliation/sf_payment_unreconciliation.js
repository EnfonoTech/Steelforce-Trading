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

		// The primary action is never left empty: clearing it lets frappe put Save back, and Save
		// means nothing here -- this is a tool, not a document.
		const ticked = (frm.doc.allocations || []).filter((r) => r.select_row);
		if (ticked.length) {
			frm.page.set_primary_action(__("Unreconcile ({0})", [ticked.length]),
				() => sf_confirm(frm, ticked));
			frm.add_custom_button(__("Get Allocations"), () => sf_fetch(frm));
		} else {
			frm.page.set_primary_action(__("Get Allocations"), () => sf_fetch(frm));
		}
		sf_summary(frm);
		sf_render_insight(frm);
		sf_tint(frm);
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
	payment_no: (frm) => sf_clear(frm),
	reconciled_within: (frm) => sf_clear(frm),

	// pasting a payment number is the other way people arrive here -- "this receipt should not
	// have touched that invoice" -- so offer the same completion the invoice field has
	setup_payment_query(frm) {
		frm.set_query("payment_no", () => ({ filters: { company: frm.doc.company, docstatus: 1 } }));
	},
});

frappe.ui.form.on("SF Unreconciliation Row", {
	select_row(frm, cdt, cdn) {
		const row = frappe.get_doc(cdt, cdn);
		if (row.select_row && !sf_can_undo(row)) {
			row.select_row = 0;
			frm.refresh_field("allocations");
			frappe.show_alert({
				message: row.in_closed_period
					? __("{0} sits in a closed accounting period.", [row.voucher_no])
					: __("{0} is a credit note netted onto its own invoice. ERPNext can only "
					     + "unreconcile a Payment Entry or a Journal Entry — cancel or amend the "
					     + "credit note instead.", [row.voucher_no]),
				indicator: "orange",
			}, 7);
		}
		frm.trigger("refresh");
	},
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

function sf_can_undo(row) {
	// a credit note netted onto its own invoice is listed for completeness but ERPNext cannot
	// unreconcile it, and a closed period will refuse it
	return !row.in_closed_period && cint(row.undoable !== undefined ? row.undoable : 1);
}

function sf_tick(frm, value) {
	(frm.doc.allocations || []).forEach((row) => {
		// never tick a row the server is going to refuse
		row.select_row = value && sf_can_undo(row) ? 1 : 0;
	});
	frm.refresh_field("allocations");
	frm.trigger("refresh");
}

function sf_clear(frm) {
	// assigned rather than set_value: set_value marks the form dirty, and a dirty form puts the
	// Save button back over the action the user actually needs
	frm.doc.allocations = [];
	frm.__all_rows = [];
	frm.__view = "all";
	frm.__voucher = null;
	frm.refresh_field("allocations");
	sf_summary(frm);
	sf_render_insight(frm);
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
			sf_capture(frm);
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
	const rows = (frm.__all_rows && frm.__all_rows.length) ? frm.__all_rows : (frm.doc.allocations || []);
	const shown = frm.doc.allocations || [];
	const ticked = rows.filter((r) => r.select_row);
	const closed = rows.filter((r) => r.in_closed_period).length;
	const total = ticked.reduce((sum, r) => sum + flt(r.allocated_amount), 0);
	const currency = (rows[0] && rows[0].currency) || frappe.boot.sysdefaults.currency;

	let html = `<div class="text-muted">${__("{0} allocation(s) found", [rows.length])}`;
	if (shown.length !== rows.length) {
		html += ` · ${__("{0} shown by the current filter", [shown.length])}`;
	}
	if (ticked.length) {
		html += ` · <b>${__("{0} ticked", [ticked.length])}</b> · ${format_currency(total, currency)}`;
	}
	const advances = rows.filter((r) => r.entry_type === "Order Advance").length;
	if (advances) {
		html += ` · ${__("{0} order advance(s)", [advances])}`;
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
	const blocked = ticked.filter((r) => !sf_can_undo(r));
	const advances = ticked.filter((r) => r.entry_type === "Order Advance");
	const lines = ticked.slice(0, 12).map((r) =>
		`<tr><td>${frappe.utils.escape_html(r.voucher_no)}</td>
		 <td>${frappe.utils.escape_html(r.against_voucher_no)}</td>
		 <td>${frappe.utils.escape_html(r.entry_type || "")}</td>
		 <td class="text-right">${format_currency(r.allocated_amount, r.currency)}</td></tr>`).join("");

	frappe.confirm(
		`<p>${__("Break {0} allocation(s) worth {1}?", [ticked.length, format_currency(total, currency)])}</p>
		 <table class="table table-bordered small"><thead><tr>
			<th>${__("Payment")}</th><th>${__("Applied To")}</th><th>${__("Type")}</th>
			<th class="text-right">${__("Allocated")}</th>
		 </tr></thead><tbody>${lines}</tbody></table>
		 ${ticked.length > 12 ? `<p class="text-muted small">${__("…and {0} more", [ticked.length - 12])}</p>` : ""}
		 ${blocked.length ? `<p style="color:var(--red-500)">${
			__("{0} of these cannot be undone here and will be refused.", [blocked.length])}</p>` : ""}
		 ${advances.length ? `<p class="text-muted small">${
			__("{0} of these are order advances: the order's Advance Paid drops by that amount and the payment is free to be applied elsewhere.", [advances.length])}</p>` : ""}
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
			sf_capture(frm);
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


// =================================================================================
// The insight layer
//
// A party with real history returns dozens of rows, and most of them are one bulk journal
// wearing a different invoice number on each line. Left as a flat list there is nothing to
// tell a clerk which row somebody got wrong. So: the server marks each row with what it
// observed, and everything below turns those marks into somewhere to look first --
// a rollup per payment, chips that narrow the grid, and a tint on the rows worth a second
// glance. Nothing here changes what can be undone; it only changes what is easy to find.
// =================================================================================

const SF_NEW_DAYS = 7;

function sf_date_part(value) {
	if (!value) return "";
	return typeof value === "string" ? value.slice(0, 10)
		: (frappe.datetime.obj_to_str(value) || "").slice(0, 10);
}

function sf_is_new(row) {
	const day = sf_date_part(row.allocated_on);
	if (!day) return false;
	return frappe.datetime.get_day_diff(frappe.datetime.now_date(), day) <= SF_NEW_DAYS;
}

const SF_VIEWS = [
	{ key: "all", label: __("All"), test: () => true },
	{ key: "risk", label: __("Worth a look"), test: (r) => r.severity === "risk" },
	{ key: "lead", label: __("Has an open credit note"), test: (r) => r.severity === "lead" },
	{ key: "new", label: __("Allocated recently"), test: (r) => sf_is_new(r) },
	{ key: "human", label: __("Entered by a person"), test: (r) => !cint(r.imported) },
	{ key: "single", label: __("One-off payments"), test: (r) => cint(r.leg_count) <= 1 },
	{ key: "bulk", label: __("Bulk vouchers"), test: (r) => cint(r.leg_count) >= 5 },
	{ key: "import", label: __("From the data import"), test: (r) => !!cint(r.imported) },
];

function sf_alerts(frm) {
	// what is true about this party before a single row is read: two times out of three the reason
	// somebody opened this screen is a credit note nobody applied
	if (!(frm.doc.company && frm.doc.party_type && frm.doc.party)) {
		frm.__alerts = [];
		return;
	}
	frappe.call({
		method: "sf_trading.payment_unreconciliation.party_alerts",
		args: { company: frm.doc.company, party_type: frm.doc.party_type, party: frm.doc.party },
		callback: (r) => {
			frm.__alerts = r.message || [];
			sf_render_insight(frm);
		},
	});
}

function sf_capture(frm) {
	// hold the whole result set: the chips below show subsets of it, and re-reading the ledger
	// for every chip click would be both slow and a different answer each time
	frm.__all_rows = (frm.doc.allocations || []).slice();
	if (!frm.__view) frm.__view = "all";
	frm.__voucher = null;
	sf_apply_view(frm);
	sf_alerts(frm);
}

function sf_apply_view(frm) {
	const all = frm.__all_rows || [];
	const view = SF_VIEWS.find((v) => v.key === (frm.__view || "all")) || SF_VIEWS[0];
	// the row objects are the same ones the grid ticked, so a tick survives a chip click
	const shown = all.filter((r) => view.test(r) && (!frm.__voucher || r.voucher_no === frm.__voucher));
	shown.forEach((r, i) => { r.idx = i + 1; });
	frm.doc.allocations = shown;
	frm.refresh_field("allocations");
	sf_tint(frm);
}

function sf_tint(frm) {
	const grid = frm.get_field("allocations") && frm.get_field("allocations").grid;
	if (!grid || !grid.grid_rows) return;
	grid.grid_rows.forEach((gr) => {
		if (!gr.doc || !gr.wrapper) return;
		const risk = gr.doc.severity === "risk";
		const lead = gr.doc.severity === "lead";
		const locked = !sf_can_undo(gr.doc);
		const fresh = !risk && !lead && sf_is_new(gr.doc);
		gr.wrapper.css({
			"background-color": risk ? "rgba(255, 170, 0, 0.10)"
				: lead ? "rgba(120, 80, 200, 0.07)"
				: fresh ? "rgba(0, 120, 255, 0.06)" : "",
			"border-left": risk ? "3px solid rgba(230, 130, 0, 0.9)"
				: lead ? "3px solid rgba(120, 80, 200, 0.7)"
				: fresh ? "3px solid rgba(0, 120, 255, 0.6)" : "",
			opacity: locked ? 0.72 : "",
		});
	});
}

function sf_render_insight(frm) {
	const field = frm.get_field("insight_html");
	if (!field || !field.$wrapper) return;
	const all = frm.__all_rows || [];
	if (!all.length) { field.$wrapper.empty(); return; }

	const currency = all[0].currency || frappe.boot.sysdefaults.currency;
	const esc = frappe.utils.escape_html;

	// --- chips: how many rows each way of looking at this party would show ---------
	const chips = SF_VIEWS.map((v) => {
		const n = all.filter(v.test).length;
		if (!n && v.key !== "all") return "";
		const active = (frm.__view || "all") === v.key;
		return `<button class="btn btn-xs sf-chip ${active ? "btn-primary" : "btn-default"}"
			data-view="${v.key}" style="margin-right:4px">${esc(v.label)} (${n})</button>`;
	}).join("");

	// --- one line per payment: eleven legs of a journal are ONE accounting event ----
	const byVoucher = {};
	all.forEach((r) => {
		const v = byVoucher[r.voucher_no] || (byVoucher[r.voucher_no] = {
			voucher_no: r.voucher_no, voucher_type: r.voucher_type, posting_date: r.posting_date,
			allocated_by: r.allocated_by, allocated_on: r.allocated_on, leg_count: cint(r.leg_count),
			shown: 0, total: 0, risk: 0, currency: r.currency,
		});
		v.shown += 1;
		v.total += flt(r.allocated_amount);
		if (r.severity === "risk") v.risk += 1;
	});
	const vouchers = Object.values(byVoucher).sort((a, b) => (b.risk - a.risk) || (b.total - a.total));

	const rows = vouchers.map((v) => `
		<tr data-voucher="${esc(v.voucher_no)}" class="sf-voucher" style="cursor:pointer">
			<td>${esc(v.voucher_no)}${v.risk ? ` <span style="color:rgb(200,110,0)">&#9888;</span>` : ""}</td>
			<td>${frappe.datetime.str_to_user(v.posting_date) || ""}</td>
			<td class="text-right">${v.shown}${v.leg_count > v.shown ? ` / ${v.leg_count}` : ""}</td>
			<td class="text-right">${format_currency(v.total, v.currency)}</td>
			<td>${esc(v.allocated_by || "")}</td>
			<td>${sf_date_part(v.allocated_on)
				? frappe.datetime.str_to_user(sf_date_part(v.allocated_on)) : ""}</td>
		</tr>`).join("");

	const flagged = all.filter((r) => r.severity === "risk").length;
	const counts = {};
	all.forEach((r) => (r.insight || "").split(" · ").filter(Boolean)
		.forEach((n) => { counts[n] = (counts[n] || 0) + 1; }));
	const legend = Object.keys(counts).sort((a, b) => counts[b] - counts[a])
		.map((n) => `${esc(n)} (${counts[n]})`).join(" · ");

	const alerts = (frm.__alerts || []).map((a) => `
		<div style="padding:6px 10px;margin-bottom:6px;border-left:3px solid ${
			a.kind === "credit_note" ? "rgb(230,130,0)" : "rgb(120,120,120)"};
			background:${a.kind === "credit_note" ? "rgba(255,170,0,0.10)" : "rgba(120,120,120,0.08)"}">
			<b>${a.kind === "credit_note" ? __("Unapplied credit note") : __("Cannot be undone here")}</b>
			— ${esc(a.message)}</div>`).join("");

	field.$wrapper.html(`
		<div class="sf-insight" style="margin-bottom:12px">
			${alerts}
			<div style="margin-bottom:8px">${chips}
				${frm.__voucher ? `<button class="btn btn-xs btn-default sf-chip" data-view="${
					frm.__view || "all"}" style="margin-left:8px">&times; ${
					__("Payment filter: {0}", [esc(frm.__voucher)])}</button>` : ""}
			</div>
			<div class="text-muted small" style="margin-bottom:8px">
				${__("{0} allocation(s) across {1} payment(s)", [all.length, vouchers.length])}${
					flagged ? ` · <b style="color:rgb(200,110,0)">${
						__("{0} worth a look", [flagged])}</b>` : ""}${legend ? ` · ${legend}` : ""}
			</div>
			<table class="table table-bordered table-condensed small" style="margin-bottom:0">
				<thead><tr>
					<th>${__("Payment")}</th><th>${__("Date")}</th>
					<th class="text-right">${__("Legs")}</th>
					<th class="text-right">${__("Allocated")}</th>
					<th>${__("Allocated By")}</th><th>${__("Allocated On")}</th>
				</tr></thead>
				<tbody>${rows}</tbody>
			</table>
			<div class="text-muted small" style="margin-top:6px">${__(
				"Click a payment to see only its legs. A tinted row is one the ledger flagged; the Insight column says why.")}</div>
		</div>`);

	field.$wrapper.find(".sf-chip").on("click", (e) => {
		frm.__view = $(e.currentTarget).attr("data-view") || "all";
		if ($(e.currentTarget).text().indexOf("\u00d7") === 0) frm.__voucher = null;
		sf_apply_view(frm);
		sf_render_insight(frm);
		sf_summary(frm);
	});
	field.$wrapper.find(".sf-voucher").on("click", (e) => {
		const picked = $(e.currentTarget).attr("data-voucher");
		frm.__voucher = frm.__voucher === picked ? null : picked;
		sf_apply_view(frm);
		sf_render_insight(frm);
		sf_summary(frm);
	});
}
