// sf_trading/public/js/payment_entry_journal.js
// "Get Outstanding Journal Entries" on the Payment Entry, beside Get Outstanding Invoices and Orders
// (sf_trading/payment_entry_journal.py). It asks the same questions, lists the party's open journals with
// their remark and cheque number, and ADDS the ticked ones to the references already on the entry. The
// amounts are allocated by the form's own allocation, as the other two buttons do.
// UI only: the Payment Entry refuses on save whatever it cannot settle.

frappe.provide("sf_trading.pe_journal");

(function (pej) {
	pej.party_account = (frm) => (frm.doc.payment_type == "Receive" ? frm.doc.paid_from : frm.doc.paid_to);

	pej.problem = function (frm) {
		if (frm.doc.payment_type != "Pay" && frm.doc.payment_type != "Receive") {
			return __("A Journal Entry is settled by a Pay or Receive entry.");
		}
		if (!frm.doc.company || !frm.doc.party_type || !frm.doc.party) {
			return __("Choose the company, the party type and the party first.");
		}
		if (!pej.party_account(frm)) {
			return __("Choose the party's account first: {0}.", [frm.doc.payment_type == "Receive" ? __("Account Paid From") : __("Account Paid To")]);
		}
		return null;
	};

	pej.filter_fields = function (frm) {
		let fields = [
			{ fieldtype: "Section Break", label: __("Posting Date") },
			{ fieldtype: "Date", label: __("From Date"), fieldname: "from_posting_date" },
			{ fieldtype: "Column Break" },
			{ fieldtype: "Date", label: __("To Date"), fieldname: "to_posting_date" },
			{ fieldtype: "Section Break", label: __("Outstanding Amount") },
			{ fieldtype: "Float", label: __("Greater Than Amount"), fieldname: "outstanding_amt_greater_than", default: 0 },
			{ fieldtype: "Column Break" },
			{ fieldtype: "Float", label: __("Less Than Amount"), fieldname: "outstanding_amt_less_than" },
		];
		if (frm.dimension_filters) {
			const break_at = Math.ceil(frm.dimension_filters.length / 2);
			fields.push({ fieldtype: "Section Break" });
			frm.dimension_filters.forEach((elem, idx) => {
				fields.push({
					fieldtype: "Link",
					label: elem.document_type == "Cost Center" ? "Cost Center" : elem.label,
					options: elem.document_type,
					fieldname: elem.fieldname || elem.document_type,
				});
				if (idx + 1 == break_at) fields.push({ fieldtype: "Column Break" });
			});
		}
		fields.push(
			{ fieldtype: "Section Break" },
			{
				fieldtype: "Data",
				label: __("Remark or Cheque No Contains"),
				fieldname: "remark",
				description: __("A word from the journal's remark, title or cheque number."),
			},
			{ fieldtype: "Check", label: __("Allocate Payment Amount"), fieldname: "allocate_payment_amount", default: 1 }
		);
		return fields;
	};

	pej.ask = function (frm) {
		const problem = pej.problem(frm);
		if (problem) {
			frappe.msgprint(problem);
			return;
		}
		frappe.prompt(
			pej.filter_fields(frm),
			(filters) => {
				frm.events.validate_filters_data && frm.events.validate_filters_data(frm, filters);
				pej.fetch(frm, filters);
			},
			__("Filters"),
			__("Get Outstanding Journal Entries")
		);
	};

	pej.fetch = function (frm, filters) {
		const args = Object.assign(
			{
				posting_date: frm.doc.posting_date,
				company: frm.doc.company,
				party_type: frm.doc.party_type,
				payment_type: frm.doc.payment_type,
				party: frm.doc.party,
				party_account: pej.party_account(frm),
				cost_center: filters.cost_center,
			},
			filters
		);
		if (frm.doc.book_advance_payments_in_separate_party_account) args.book_advance_payments_in_separate_party_account = true;
		frappe.call({
			method: "sf_trading.payment_entry_journal.outstanding_journal_entries",
			args: { args: args },
			freeze: true,
			callback(r) {
				const entries = r.message || [];
				if (!entries.length) {
					frappe.msgprint(
						__("No outstanding journal entries were found for {0} {1} that this entry can settle.", [
							__(frm.doc.party_type),
							frappe.utils.escape_html(frm.doc.party),
						])
					);
					return;
				}
				pej.choose(frm, entries, filters);
			},
		});
	};

	pej.already_in = (frm, name) =>
		(frm.doc.references || []).some((d) => d.reference_doctype == "Journal Entry" && d.reference_name == name);

	pej.choose = function (frm, entries, filters) {
		const rows = entries.map((e) => ({
			add: pej.already_in(frm, e.voucher_no) ? 0 : 1,
			voucher_no: e.voucher_no,
			posting_date: e.posting_date,
			journal_type: e.journal_type,
			remark: [e.title, e.user_remark, e.cheque_no ? __("Cheque {0}", [e.cheque_no]) : ""].filter((x) => x).join(" · "),
			outstanding_amount: e.outstanding_amount,
			invoice_amount: e.invoice_amount,
			account: e.account,
			exchange_rate: e.exchange_rate,
			due_date: e.due_date,
		}));
		const dialog = new frappe.ui.Dialog({
			title: __("Outstanding Journal Entries"),
			size: "extra-large",
			fields: [
				{
					fieldname: "entries",
					fieldtype: "Table",
					cannot_add_rows: true,
					cannot_delete_rows: true,
					in_place_edit: true,
					data: rows,
					get_data: () => rows,
					fields: [
						{ fieldname: "add", fieldtype: "Check", label: __("Add"), in_list_view: 1, columns: 1 },
						{ fieldname: "voucher_no", fieldtype: "Link", options: "Journal Entry", label: __("Journal Entry"), in_list_view: 1, read_only: 1, columns: 2 },
						{ fieldname: "posting_date", fieldtype: "Date", label: __("Date"), in_list_view: 1, read_only: 1, columns: 1 },
						{ fieldname: "remark", fieldtype: "Small Text", label: __("Remark"), in_list_view: 1, read_only: 1, columns: 4 },
						{ fieldname: "outstanding_amount", fieldtype: "Currency", label: __("Outstanding"), in_list_view: 1, read_only: 1, columns: 2 },
					],
				},
			],
			primary_action_label: __("Add to Payment References"),
			primary_action(values) {
				const picked = (values.entries || []).filter((row) => cint(row.add));
				if (!picked.length) {
					frappe.show_alert({ message: __("Tick at least one journal entry."), indicator: "orange" });
					return;
				}
				dialog.hide();
				pej.add(frm, picked, filters);
			},
		});
		dialog.show();
	};

	// the rows are made as the form's own Get Outstanding Invoices makes them
	pej.add = function (frm, picked, filters) {
		const company_currency = frappe.get_doc(":Company", frm.doc.company).default_currency;
		const party_currency = frm.doc.payment_type == "Receive" ? frm.doc.paid_from_account_currency : frm.doc.paid_to_account_currency;
		let total = 0;
		picked.forEach((d) => {
			if (pej.already_in(frm, d.voucher_no)) return;
			const c = frm.add_child("references");
			c.reference_doctype = "Journal Entry";
			c.reference_name = d.voucher_no;
			c.due_date = d.due_date;
			c.total_amount = d.invoice_amount;
			c.outstanding_amount = d.outstanding_amount;
			c.account = d.account;
			c.exchange_rate = party_currency != company_currency ? d.exchange_rate : 1;
			total += flt(d.outstanding_amount);
		});
		frm.refresh_field("references");
		const settles_own_side =
			(frm.doc.payment_type == "Receive" && frm.doc.party_type == "Customer") ||
			(frm.doc.payment_type == "Pay" && (frm.doc.party_type == "Supplier" || frm.doc.party_type == "Employee"));
		frappe.flags.allocate_payment_amount = filters.allocate_payment_amount;
		const allocate = () =>
			frm.events.allocate_party_amount_against_ref_docs(frm, frm.doc.payment_type == "Receive" ? frm.doc.paid_amount : frm.doc.received_amount, false);
		if (settles_own_side && total > 0 && !frm.doc.paid_amount) {
			frm.set_value("paid_amount", total).then(allocate);
		} else {
			allocate();
		}
	};
})(sf_trading.pe_journal);

frappe.ui.form.on("Payment Entry", {
	sf_get_outstanding_journal_entries(frm) {
		sf_trading.pe_journal.ask(frm);
	},
});
