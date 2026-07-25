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

# Reference types a Payment Advice may point at. Enforced server-side as well as in the
# form, because a Dynamic Link accepts any DocType name over the API otherwise.
ALLOWED_REFERENCE_DOCTYPES = (
    "Purchase Invoice",
    "Sales Invoice",
    "Journal Entry",
    "Expense Claim",
    "Purchase Order",
    "Sales Order",
)

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
        self.validate_references()
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
        # the vouchers are free again — make sure their status on this advice reads true
        refresh_reference_status(self.name)

    # ── reference integrity ──────────────────────────────────────────────────────
    def validate_references(self):
        """Dynamic Link safety: right doctype, right company, right party, no doubles.

        The form filters these pickers, but a Dynamic Link will accept anything over the
        API, so every rule is enforced here too.
        """
        seen = {}

        for row in self.payment_advice_reference:
            if not (row.reference_doctype and row.reference_record):
                frappe.throw(
                    _("Row #%s: select both a reference type and a reference document.") % row.idx
                )

            if row.reference_doctype not in ALLOWED_REFERENCE_DOCTYPES:
                frappe.throw(
                    _("Row #%(idx)s: %(dt)s cannot be referenced on a Payment Advice.")
                    % {"idx": row.idx, "dt": _(row.reference_doctype)}
                )

            if not frappe.db.exists(row.reference_doctype, row.reference_record):
                frappe.throw(
                    _("Row #%(idx)s: %(dt)s %(name)s does not exist.")
                    % {
                        "idx": row.idx,
                        "dt": _(row.reference_doctype),
                        "name": frappe.bold(row.reference_record),
                    }
                )

            key = (row.reference_doctype, row.reference_record)
            if key in seen:
                frappe.throw(
                    _("Row #%(idx)s: %(name)s is already on row #%(other)s.")
                    % {"idx": row.idx, "name": frappe.bold(row.reference_record), "other": seen[key]}
                )
            seen[key] = row.idx

            meta = frappe.get_meta(row.reference_doctype)
            values = frappe.db.get_value(
                row.reference_doctype,
                row.reference_record,
                ["docstatus", "company"] + ([self.party_field()] if meta.has_field(self.party_field()) else []),
                as_dict=True,
            ) or frappe._dict()

            if cint(values.get("docstatus")) != 1:
                frappe.throw(
                    _("Row #%(idx)s: %(name)s is not submitted.")
                    % {"idx": row.idx, "name": frappe.bold(row.reference_record)}
                )

            if values.get("company") and values.get("company") != self.company:
                frappe.throw(
                    _("Row #%(idx)s: %(name)s belongs to company %(other)s, not %(company)s.")
                    % {
                        "idx": row.idx,
                        "name": frappe.bold(row.reference_record),
                        "other": frappe.bold(values.get("company")),
                        "company": frappe.bold(self.company),
                    }
                )

            row_party = values.get(self.party_field())
            if row_party and row_party != self.party:
                frappe.throw(
                    _("Row #%(idx)s: %(name)s belongs to %(other)s, not %(party)s.")
                    % {
                        "idx": row.idx,
                        "name": frappe.bold(row.reference_record),
                        "other": frappe.bold(row_party),
                        "party": frappe.bold(self.party),
                    }
                )

        self.validate_not_advised_elsewhere()

    def party_field(self):
        return {"Supplier": "supplier", "Customer": "customer", "Employee": "employee"}.get(
            self.party_type, "supplier"
        )

    def validate_not_advised_elsewhere(self):
        """Stop the same voucher being paid twice through two live advices."""
        records = [r.reference_record for r in self.payment_advice_reference if r.reference_record]
        if not records:
            return

        clashes = frappe.get_all(
            "Payment Advice Reference",
            filters={
                "reference_record": ["in", records],
                "parenttype": "Payment Advice",
                "parent": ["!=", self.name or ""],
                "docstatus": 1,
            },
            fields=["parent", "reference_record", "allocated_amount"],
        )

        for clash in clashes:
            if flt(clash.allocated_amount) <= 0:
                continue
            advice_status = frappe.db.get_value("Payment Advice", clash.parent, "status")
            if advice_status in (STATUS_CANCELLED,):
                continue
            frappe.throw(
                _("%(name)s is already allocated on Payment Advice %(advice)s (%(status)s).")
                % {
                    "name": frappe.bold(clash.reference_record),
                    "advice": frappe.bold(clash.parent),
                    "status": advice_status,
                }
            )

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

            # live status of the voucher, so the advice never shows a stale picture
            row.reference_status = source("status") if meta.has_field("status") else None

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
def get_outstanding_documents(
    company: str,
    party_type: str,
    party: str,
    party_account: str = None,
    from_posting_date: str = None,
    to_posting_date: str = None,
    from_due_date: str = None,
    to_due_date: str = None,
    cost_center: str = None,
    from_amount: float = None,
    to_amount: float = None,
    get_outstanding_invoices: int = 1,
    get_orders_to_be_billed: int = 0,
):
    """Outstanding vouchers for a party, using ERPNext's own Payment Entry engine.

    Delegates to `erpnext…payment_entry.get_outstanding_reference_documents`, which is the
    same code the Payment Entry form's "Get Outstanding Invoices" runs. That buys the real
    behaviour rather than an approximation of it: Payment Ledger based outstanding (so
    part-payments, credit notes and return invoices are already netted), payment-term
    splitting, supplier block-status checks, accounting-dimension filters, and the party
    permission check it performs internally.

    Returns rows shaped for the Payment Advice Reference table.
    """
    frappe.has_permission("Payment Advice", "write", throw=True)

    from erpnext.accounts.doctype.payment_entry.payment_entry import (
        get_outstanding_reference_documents,
    )

    if not company:
        frappe.throw(_("Company is required."))
    if not (party_type and party):
        frappe.throw(_("Party Type and Party are required."))
    if party_type not in PAY_PARTY_TYPES + RECEIVE_PARTY_TYPES:
        frappe.throw(_("Unsupported Party Type: %s") % party_type)

    party_account = party_account or get_party_account(party_type, party, company)
    if not party_account:
        frappe.throw(
            _("No default account is set for %(party_type)s %(party)s.")
            % {"party_type": _(party_type), "party": frappe.bold(party)}
        )

    args = {
        "company": company,
        "party_type": party_type,
        "party": party,
        "party_account": party_account,
        "payment_type": get_payment_type(party_type),
        "posting_date": nowdate(),
        "get_outstanding_invoices": cint(get_outstanding_invoices),
        "get_orders_to_be_billed": cint(get_orders_to_be_billed),
        "cost_center": cost_center,
        "from_posting_date": from_posting_date,
        "to_posting_date": to_posting_date,
        "from_due_date": from_due_date,
        "to_due_date": to_due_date,
    }

    vouchers = get_outstanding_reference_documents(args) or []
    return shape_reference_rows(
        vouchers, cost_center=cost_center, from_amount=from_amount, to_amount=to_amount
    )


def shape_reference_rows(vouchers, cost_center=None, from_amount=None, to_amount=None):
    """Map ERPNext's outstanding rows onto Payment Advice Reference rows.

    Verified against live data: the engine returns account, bill_no, currency, due_date,
    exchange_rate, invoice_amount, outstanding_amount, payment_amount, posting_date,
    voucher_no, voucher_type. `payment_term` appears only for term-allocated invoices and
    `total_amount` not at all, so both are read defensively. Only cost_center and status
    need a extra lookup, since the engine does not carry them.
    """
    today = getdate(nowdate())
    default_currency = frappe.db.get_default("currency")
    rows = []

    for voucher in vouchers:
        voucher = frappe._dict(voucher)
        outstanding = flt(voucher.get("outstanding_amount"))
        if outstanding <= 0:
            continue
        if from_amount and outstanding < flt(from_amount):
            continue
        if to_amount and outstanding > flt(to_amount):
            continue

        doctype = voucher.get("voucher_type")
        name = voucher.get("voucher_no")
        if doctype not in ALLOWED_REFERENCE_DOCTYPES or not name:
            continue

        meta = frappe.get_meta(doctype)
        lookup = [f for f in ("cost_center", "status") if meta.has_field(f)]
        extra = (
            frappe.db.get_value(doctype, name, lookup, as_dict=True) if lookup else None
        ) or frappe._dict()

        invoice_amount = flt(voucher.get("invoice_amount")) or flt(voucher.get("total_amount"))
        due_date = voucher.get("due_date") or voucher.get("posting_date")

        rows.append(
            {
                "reference_doctype": doctype,
                "reference_record": name,
                "bill_no": voucher.get("bill_no"),
                "date": voucher.get("posting_date"),
                "amount": invoice_amount,
                "settled_amount": flt(invoice_amount - outstanding, 3),
                "net_payable_amount": outstanding,
                "ageing": max(0, (today - getdate(due_date)).days) if due_date else 0,
                "payment_term": voucher.get("payment_term"),
                "currency": voucher.get("currency") or default_currency,
                "exchange_rate": flt(voucher.get("exchange_rate")) or 1.0,
                "cost_center": extra.get("cost_center") or cost_center,
                "reference_status": extra.get("status"),
            }
        )

    rows.sort(key=lambda r: (-r["ageing"], -r["net_payable_amount"]))
    return rows


def refresh_reference_status(advice_name):
    """Re-read every referenced voucher's status and outstanding on a submitted advice.

    Child rows are written with `frappe.db.set_value` — never `parent.save()`, which would
    re-validate the submitted document and reject fields lacking allow_on_submit.
    """
    rows = frappe.get_all(
        "Payment Advice Reference",
        filters={"parent": advice_name, "parenttype": "Payment Advice"},
        fields=["name", "reference_doctype", "reference_record", "amount"],
    )

    for row in rows:
        if not (row.reference_doctype and row.reference_record):
            continue
        if not frappe.db.exists(row.reference_doctype, row.reference_record):
            continue

        meta = frappe.get_meta(row.reference_doctype)
        updates = {}

        if meta.has_field("status"):
            updates["reference_status"] = frappe.db.get_value(
                row.reference_doctype, row.reference_record, "status"
            )

        if meta.has_field("outstanding_amount"):
            outstanding = flt(
                frappe.db.get_value(row.reference_doctype, row.reference_record, "outstanding_amount")
            )
            updates["net_payable_amount"] = outstanding
            updates["settled_amount"] = flt(flt(row.amount) - outstanding, 3)

        if updates:
            frappe.db.set_value(
                "Payment Advice Reference", row.name, updates, update_modified=False
            )


def get_due_cutoff(days=0):
    """Cut-off date for "due within N days" — used by the automation layer."""
    return add_days(getdate(nowdate()), cint(days))
