// apps/sf_trading/sf_trading/public/js/sales_invoice_list.js
// Sales Invoice list: raise Payment Advices for the ticked invoices, grouped by customer.
// The mirror of the Purchase Invoice action — the customer (Inward) side had no list action at
// all, so collections could only be built one advice at a time.

frappe.listview_settings["Sales Invoice"] = Object.assign(
	frappe.listview_settings["Sales Invoice"] || {},
	{
		onload(listview) {
			listview.page.add_actions_menu_item(__("Create Payment Advice (by customer)"), () => {
				const selected = listview.get_checked_items();
				if (!selected.length) {
					frappe.msgprint(__("Tick the invoices to collect against first."));
					return;
				}

				const collectable = selected.filter(
					(d) => flt(d.outstanding_amount) > 0 && d.docstatus === 1
				);
				if (!collectable.length) {
					frappe.msgprint(
						__("None of the ticked invoices are submitted with an outstanding amount.")
					);
					return;
				}

				const customers = Array.from(new Set(collectable.map((d) => d.customer)));
				const total = collectable.reduce((sum, d) => sum + flt(d.outstanding_amount), 0);

				const d = new frappe.ui.Dialog({
					title: __("Create Payment Advice"),
					fields: [
						{
							fieldtype: "HTML",
							options: `<p>${__(
								"{0} invoice(s) across {1} customer(s) — {2} outstanding. One advice per customer will be created as a draft.",
								[collectable.length, customers.length, format_currency(total)]
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
								filters: { company: collectable[0].company, is_company_account: 1 },
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
								invoices: collectable.map((row) => row.name),
								options: Object.assign({ doctype: "Sales Invoice" }, values),
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
