// sf_trading/public/js/landed_cost.js
// Landed cost charge rows name the expense entry that booked them (sf_trading/landed_cost.py).
// Picking the entry fills the row from it: the account it was booked on and what it still has
// left. The server holds the row to that entry whatever the form does.

function sf_lc_setup(frm) {
	frm.set_query("custom_expense_entry", "taxes", function (doc, cdt, cdn) {
		const row = locals[cdt][cdn];
		if (!row.custom_expense_doctype) {
			frappe.throw(__("Choose the Expense Entry Type first."));
		}
		return {
			query: "sf_trading.landed_cost.expense_entry_query",
			filters: { company: frm.doc.company },
		};
	});
}

function sf_lc_fill(frm, cdt, cdn, account_field, amount_field) {
	const row = locals[cdt][cdn];
	if (!row.custom_expense_entry || !row.custom_expense_doctype) return;
	frappe.call({
		method: "sf_trading.landed_cost.get_expense_entry_lines",
		args: {
			expense_doctype: row.custom_expense_doctype,
			expense_entry: row.custom_expense_entry,
			parenttype: frm.doctype,
			parent: frm.is_new() ? null : frm.doc.name,
			// the form's own receipt / invoice ties: a charge copied from the receipt stays its own
			update_stock: frm.doc.update_stock ? 1 : 0,
			return_against: frm.doc.is_return ? frm.doc.return_against || null : null,
			items: (frm.doc.items || []).map((i) => ({
				purchase_receipt: i.purchase_receipt || null,
				purchase_invoice: i.purchase_invoice || null,
				po_detail: i.po_detail || null,
				purchase_order_item: i.purchase_order_item || null,
			})),
		},
		callback(r) {
			const lines = (r && r.message) || [];
			if (!lines.length) {
				frappe.msgprint({
					title: __("Nothing To Absorb"),
					indicator: "orange",
					message: __("{0} booked nothing that a landed cost can absorb.", [row.custom_expense_entry]),
				});
				return;
			}
			const pick = lines.find((l) => l.account === row[account_field]) || (lines.length === 1 ? lines[0] : null);
			if (!pick) {
				frappe.msgprint({
					title: __("Choose the Account"),
					indicator: "blue",
					message:
						__("{0} booked several accounts. Set the charge's account to one of them:", [row.custom_expense_entry]) +
						"<ul>" +
						lines
							.map((l) => `<li>${frappe.utils.escape_html(l.account)}: ${format_currency(l.available)} ${__("left")}</li>`)
							.join("") +
						"</ul>",
				});
				return;
			}
			frappe.model.set_value(cdt, cdn, account_field, pick.account);
			if (!flt(row[amount_field]) || flt(row[amount_field]) > flt(pick.available)) {
				frappe.model.set_value(cdt, cdn, amount_field, Math.max(flt(pick.available), 0));
			}
			frappe.show_alert(
				{
					message: __("{0}: {1} booked, {2} left", [
						row.custom_expense_entry,
						format_currency(pick.booked),
						format_currency(pick.available),
					]),
					indicator: flt(pick.available) > 0 ? "green" : "red",
				},
				6
			);
		},
	});
}

frappe.ui.form.on("Landed Cost Voucher", { setup: sf_lc_setup });

// A draft voucher may name a draft stock invoice (sf_trading/landed_cost_with_invoice.py): the voucher of
// an import purchase is drawn up with the invoice and submitted by its approval. Core's query only offers
// submitted documents, and it is set in the controller's own setup(), which runs after this file's
// handlers -- so the query is replaced on refresh.
function sf_lcv_receipt_query(frm) {
	const grid = frm.fields_dict.purchase_receipts && frm.fields_dict.purchase_receipts.grid;
	if (!grid) return;
	grid.get_field("receipt_document").get_query = function (doc, cdt, cdn) {
		const d = locals[cdt][cdn];
		const filters = [[d.receipt_document_type, "company", "=", frm.doc.company]];
		if (d.receipt_document_type === "Purchase Invoice") {
			filters.push(["Purchase Invoice", "docstatus", "<", 2], ["Purchase Invoice", "update_stock", "=", 1]);
		} else {
			filters.push([d.receipt_document_type, "docstatus", "=", 1]);
		}
		return { filters: filters };
	};
}

function sf_lcv_banner(frm) {
	if (frm.is_new() || frm.doc.docstatus !== 0) return;
	const asked = frm.doc.name;
	frappe.call({
		method: "sf_trading.landed_cost_with_invoice.voucher_status",
		args: { voucher: asked },
		callback(r) {
			const s = r && r.message;
			if (frm.doc.name !== asked || !s || !s.invoice || s.invoice_docstatus !== 0) return;
			const link = `<a href="/app/purchase-invoice/${encodeURIComponent(s.invoice)}">${frappe.utils.escape_html(s.invoice)}</a>`;
			frm.dashboard.clear_headline();
			if (s.stale && s.stale.length) {
				frm.dashboard.set_headline_alert(
					__("This voucher does not match Purchase Invoice {0}: {1}.", [link, frappe.utils.escape_html(s.stale.join("; "))]),
					"orange"
				);
				frm.add_custom_button(__("Refresh from Invoice"), () =>
					frappe.call({
						method: "sf_trading.landed_cost_with_invoice.refresh_from_invoice",
						args: { voucher: frm.doc.name },
						freeze: true,
						callback() {
							frm.reload_doc();
						},
					})
				);
			} else {
				frm.dashboard.set_headline_alert(
					__("Drawn up for draft Purchase Invoice {0}: this voucher is submitted when that invoice is approved.{1}", [
						link,
						s.locked ? " " + __("The invoice is out for approval ({0}): the charges are locked.", [frappe.utils.escape_html(s.state)]) : "",
					]),
					s.locked ? "blue" : "green"
				);
			}
			// nothing to submit by hand: the server refuses it anyway, so do not offer it
			const primary = frm.page.btn_primary;
			if (primary && primary.text().trim() === __("Submit")) frm.page.clear_primary_action();
		},
	});
}

frappe.ui.form.on("Landed Cost Voucher", {
	refresh(frm) {
		sf_lcv_receipt_query(frm);
		sf_lcv_banner(frm);
	},
});
frappe.ui.form.on("Purchase Invoice", { setup: sf_lc_setup });
frappe.ui.form.on("Purchase Receipt", { setup: sf_lc_setup });

frappe.ui.form.on("Landed Cost Taxes and Charges", {
	custom_expense_doctype(frm, cdt, cdn) {
		frappe.model.set_value(cdt, cdn, "custom_expense_entry", null);
	},
	custom_expense_entry(frm, cdt, cdn) {
		sf_lc_fill(frm, cdt, cdn, "expense_account", "amount");
	},
});

frappe.ui.form.on("Purchase Taxes and Charges", {
	custom_expense_doctype(frm, cdt, cdn) {
		frappe.model.set_value(cdt, cdn, "custom_expense_entry", null);
	},
	custom_expense_entry(frm, cdt, cdn) {
		sf_lc_fill(frm, cdt, cdn, "account_head", "tax_amount");
	},
});
