// Copyright (c) 2026, Enfono Technologies and contributors
// For license information, please see license.txt

// "Document Trail" dashboard section: the WHOLE Purchase (Order -> Receipt -> Invoice) or Sales
// (Order -> Delivery Note -> Invoice) chain, from any one document in it. Core's own Connections
// tab only shows what is one hop from the document you are looking at (see
// sf_trading/document_trail.py for why that leaves a Purchase Order or a mid-chain Purchase
// Receipt unable to show its own invoice) -- this calls the server-side walk instead and paints
// every doctype in the chain as its own group, saved drafts only, same as Period Closing
// Voucher's Open Items section: an unsaved form has no chain to walk yet.

frappe.ui.form.on("Purchase Order", { refresh: render_document_trail });
frappe.ui.form.on("Purchase Receipt", { refresh: render_document_trail });
frappe.ui.form.on("Purchase Invoice", { refresh: render_document_trail });
frappe.ui.form.on("Sales Order", { refresh: render_document_trail });
frappe.ui.form.on("Delivery Note", { refresh: render_document_trail });
frappe.ui.form.on("Sales Invoice", { refresh: render_document_trail });

function render_document_trail(frm) {
	if (frm.is_new()) return;

	// stale-response guard: only the latest request may draw
	const seq = (frm._sf_doc_trail_seq = (frm._sf_doc_trail_seq || 0) + 1);

	frappe.call({
		method: "sf_trading.document_trail.get_document_trail",
		args: { doctype: frm.doc.doctype, docname: frm.doc.name },
		callback(r) {
			if (!r.message || seq !== frm._sf_doc_trail_seq) return;

			// replace, never stack -- an earlier section may survive a partial refresh
			if (frm._sf_doc_trail_section) {
				frm._sf_doc_trail_section.remove();
				frm._sf_doc_trail_section = null;
			}

			const section = frm.dashboard.add_section(
				build_trail_html(frm, r.message),
				__("Document Trail")
			);
			frm._sf_doc_trail_section = section;
			frm.dashboard.show();
		},
	});
}

function build_trail_html(frm, message) {
	const chain = message.chain || [];
	const documents = message.documents || {};
	const currency = frappe.get_doc(":Company", frm.doc.company)?.default_currency;

	const groups = chain
		.map((doctype) => {
			const rows = documents[doctype] || [];
			if (!rows.length) return "";

			const items = rows
				.map((row) => {
					const is_this_one = doctype === frm.doc.doctype && row.name === frm.doc.name;
					const link = is_this_one
						? `<b>${frappe.utils.escape_html(row.name)}</b>`
						: frappe.utils.get_form_link(doctype, row.name, true);
					const total =
						row.grand_total != null ? format_currency(row.grand_total, currency) : "";
					const date = row.date ? frappe.datetime.str_to_user(row.date) : "";
					return `<tr>
						<td>${link}</td>
						<td>${frappe.utils.escape_html(row.party_name || row.party || "")}</td>
						<td>${date}</td>
						<td>${frappe.utils.escape_html(row.status || "")}</td>
						<td class="text-right">${total}</td>
					</tr>`;
				})
				.join("");

			return `<div class="sf-doc-trail-group">
				<div class="text-muted" style="margin-top: 8px;">${__(doctype)}</div>
				<table class="table table-bordered table-sm">
					<thead><tr>
						<th>${__("Name")}</th>
						<th>${__("Party")}</th>
						<th>${__("Date")}</th>
						<th>${__("Status")}</th>
						<th class="text-right">${__("Total")}</th>
					</tr></thead>
					<tbody>${items}</tbody>
				</table>
			</div>`;
		})
		.join("");

	return `<div>${groups || `<div class="text-muted">${__("No related documents found.")}</div>`}</div>`;
}
