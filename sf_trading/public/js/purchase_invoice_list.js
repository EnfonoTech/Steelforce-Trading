// apps/sf_trading/sf_trading/public/js/purchase_invoice_list.js
// Purchase Invoice list: raise Payment Advices for the ticked invoices, grouped by supplier.
// One advice per supplier, however many invoices you tick. Anything already sitting on a
// live advice is skipped and reported rather than silently double-paid.
//
// Loaded unbundled via doctype_list_js, so edits go live without `bench build`.

frappe.listview_settings["Purchase Invoice"] = Object.assign(
	frappe.listview_settings["Purchase Invoice"] || {},
	{
		onload(listview) {
			listview.page.add_actions_menu_item(__("Create Payment Advice (by supplier)"), () => {
				const selected = listview.get_checked_items();
				if (!selected.length) {
					frappe.msgprint(__("Tick the invoices to pay first."));
					return;
				}

				const payable = selected.filter((d) => flt(d.outstanding_amount) > 0 && d.docstatus === 1);
				if (!payable.length) {
					frappe.msgprint(
						__("None of the ticked invoices are submitted with an outstanding amount.")
					);
					return;
				}

				const suppliers = Array.from(new Set(payable.map((d) => d.supplier)));
				const total = payable.reduce((sum, d) => sum + flt(d.outstanding_amount), 0);

				const d = new frappe.ui.Dialog({
					title: __("Create Payment Advice"),
					fields: [
						{
							fieldtype: "HTML",
							options: `<p>${__(
								"{0} invoice(s) across {1} supplier(s) — {2} outstanding. One advice per supplier will be created as a draft.",
								[payable.length, suppliers.length, format_currency(total)]
							)}</p>`,
						},
						{
							fieldname: "mode_of_payment",
							label: __("Mode of Payment"),
							fieldtype: "Link",
							options: "Mode of Payment",
						},
						{
							fieldname: "bank_account",
							label: __("Company Bank Account"),
							fieldtype: "Link",
							options: "Bank Account",
							get_query: () => ({
								filters: { company: payable[0].company, is_company_account: 1 },
							}),
						},
						{
							fieldname: "approver",
							label: __("Approver"),
							fieldtype: "Link",
							options: "Employee",
						},
						{ fieldname: "remarks", label: __("Remarks"), fieldtype: "Small Text" },
					],
					primary_action_label: __("Create"),
					primary_action(values) {
						d.hide();
						frappe.call({
							method: "sf_trading.api.payment_advice_builder.create_advices_from_invoices",
							args: {
								invoices: payable.map((row) => row.name),
								options: Object.assign({ doctype: "Purchase Invoice" }, values),
							},
							freeze: true,
							freeze_message: __("Creating Payment Advices…"),
							callback(r) {
								const result = r.message;
								if (!result) return;

								const created = (result.created || [])
									.map(
										(row) =>
											`<li><a href="/app/payment-advice/${encodeURIComponent(
												row.advice
											)}" target="_blank">${row.advice}</a> — ${frappe.utils.escape_html(
												row.party
											)} — ${format_currency(row.amount)}</li>`
									)
									.join("");
								const skipped = (result.skipped_already_advised || []).length
									? `<p>${__("Skipped (already on a live advice)")}: ${(
											result.skipped_already_advised || []
									  )
											.map(frappe.utils.escape_html)
											.join(", ")}</p>`
									: "";
								const failed = (result.failed || [])
									.map(
										(row) =>
											`<li>${frappe.utils.escape_html(row.party)} — <span class="text-danger">${
												frappe.utils.escape_html(row.error)
											}</span></li>`
									)
									.join("");

								frappe.msgprint({
									title: __("Payment Advices"),
									indicator: failed ? "orange" : "green",
									message: `${created ? `<ul>${created}</ul>` : ""}${skipped}${
										failed ? `<p>${__("Failed")}:</p><ul>${failed}</ul>` : ""
									}`,
								});
								listview.clear_checked_items();
								listview.refresh();
							},
						});
					},
				});
				d.show();
			});
		},
	}
);
