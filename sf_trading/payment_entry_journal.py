# sf_trading/payment_entry_journal.py
"""Get Outstanding Journal Entries, on the Payment Entry, beside Get Outstanding Invoices and Orders.

ERPNext's Payment Entry can settle a Journal Entry (a journal that debits a customer or credits a
supplier, an opening balance, a manual accrual), but only as a few rows in the middle of the invoices
that Get Outstanding Invoices lists, with nothing to tell one from another: no remark, no cheque number,
and no way to ask for the journals alone. This adds a button that does:

* it asks the same questions as the others (posting date, outstanding amount, dimensions) and one more, a
  word from the journal's remark or cheque number;
* it lists the party's open journals with what identifies them (journal type, remark, cheque) and lets the
  accountant tick which to bring in;
* the ticked ones are ADDED to the references already on the entry (the invoices and orders buttons replace
  them), so an invoice run and a journal run can be combined, and a journal already there is not added twice;
* the amounts are allocated exactly as the other two buttons allocate them.

Which journals are offered is ERPNext's own answer (`get_outstanding_reference_documents`, the Payment
Ledger), kept to Journal Entries and to those the entry can settle in its own direction: the same test the
Payment Entry applies on save (`validate_journal_entry`), made here so that nothing offered is refused later.
A journal that sits on the other side (a debit against a supplier, a credit against a customer) is a
credit note in disguise, which a Payment Entry cannot settle; it is not offered.
"""

import frappe
from frappe import _
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields
from frappe.utils import cstr, flt

DOCTYPE = "Payment Entry"
BUTTON = "sf_get_outstanding_journal_entries"
JOURNAL = "Journal Entry"
DIRECTIONS = {"Receive": "debit", "Pay": "credit"}


def ensure_custom_fields():
	"""after_migrate: the button, in the Reference section beside Get Outstanding Invoices / Orders."""
	create_custom_fields(
		{
			DOCTYPE: [
				{
					"fieldname": BUTTON,
					"label": "Get Outstanding Journal Entries",
					"fieldtype": "Button",
					"insert_after": "get_outstanding_orders",
					"depends_on": "eval:doc.docstatus==0",
				}
			]
		},
		ignore_validate=True,
		update=True,
	)


def settleable(names, party_account, party, payment_type) -> set:
	"""The journals, of these, that the Payment Entry will accept as a reference: a submitted journal with a row on
	the party's account and the party, not already tied to another voucher, on the side this entry settles."""
	column = DIRECTIONS.get(payment_type)
	if not names or not column:
		return set()
	rows = frappe.get_all(
		"Journal Entry Account",
		filters={"parent": ["in", list(names)], "parenttype": JOURNAL, "account": party_account, "party": party, "docstatus": 1},
		fields=["parent", "debit", "credit", "reference_type"],
		limit_page_length=0,
	)
	return {r.parent for r in rows if (not r.reference_type or r.reference_type in ("Sales Order", "Purchase Order")) and flt(r.get(column)) > 0}


def describe(names) -> dict:
	"""What identifies each journal to the person choosing."""
	if not names:
		return {}
	rows = frappe.get_all(
		JOURNAL,
		filters={"name": ["in", list(names)]},
		fields=["name", "voucher_type", "title", "user_remark", "cheque_no", "cheque_date"],
		limit_page_length=0,
	)
	return {r.name: r for r in rows}


def matches(entry, text) -> bool:
	"""A word typed by the person, found in the remark, the title or the cheque number."""
	if not text:
		return True
	text = cstr(text).strip().lower()
	return any(text in cstr(entry.get(key)).lower() for key in ("user_remark", "title", "cheque_no"))


@frappe.whitelist()
def outstanding_journal_entries(args) -> list:
	"""The open journals of a party that a Payment Entry in this direction can settle, oldest first.

	`args` are the ones the form sends ERPNext's Get Outstanding Invoices (company, party_type, party,
	party_account, payment_type, posting date range, outstanding range, dimensions) plus `remark`."""
	args = frappe._dict(frappe.parse_json(args) or {})
	for key in ("company", "party_type", "party", "party_account", "payment_type"):
		if not args.get(key):
			frappe.throw(_("%s is needed to find the outstanding journal entries.") % _(key.replace("_", " ").title()))
	if args.payment_type not in DIRECTIONS:
		frappe.throw(_("Journal entries are settled by a Pay or Receive entry, not an Internal Transfer."))
	frappe.has_permission(DOCTYPE, "read", throw=True)
	frappe.has_permission(JOURNAL, "read", throw=True)

	from erpnext.accounts.doctype.payment_entry.payment_entry import get_outstanding_reference_documents

	args["get_outstanding_invoices"] = True
	args.pop("get_orders_to_be_billed", None)
	# the voucher filter the form can send for one invoice is not a journal's
	for key in ("voucher_type", "voucher_no", "vouchers"):
		args.pop(key, None)
	rows = [r for r in (get_outstanding_reference_documents(args, validate=True) or []) if r.get("voucher_type") == JOURNAL]
	names = [r.voucher_no for r in rows]
	ok = settleable(names, args.party_account, args.party, args.payment_type)
	info = describe(ok)
	out = []
	for row in rows:
		if row.voucher_no not in ok:
			continue
		about = info.get(row.voucher_no) or frappe._dict()
		entry = dict(row)
		entry.update(
			journal_type=about.get("voucher_type"),
			title=about.get("title"),
			user_remark=about.get("user_remark"),
			cheque_no=about.get("cheque_no"),
			cheque_date=about.get("cheque_date"),
		)
		if matches(entry, args.get("remark")):
			out.append(entry)
	out.sort(key=lambda e: (cstr(e.get("posting_date")), e.get("voucher_no")))
	return out
