# sf_trading/sf_trading/report/pdc_report/pdc_report.py
"""PDC Report — Post-Dated / cheque payments.

Lists Payment Entries whose linked Mode of Payment carries ZATCA payment-means
code '20' (cheque). Robust against nulls / cancelled / messy reference data.

Reminder anchor: the **reference date**, which on a cheque payment is the cheque's own
date. Every date-based figure here counts from it, and falls back to the posting date only
when a row carries no reference date at all. The notification "PDC Cheque Date Reminder"
(Notification fixture in permission_manager) was re-anchored to `reference_date` at the same
time, so the alert and the Reminder Date column still name the same day — the column exists
so an accountant can see, in the report, the day the alert lands. Keep the two in step.

Internal Transfer: a cheque is banked -- in full or in parts -- by Internal Transfer Payment
Entries created by `sf_trading.pdc_transfer`, each naming the cheque in
`custom_pdc_source_payment_entry`; a cheque returned unpaid is reversed by a Journal Entry that
names it the same way. Status, Cleared, Returned and Remaining are worked out here from those
documents (`pdc_transfer.position_of`), so the report is right even where the stored PDC Status
on the cheque has not been refreshed yet.
"""

import frappe
from frappe import _
from frappe.utils import add_days, cint, date_diff, flt, getdate, nowdate

from sf_trading.pdc_transfer import REJECTION_FIELD, position_of, return_rows, transfer_rows

# days before the posting date that the PDC reminder notification fires
REMINDER_LEAD_DAYS = 3


def execute(filters=None):
    filters = frappe._dict(filters or {})
    return get_columns(), get_data(filters)


def get_cheque_modes():
    """Modes of Payment whose ZATCA payment means code == '20' (cheque)."""
    modes = frappe.get_all(
        "Mode of Payment",
        fields=["name", "custom_zatca_payment_means_code"],
    )
    return [m.name for m in modes if (m.custom_zatca_payment_means_code or "").strip() == "20"]


def get_data(filters):
    cheque_modes = get_cheque_modes()
    if not cheque_modes:
        return []

    conds = [
        ["Payment Entry", "mode_of_payment", "in", cheque_modes],
    ]
    if cint(filters.get("include_cancelled")):
        conds.append(["Payment Entry", "docstatus", "in", [0, 1, 2]])
    else:
        conds.append(["Payment Entry", "docstatus", "<", 2])

    for key in ("company", "payment_type", "party_type", "party", "mode_of_payment"):
        if filters.get(key):
            conds.append(["Payment Entry", key, "=", filters.get(key)])

    # The transfer that banks a cheque carries the cheque's own mode of payment, so it matches
    # the cheque-mode filter above and would be listed as a second cheque -- doubling the
    # outstanding PDC figure. This report is about cheques received and paid; the transfers are
    # shown against them in the Internal Transfer column. Asking for them by name still works.
    if filters.get("payment_type") != "Internal Transfer":
        conds.append(["Payment Entry", "payment_type", "!=", "Internal Transfer"])

    if filters.get("from_date"):
        conds.append(["Payment Entry", "reference_date", ">=", getdate(filters.from_date)])
    if filters.get("to_date"):
        conds.append(["Payment Entry", "reference_date", "<=", getdate(filters.to_date)])

    # Same guard pdc_transfer.transfers_for uses: on a bench that has pulled this code but not
    # migrated yet, the column does not exist -- asking for it would break the whole report
    # rather than simply not knowing about Rejected yet.
    has_rejection_field = frappe.db.has_column("Payment Entry", REJECTION_FIELD)

    # applied per row below, from the cheque's own transfers and returns; "Rejected" is the
    # name the report used before returns were posted, and asks for both kinds of return
    status = filters.get("status")
    wanted = {"Rejected": {"Returned", "Partly Returned"}}.get(status, {status} if status else None)

    fields = [
        "name", "payment_type", "posting_date", "reference_date", "reference_no",
        "party_type", "party", "party_name", "mode_of_payment", "paid_amount",
        "received_amount", "paid_from", "paid_to", "clearance_date", "docstatus",
        "company", "paid_from_account_currency", "paid_to_account_currency",
    ]
    if has_rejection_field:
        fields.append(REJECTION_FIELD)

    rows = frappe.get_all(
        "Payment Entry",
        filters=conds,
        fields=fields,
        order_by="reference_date asc, name asc",
    )

    names = [r.name for r in rows]
    transfers, returns = transfer_rows(names), return_rows(names)
    transfer_filter = filters.get("transfer_status")

    today = getdate(nowdate())
    out = []
    for r in rows:
        is_receive = r.payment_type == "Receive"
        amount = flt(r.received_amount) if is_receive else flt(r.paid_amount)
        currency = r.paid_to_account_currency if is_receive else r.paid_from_account_currency
        bank = r.paid_to if is_receive else r.paid_from
        cheque_date = getdate(r.reference_date) if r.reference_date else None
        posting_date = getdate(r.posting_date) if r.posting_date else None
        # The cheque date leads: it is the day the money is expected, and it is what the
        # reminder notification counts back from. The posting date only stands in for a row
        # that carries no reference date at all.
        anchor = cheque_date or posting_date
        days = date_diff(cheque_date, today) if cheque_date else None
        reminder_date = add_days(anchor, -REMINDER_LEAD_DAYS) if anchor else None
        days_to_anchor = date_diff(anchor, today) if anchor else None
        days_to_posting = date_diff(posting_date, today) if posting_date else None

        cheque_transfers = transfers.get(r.name, [])
        pos = position_of(r, transfers=cheque_transfers, returns=returns.get(r.name, []))
        submitted = [t for t in cheque_transfers if cint(t.docstatus) == 1]
        transfer = (submitted or cheque_transfers or [None])[-1]
        if any(cint(t.docstatus) == 0 for t in cheque_transfers):
            transfer_state = "Draft Transfer"
        elif not submitted:
            transfer_state = "Not Transferred"
        elif pos.remaining > 0.0005 and pos.status == "Partly Cleared":
            transfer_state = "Partly Transferred"
        else:
            transfer_state = "Transferred"
        if transfer_filter and transfer_filter != transfer_state:
            continue

        rejection_date = r.get(REJECTION_FIELD) if has_rejection_field else None
        if r.docstatus == 2:
            state = "Cancelled"
        elif r.docstatus == 0:
            state = "Draft"
        elif r.payment_type == "Internal Transfer":
            state = "Transfer"
        else:
            state = pos.status
        if wanted and state not in wanted:
            continue
        settled = r.docstatus == 1
        out.append({
            "payment_entry": r.name,
            "payment_type": r.payment_type,
            "posting_date": posting_date,
            "cheque_date": cheque_date,
            "reminder_date": reminder_date,
            "reference_no": r.reference_no,
            "days_to_cheque_date": days,
            "days_to_posting_date": days_to_posting,
            "days_to_reminder_anchor": days_to_anchor,
            "status": state,
            "internal_transfer": transfer.name if transfer else None,
            "transfers": len(cheque_transfers),
            "transfer_status": transfer_state,
            "transfer_date": getdate(transfer.posting_date) if transfer and transfer.posting_date else None,
            "transfer_account": transfer.paid_to if transfer else None,
            "party_type": r.party_type,
            "party": r.party,
            "party_name": r.party_name,
            "mode_of_payment": r.mode_of_payment,
            "amount": amount,
            "cleared_amount": pos.cleared if settled else 0,
            "returned_amount": pos.returned if settled else 0,
            "remaining_amount": pos.remaining if settled else 0,
            "return_journal": (returns.get(r.name) or [frappe._dict(name=None)])[-1].name,
            "currency": currency,
            "bank_account": bank,
            "clearance_date": r.clearance_date,
            "rejection_date": rejection_date,
            "company": r.company,
        })
    return out


def get_columns():
    return [
        {"label": _("Payment Entry"), "fieldname": "payment_entry", "fieldtype": "Link", "options": "Payment Entry", "width": 165},
        {"label": _("Type"), "fieldname": "payment_type", "fieldtype": "Data", "width": 80},
        {"label": _("Posting Date"), "fieldname": "posting_date", "fieldtype": "Date", "width": 105},
        {"label": _("Reminder Date (Cheque − 3)"), "fieldname": "reminder_date", "fieldtype": "Date", "width": 150},
        {"label": _("Days to Posting"), "fieldname": "days_to_posting_date", "fieldtype": "Int", "width": 105},
        {"label": _("Cheque Date"), "fieldname": "cheque_date", "fieldtype": "Date", "width": 100},
        {"label": _("Cheque / Ref No"), "fieldname": "reference_no", "fieldtype": "Data", "width": 170},
        {"label": _("Days to Cheque Date"), "fieldname": "days_to_cheque_date", "fieldtype": "Int", "width": 125},
        {"label": _("Status"), "fieldname": "status", "fieldtype": "Data", "width": 115},
        {"label": _("Transfer Status"), "fieldname": "transfer_status", "fieldtype": "Data", "width": 125},
        {"label": _("Internal Transfer"), "fieldname": "internal_transfer", "fieldtype": "Link", "options": "Payment Entry", "width": 165},
        {"label": _("Transfers"), "fieldname": "transfers", "fieldtype": "Int", "width": 80},
        {"label": _("Transferred On"), "fieldname": "transfer_date", "fieldtype": "Date", "width": 110},
        {"label": _("Transferred To"), "fieldname": "transfer_account", "fieldtype": "Link", "options": "Account", "width": 170},
        {"label": _("Party Type"), "fieldname": "party_type", "fieldtype": "Data", "width": 90},
        {"label": _("Party"), "fieldname": "party", "fieldtype": "Dynamic Link", "options": "party_type", "width": 150},
        {"label": _("Party Name"), "fieldname": "party_name", "fieldtype": "Data", "width": 180},
        {"label": _("Mode of Payment"), "fieldname": "mode_of_payment", "fieldtype": "Link", "options": "Mode of Payment", "width": 120},
        {"label": _("Amount"), "fieldname": "amount", "fieldtype": "Currency", "options": "currency", "width": 120},
        {"label": _("Cleared"), "fieldname": "cleared_amount", "fieldtype": "Currency", "options": "currency", "width": 115},
        {"label": _("Returned"), "fieldname": "returned_amount", "fieldtype": "Currency", "options": "currency", "width": 115},
        {"label": _("Remaining"), "fieldname": "remaining_amount", "fieldtype": "Currency", "options": "currency", "width": 115},
        {"label": _("Currency"), "fieldname": "currency", "fieldtype": "Link", "options": "Currency", "width": 70},
        {"label": _("Bank / Cash Account"), "fieldname": "bank_account", "fieldtype": "Link", "options": "Account", "width": 170},
        {"label": _("Cleared On"), "fieldname": "clearance_date", "fieldtype": "Date", "width": 100},
        {"label": _("Returned On"), "fieldname": "rejection_date", "fieldtype": "Date", "width": 100},
        {"label": _("Return Journal"), "fieldname": "return_journal", "fieldtype": "Link", "options": "Journal Entry", "width": 150},
        {"label": _("Company"), "fieldname": "company", "fieldtype": "Link", "options": "Company", "width": 150},
    ]
