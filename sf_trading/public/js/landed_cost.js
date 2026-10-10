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
