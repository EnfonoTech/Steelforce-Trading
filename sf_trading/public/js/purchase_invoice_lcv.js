// sf_trading/public/js/purchase_invoice_lcv.js
// An import purchase carries its Landed Cost Voucher (sf_trading/landed_cost_with_invoice.py).
// Create > Landed Cost Voucher opens a voucher naming this draft invoice; the headline says whether one
// is still needed, whether the ones drawn up still match, and that they are submitted by the approval.
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

frappe.ui.form.on("Purchase Invoice", {
	refresh(frm) {
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
});
