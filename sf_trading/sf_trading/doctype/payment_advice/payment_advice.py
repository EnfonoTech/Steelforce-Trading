# apps/sf_trading/sf_trading/sf_trading/doctype/payment_advice/payment_advice.py
"""Payment Advice — authorise payment against several outstanding documents of one party.

A Payment Advice groups many references (Purchase Invoices, Sales Invoices, Expense Claims…)
for a SINGLE party and carries one authorised `payment_amount`, allocated across those
references in row order. Submitting it records approval; creating the Payment Entry turns it
into money movement. This is what ERPNext's Payment Request cannot express: PR is
one-document-per-request, so it cannot say "pay these nine invoices for this supplier".

Ported from EnfonoTech/payment_advice (MIT) and corrected. Fixes versus that app:
  * Payment Entry takes `company` from THIS document instead of the session user's default.
  * `get_exchange_rate()` is called as (from_currency, to_currency, date) — the original
    passed `company` as the target currency, so every multi-currency advice was wrong.
  * All money maths goes through `flt()`; nulls no longer raise or write None amounts.
  * Allocation stops once the authorised amount is exhausted instead of appending
    zero-allocation reference rows, which ERPNext rejects.
  * The Payment Entry is finished with `set_missing_values()`, so base amounts, party balance
    and unallocated amount are computed by ERPNext instead of left unset.
  * The whitelisted endpoint checks permissions and no longer inserts with
    `ignore_permissions=True`.
  * Party and company account lookups use ERPNext's own helpers.
  * The advice is stamped with its Payment Entry on the PE's `on_submit` (cleared on
    `on_cancel`), not in `before_submit` — a failed submit can no longer leave a false link.
  * Adds a real `status` lifecycle and a `payment_entry` Link (was a free-text Data field).
  * Every user-facing string is translatable.

No SQL is constructed here: reads go through frappe.db.get_value / frappe.get_all.
"""

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import add_days, cint, flt, getdate, get_link_to_form, money_in_words, nowdate

from erpnext.accounts.party import get_party_account
from erpnext.setup.utils import get_exchange_rate

PAY_PARTY_TYPES = ("Supplier", "Employee")
RECEIVE_PARTY_TYPES = ("Customer",)

STATUS_DRAFT = "Draft"
STATUS_PENDING = "Pending Approval"
STATUS_APPROVED = "Approved"
STATUS_PARTLY_PAID = "Partly Paid"
STATUS_PAID = "Paid"
STATUS_CANCELLED = "Cancelled"

PARTY_NAME_FIELD = {
    "Supplier": "supplier_name",
    "Customer": "customer_name",
    "Employee": "employee_name",
}


class PaymentAdvice(Document):
    # ── lifecycle ────────────────────────────────────────────────────────────────
    def validate(self):
        self.set_party_name()
        self.set_reference_details()
        self.compute_totals()
        self.validate_payment_amount()
        self.allocate_payment()
        self.set_words()
        self.set_status()

    def before_submit(self):
        self.validate_approver()

    def on_submit(self):
        self.db_set("status", STATUS_APPROVED, update_modified=False)

    def on_cancel(self):
        if self.payment_entry:
            docstatus = frappe.db.get_value("Payment Entry", self.payment_entry, "docstatus")
            if cint(docstatus) == 1:
                frappe.throw(
                    _("Cancel Payment Entry %s before cancelling this advice.")
                    % frappe.bold(self.payment_entry)
                )
        self.db_set("status", STATUS_CANCELLED, update_modified=False)

    # ── derivation ───────────────────────────────────────────────────────────────
    def set_party_name(self):
        if not (self.party_type and self.party):
            return
        field = PARTY_NAME_FIELD.get(self.party_type)
        if field:
            self.party_name = frappe.db.get_value(self.party_type, self.party, field) or self.party

    def set_reference_details(self):
        """Fill each row from its source document. Ageing is measured from the due date."""
        today = getdate(nowdate())

        for row in self.payment_advice_reference:
            if not (row.reference_doctype and row.reference_record):
                continue

            meta = frappe.get_meta(row.reference_doctype)

            def source(fieldname):
                if not meta.has_field(fieldname):
                    return None
                return frappe.db.get_value(row.reference_doctype, row.reference_record, fieldname)

            if not row.date:
                row.date = source("posting_date")
            if not row.amount:
                row.amount = flt(source("grand_total"))
            if not row.currency:
                row.currency = source("currency") or frappe.db.get_default("currency")
            if not row.exchange_rate:
                row.exchange_rate = flt(source("conversion_rate")) or 1.0
            if not row.cost_center:
                row.cost_center = source("cost_center")

            # outstanding, where the reference doctype tracks it
            if meta.has_field("outstanding_amount"):
                outstanding = flt(source("outstanding_amount"))
                row.settled_amount = flt(flt(row.amount) - outstanding, 3)
                row.net_payable_amount = outstanding
            elif not row.net_payable_amount:
                row.net_payable_amount = flt(flt(row.amount) - flt(row.settled_amount), 3)

            due = source("due_date") if meta.has_field("due_date") else None
            due = getdate(due or row.date or today)
            row.ageing = max(0, (today - due).days)

            rate = flt(row.exchange_rate) or 1.0
            row.amount_in_currency = flt(flt(row.amount) / rate, 3)
            row.settled_amount_in_currency = flt(flt(row.settled_amount) / rate, 3)
            row.net_payable_amount_in_currency = flt(flt(row.net_payable_amount) / rate, 3)

    def compute_totals(self):
        self.amount = flt(sum(flt(r.amount) for r in self.payment_advice_reference), 3)
        self.amount_paid = flt(sum(flt(r.settled_amount) for r in self.payment_advice_reference), 3)
        self.amount_to_be_settled = flt(
            sum(flt(r.net_payable_amount) for r in self.payment_advice_reference), 3
        )

        pending = flt(self.amount_to_be_settled) - flt(self.payment_amount)
        self.pending_amount = flt(pending, 3) if pending > 0 else 0.0

        rate = flt(self.exchange_rate) or 1.0
        if self.transaction_currency:
            self.amount_in_trans_cur = flt(flt(self.amount) / rate, 3)
            self.amount_paid_in_trans_curr = flt(flt(self.amount_paid) / rate, 3)
            self.amount_to_be_settled_trans_curr = flt(flt(self.amount_to_be_settled) / rate, 3)

    def validate_payment_amount(self):
        if flt(self.payment_amount) <= 0:
            frappe.throw(_("Payment Amount must be greater than zero."))

        if flt(self.payment_amount) > flt(self.amount_to_be_settled):
            frappe.throw(
                _("Payment Amount %(paid)s exceeds the total payable of %(payable)s across the references.")
                % {
                    "paid": frappe.bold(frappe.utils.fmt_money(flt(self.payment_amount))),
                    "payable": frappe.bold(frappe.utils.fmt_money(flt(self.amount_to_be_settled))),
                }
            )

    def allocate_payment(self):
        """Spread `payment_amount` across the references in row order.

        Rows beyond the authorised amount get 0 and simply are not paid this time — they stay
        on the advice so it still shows the full picture. The Payment Entry builder skips them.
        """
        balance = flt(self.payment_amount)
        for row in self.payment_advice_reference:
            payable = flt(row.net_payable_amount)
            if balance <= 0 or payable <= 0:
                row.allocated_amount = 0.0
                continue
            allocated = payable if payable <= balance else balance
            row.allocated_amount = flt(allocated, 3)
            balance = flt(balance - allocated, 3)

    def set_words(self):
        company_currency = frappe.db.get_value("Company", self.company, "default_currency")
        if self.amount_to_be_settled:
            self.amount_to_be_settled_in_words = money_in_words(
                self.amount_to_be_settled, company_currency
            )
        if self.amount_to_be_settled_trans_curr and self.transaction_currency:
            self.amount_words_trans_curr = money_in_words(
                self.amount_to_be_settled_trans_curr, self.transaction_currency
            )

    def set_status(self):
        if self.docstatus == 2:
            self.status = STATUS_CANCELLED
        elif self.docstatus == 0:
            self.status = STATUS_PENDING if self.approver else STATUS_DRAFT
        elif self.payment_entry:
            paid = flt(frappe.db.get_value("Payment Entry", self.payment_entry, "paid_amount"))
            self.status = STATUS_PAID if paid >= flt(self.payment_amount) else STATUS_PARTLY_PAID
        else:
            self.status = STATUS_APPROVED

    def validate_approver(self):
        if not self.approver:
            frappe.throw(_("Select an Approver before submitting."))

        approver_user = frappe.db.get_value("Employee", self.approver, "user_id")
        if not approver_user:
            frappe.throw(_("No user account is linked to Employee %s.") % frappe.bold(self.approver))

        if frappe.session.user == approver_user:
            return

        # a System Manager can always push a stuck advice through
        if "System Manager" in frappe.get_roles():
            return

        frappe.throw(
            _("Only %s (the selected Approver) can submit this advice.") % frappe.bold(approver_user)
        )


# ── Payment Entry creation ───────────────────────────────────────────────────────

def get_payment_type(party_type):
    if party_type in RECEIVE_PARTY_TYPES:
        return "Receive"
    if party_type in PAY_PARTY_TYPES:
        return "Pay"
    frappe.throw(_("Unsupported Party Type for payment: %s") % party_type)


def get_company_account(company, mode_of_payment=None):
    """Bank/cash account for the company side.

    Mirrors ERPNext's own lookup (the Mode of Payment Account child row for this company),
    then falls back to the company's default bank, then cash account.
    """
    if mode_of_payment:
        account = frappe.db.get_value(
            "Mode of Payment Account",
            {"parent": mode_of_payment, "company": company},
            "default_account",
        )
        if account:
            return account

    return frappe.db.get_value("Company", company, "default_bank_account") or frappe.db.get_value(
        "Company", company, "default_cash_account"
    )


def build_payment_entry(advice):
    """Return an unsaved Payment Entry for a submitted Payment Advice."""
    if not advice.payment_advice_reference:
        frappe.throw(_("Add at least one reference before creating a Payment Entry."))

    allocations = [r for r in advice.payment_advice_reference if flt(r.allocated_amount) > 0]
    if not allocations:
        frappe.throw(_("Nothing is allocated for payment on this advice."))

    company = advice.company
    party_account = get_party_account(advice.party_type, advice.party, company)
    if not party_account:
        frappe.throw(
            _("No default account is set for %(party_type)s %(party)s.")
            % {"party_type": _(advice.party_type), "party": frappe.bold(advice.party)}
        )

    company_account = get_company_account(company, advice.mode_of_payment)
    if not company_account:
        frappe.throw(
            _("Set a default bank or cash account for company %s, or pick a Mode of Payment that has one.")
            % frappe.bold(company)
        )

    payment_type = get_payment_type(advice.party_type)
    paid_from, paid_to = (
        (party_account, company_account)
        if payment_type == "Receive"
        else (company_account, party_account)
    )

    pe = frappe.new_doc("Payment Entry")
    pe.update(
        {
            "payment_type": payment_type,
            "company": company,
            "posting_date": nowdate(),
            "party_type": advice.party_type,
            "party": advice.party,
            "mode_of_payment": advice.mode_of_payment,
            "paid_from": paid_from,
            "paid_to": paid_to,
            "paid_amount": flt(advice.payment_amount),
            "received_amount": flt(advice.payment_amount),
            "cost_center": advice.cost_center,
            "project": advice.project,
            "bank_account": advice.bank_account,
            "custom_payment_advice": advice.name,
            "remarks": advice.remarks,
        }
    )

    if advice.reference_no:
        pe.reference_no = advice.reference_no
        pe.reference_date = advice.reference_date or nowdate()

    for row in allocations:
        pe.append(
            "references",
            {
                "reference_doctype": row.reference_doctype,
                "reference_name": row.reference_record,
                "allocated_amount": flt(row.allocated_amount),
            },
        )

    # currencies: read them, never assume they match
    company_currency = frappe.db.get_value("Company", company, "default_currency")
    pe.paid_from_account_currency = frappe.db.get_value("Account", paid_from, "account_currency")
    pe.paid_to_account_currency = frappe.db.get_value("Account", paid_to, "account_currency")

    if pe.paid_from_account_currency and pe.paid_from_account_currency != company_currency:
        pe.source_exchange_rate = get_exchange_rate(
            pe.paid_from_account_currency, company_currency, pe.posting_date
        )
    if pe.paid_to_account_currency and pe.paid_to_account_currency != company_currency:
        pe.target_exchange_rate = get_exchange_rate(
            pe.paid_to_account_currency, company_currency, pe.posting_date
        )

    # let ERPNext compute base amounts, party balance and unallocated amount
    pe.set_missing_values()
    return pe


@frappe.whitelist()
def create_payment_entry(payment_advice: str, submit: int = 0):
    """Create (and optionally submit) the Payment Entry for a submitted Payment Advice."""
    if not isinstance(payment_advice, str) or not payment_advice:
        frappe.throw(_("Payment Advice is required."))

    advice = frappe.get_doc("Payment Advice", payment_advice)
    advice.check_permission("read")

    if not frappe.has_permission("Payment Entry", "create"):
        frappe.throw(_("You are not permitted to create Payment Entries."), frappe.PermissionError)

    if advice.docstatus != 1:
        frappe.throw(_("Submit the Payment Advice before creating a Payment Entry."))

    if advice.payment_entry:
        existing = frappe.db.get_value("Payment Entry", advice.payment_entry, "docstatus")
        if cint(existing) != 2:
            frappe.throw(
                _("Payment Entry %s already exists for this advice.") % frappe.bold(advice.payment_entry)
            )

    pe = build_payment_entry(advice)
    pe.insert()

    if cint(submit):
        if not frappe.has_permission("Payment Entry", "submit"):
            frappe.throw(
                _("You are not permitted to submit Payment Entries."), frappe.PermissionError
            )
        pe.submit()

    frappe.msgprint(
        _("Payment Entry %s created.") % get_link_to_form("Payment Entry", pe.name),
        indicator="green",
        alert=True,
    )
    return pe.name


@frappe.whitelist()
def get_outstanding_references(party_type: str, party: str, company: str, due_before: str = None):
    """Outstanding documents for a party, shaped for the references table."""
    if not frappe.has_permission("Payment Advice", "write"):
        frappe.throw(_("Not permitted"), frappe.PermissionError)

    if party_type == "Employee":
        return []

    doctype = "Purchase Invoice" if party_type in PAY_PARTY_TYPES else "Sales Invoice"
    party_field = "supplier" if party_type == "Supplier" else "customer"

    filters = {
        "docstatus": 1,
        "company": company,
        party_field: party,
        "outstanding_amount": [">", 0],
    }
    if due_before:
        filters["due_date"] = ["<=", getdate(due_before)]

    rows = frappe.get_all(
        doctype,
        filters=filters,
        fields=[
            "name",
            "posting_date",
            "due_date",
            "grand_total",
            "outstanding_amount",
            "currency",
            "conversion_rate",
            "cost_center",
        ],
        order_by="due_date asc",
    )

    today = getdate(nowdate())
    return [
        {
            "reference_doctype": doctype,
            "reference_record": r.name,
            "date": r.posting_date,
            "amount": flt(r.grand_total),
            "settled_amount": flt(flt(r.grand_total) - flt(r.outstanding_amount), 3),
            "net_payable_amount": flt(r.outstanding_amount),
            "ageing": max(0, (today - getdate(r.due_date or r.posting_date)).days),
            "currency": r.currency,
            "exchange_rate": flt(r.conversion_rate) or 1.0,
            "cost_center": r.cost_center,
        }
        for r in rows
    ]


def get_due_cutoff(days=0):
    """Cut-off date for "due within N days" — used by the automation layer."""
    return add_days(getdate(nowdate()), cint(days))
