// sf_trading/public/js/purchase_invoice_lcv.js
// An import purchase carries its Landed Cost Voucher (sf_trading/landed_cost_with_invoice.py).
// Create > Landed Cost Voucher opens a voucher naming this draft invoice; the headline says whether one
// is still needed, whether the ones drawn up still match, and that they are submitted by the approval.
// The Bayan number is asked of a foreign-currency invoice, except a small import (total below the amount set
// for the company on SF Trading Settings > Small Imports); that rule lives here and replaces the site's old
// "Bayan Field  Validation" Client Script (patches/v0_1/bayan_follows_small_import.py switches that one off).
// UI only: the server refuses whatever the form lets through.

function sf_lcv_headline(frm, status) {
	frm.dashboard.clear_headline();
	const link = (name) => `<a href="/app/landed-cost-voucher/${encodeURIComponent(name)}">${frappe.utils.escape_html(name)}</a>`;
	const live = (status.vouchers || []).filter((v) => v.docstatus < 2);
	const stale = Object.keys(status.stale || {});
	if (frm.doc.docstatus === 0) {
		if (stale.length) {
			frm.dashboard.set_headline_alert(
				__("Landed Cost Voucher {0} does not match this invoice: {1}. Save the invoice to bring it in line, or use Refresh from Invoice on the voucher.", [
					stale.map(link).join(", "),
					frappe.utils.escape_html(stale.map((n) => status.stale[n].join("; ")).join(" | ")),
				]),
				"orange"
			);
		} else if (live.length) {
			const total = live.reduce((sum, v) => sum + flt(v.total), 0);
			frm.dashboard.set_headline_alert(
				__("Landed Cost Voucher {0} ({1} of charges) is submitted with this invoice when it is approved.", [
					live.map((v) => link(v.name)).join(", "),
					format_currency(total, erpnext.get_currency(frm.doc.company)),
				]),
				"green"
			);
		} else if (status.required) {
			frm.dashboard.set_headline_alert(
				__("This import purchase needs a Landed Cost Voucher before it goes for approval: Create > Landed Cost Voucher. If no landed cost applies, tick Import Without Landed Cost."),
				"orange"
			);
		}
		if (status.small_import) {
			frm.dashboard.add_comment(
				__("A small import (below {0}): no Bayan number is needed. The Landed Cost Voucher still is.", [
					format_currency(status.small_limit, erpnext.get_currency(frm.doc.company)),
				]),
				"blue",
				true
			);
		}
		if (status.locked && live.length) {
			frm.dashboard.add_comment(__("Out for approval: the voucher's charges are locked. Reject the invoice to change them."), "blue", true);
		}
	} else if (frm.doc.docstatus === 1 && status.is_import && !live.length) {
		frm.dashboard.set_headline_alert(
			__("This import purchase has no Landed Cost Voucher: its stock is valued without freight and customs."),
			"red"
		);
	}
}

// The Bayan number: shown on a foreign-currency invoice, required unless it is a small import. What decides it
// (the company's currency and its small-import amount) is read once per company; the form is re-judged as the
// currency, the company or the total changes, and once more as it is saved so the mandatory check sees the
// total as it stands.
function sf_bayan_apply(frm) {
	const rule = (frm.sf_bayan_rules || {})[frm.doc.company];
	if (!rule || !frm.doc.currency) return;
	const foreign = frm.doc.currency !== rule.company_currency;
	const small = flt(rule.small_limit) > 0 && flt(frm.doc.base_grand_total) < flt(rule.small_limit);
	frm.toggle_display("custom_bayan_value", foreign);
	frm.toggle_reqd("custom_bayan_value", foreign && !small);
}

function sf_bayan_refresh(frm) {
	const company = frm.doc.company;
	if (!company || !frm.doc.currency) return;
	frm.sf_bayan_rules = frm.sf_bayan_rules || {};
	if (frm.sf_bayan_rules[company]) return sf_bayan_apply(frm);
	frappe.call({
		method: "sf_trading.landed_cost_with_invoice.bayan_rule",
		args: { company: company },
		callback(r) {
			if (!r.message) return;
			frm.sf_bayan_rules[company] = r.message;
			sf_bayan_apply(frm);
		},
	});
}

frappe.ui.form.on("Purchase Invoice", {
	refresh(frm) {
		sf_bayan_refresh(frm);
		// the invoice's vouchers are cancelled with it by the server: no "Cancel All" dialog
		frm.ignore_doctypes_on_cancel_all = ["Landed Cost Voucher"];
		if (frm.is_new() || frm.doc.docstatus === 2) return;
		if (frm.doc.docstatus === 0 && frm.doc.update_stock && !frm.doc.is_return) {
			frm.add_custom_button(
				__("Landed Cost Voucher"),
				() =>
					frappe.model.open_mapped_doc({
						method: "sf_trading.landed_cost_with_invoice.make_landed_cost_voucher",
						frm: frm,
					}),
				__("Create")
			);
		}
		// a response is applied only while it still describes the invoice on screen
		const asked = frm.doc.name;
		frappe.call({
			method: "sf_trading.landed_cost_with_invoice.invoice_status",
			args: { invoice: asked },
			callback(r) {
				if (frm.doc.name !== asked || !r.message) return;
				sf_lcv_headline(frm, r.message);
			},
		});
	},
	update_stock(frm) {
		if (!frm.doc.update_stock) frm.set_value("custom_import_without_lcv", 0);
	},
	currency: sf_bayan_refresh,
	company: sf_bayan_refresh,
	base_grand_total: sf_bayan_apply,
	grand_total: sf_bayan_apply,
	validate: sf_bayan_apply,
});
