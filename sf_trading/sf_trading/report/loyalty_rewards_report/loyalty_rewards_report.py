# sf_trading/sf_trading/report/loyalty_rewards_report/loyalty_rewards_report.py
"""Loyalty Rewards Report — reward journals with their Sales Invoice and customer.

Steel Force does not use ERPNext's Loyalty Program (there are no Loyalty Program or
Loyalty Point Entry records). A "loyalty reward" here is a **Journal Entry created from
the `Loyalty Reward Entry` template**, debiting the Loyalty Rewards expense account and
crediting petty cash / bank. The journal itself carries no party, so the customer can
only be known through the linked Sales Invoice — that is what the Custom Field
`Journal Entry-custom_loyalty_sales_invoice` provides (mandatory when the template is used).

Journals created before that field existed have no link. They are still listed, with the
Sales Invoice / customer columns blank, and the **Only Unlinked** filter isolates them so
an accountant can attach the invoice by hand (the field is allow_on_submit).

Two shapes, switched by the "Summarise by Customer" filter:
  * detail  — one row per reward journal, with invoice + customer + reward-vs-invoice %
  * summary — one row per customer: journals, invoices, reward total, invoice value, %

Reads are batched: one query for journals, one for their account rows, one for the linked
invoices. No per-row lookups, no SQL strings.
"""

import frappe
from frappe import _
from frappe.utils import flt, getdate

TEMPLATE = "Loyalty Reward Entry"
UNLINKED_LABEL = "(Not Linked)"
LINK_FIELD = "custom_loyalty_sales_invoice"

DOCSTATUS_LABEL = {0: _("Draft"), 1: _("Submitted"), 2: _("Cancelled")}
DOCSTATUS_FILTER = {
    "Submitted": [1],
    "Draft": [0],
    "Cancelled": [2],
    "Draft + Submitted": [0, 1],
    "All": [0, 1, 2],
}


def execute(filters=None):
    filters = frappe._dict(filters or {})

    if not link_field_available():
        frappe.msgprint(
            _(
                "The <b>Loyalty Sales Invoice</b> field is not installed on this site yet, "
                "so every reward journal is shown as unlinked. Run <code>bench migrate</code> "
                "to install it."
            ),
            title=_("Link field missing"),
            indicator="orange",
        )

    rows = get_data(filters)
    if filters.get("summarise_by_customer"):
        return get_summary_columns(), summarise(rows)
    return get_columns(), rows


# ── Fetch ─────────────────────────────────────────────────────────────────────────

def link_field_available():
    """Is the Loyalty Sales Invoice Custom Field installed on this site?

    The fixture ships with the app but only lands on `bench migrate`. Until then the
    column does not exist, so querying it would raise "Unknown column". The report stays
    usable in that state: every journal is simply reported as unlinked.
    """
    return frappe.get_meta("Journal Entry").has_field(LINK_FIELD)


def _journals(filters):
    has_link = link_field_available()

    conditions = {
        "from_template": filters.get("journal_template") or TEMPLATE,
        "docstatus": ["in", DOCSTATUS_FILTER.get(filters.get("status") or "Draft + Submitted", [0, 1])],
    }
    if filters.get("company"):
        conditions["company"] = filters.get("company")

    from_date = getdate(filters.get("from_date")) if filters.get("from_date") else None
    to_date = getdate(filters.get("to_date")) if filters.get("to_date") else None
    if from_date and to_date:
        conditions["posting_date"] = ["between", [from_date, to_date]]
    elif from_date:
        conditions["posting_date"] = [">=", from_date]
    elif to_date:
        conditions["posting_date"] = ["<=", to_date]

    if has_link:
        if filters.get("sales_invoice"):
            conditions[LINK_FIELD] = filters.get("sales_invoice")
        elif filters.get("only_unlinked"):
            conditions[LINK_FIELD] = ["in", ["", None]]
    elif filters.get("sales_invoice"):
        # nothing can match an invoice while the link field is not installed
        return []

    fields = [
        "name",
        "posting_date",
        "docstatus",
        "workflow_state",
        "total_debit",
        "user_remark",
        "cheque_no",
        "epromise_vr_no",
        "company",
    ]
    if has_link:
        fields.append(LINK_FIELD)

    journals = frappe.get_all(
        "Journal Entry",
        filters=conditions,
        fields=fields,
        order_by="posting_date desc, name desc",
    )

    if not has_link:
        for journal in journals:
            journal[LINK_FIELD] = None

    return journals


def _account_rows(journal_names):
    """Debit / credit split + cost centre for every journal, in one query."""
    detail = {}
    if not journal_names:
        return detail

    for row in frappe.get_all(
        "Journal Entry Account",
        filters={"parent": ["in", journal_names]},
        fields=["parent", "account", "debit", "credit", "cost_center"],
        order_by="idx asc",
    ):
        bucket = detail.setdefault(
            row.parent,
            {"reward": 0.0, "reward_accounts": [], "funding_accounts": [], "cost_center": None},
        )
        if flt(row.debit):
            bucket["reward"] += flt(row.debit)
            if row.account not in bucket["reward_accounts"]:
                bucket["reward_accounts"].append(row.account)
        if flt(row.credit) and row.account not in bucket["funding_accounts"]:
            bucket["funding_accounts"].append(row.account)
        if not bucket["cost_center"] and row.cost_center:
            bucket["cost_center"] = row.cost_center

    return detail


def _invoices(invoice_names):
    """Sales Invoice header data for the linked invoices, in one query."""
    if not invoice_names:
        return {}

    rows = frappe.get_all(
        "Sales Invoice",
        filters={"name": ["in", list(invoice_names)]},
        fields=[
            "name",
            "customer",
            "customer_name",
            "posting_date",
            "grand_total",
            "base_grand_total",
            "outstanding_amount",
            "branch",
            "status",
            "currency",
        ],
    )
    return {row.name: row for row in rows}


# ── Build ─────────────────────────────────────────────────────────────────────────

def get_data(filters):
    journals = _journals(filters)
    if not journals:
        return []

    accounts = _account_rows([j.name for j in journals])
    invoices = _invoices({j.custom_loyalty_sales_invoice for j in journals if j.custom_loyalty_sales_invoice})

    min_amount = flt(filters.get("min_amount"))
    wanted_customer = filters.get("customer")
    wanted_cost_center = filters.get("cost_center")

    data = []
    for je in journals:
        detail = accounts.get(je.name) or {}
        reward = flt(detail.get("reward") or je.total_debit)
        if min_amount and reward < min_amount:
            continue

        cost_center = detail.get("cost_center")
        if wanted_cost_center and cost_center != wanted_cost_center:
            continue

        invoice = invoices.get(je.custom_loyalty_sales_invoice) if je.custom_loyalty_sales_invoice else None
        if wanted_customer and (not invoice or invoice.customer != wanted_customer):
            continue

        invoice_total = flt(invoice.base_grand_total) if invoice else 0.0
        data.append(
            {
                "journal_entry": je.name,
                "posting_date": je.posting_date,
                "status": DOCSTATUS_LABEL.get(je.docstatus, ""),
                "workflow_state": je.workflow_state,
                "reward_amount": reward,
                "reward_account": ", ".join(detail.get("reward_accounts") or []),
                "funding_account": ", ".join(detail.get("funding_accounts") or []),
                "cost_center": cost_center,
                "sales_invoice": je.custom_loyalty_sales_invoice,
                "invoice_date": invoice.posting_date if invoice else None,
                "invoice_total": invoice_total,
                "invoice_outstanding": flt(invoice.outstanding_amount) if invoice else 0.0,
                "invoice_status": invoice.status if invoice else None,
                "customer": invoice.customer if invoice else None,
                "customer_name": (invoice.customer_name if invoice else None) or (
                    None if je.custom_loyalty_sales_invoice else UNLINKED_LABEL
                ),
                "branch": invoice.branch if invoice else None,
                "reward_pct": flt(reward / invoice_total * 100, 2) if invoice_total else 0.0,
                "cheque_no": je.cheque_no,
                "epromise_vr_no": je.epromise_vr_no,
                "remark": (je.user_remark or "").strip(),
            }
        )

    return data


def summarise(rows):
    """Customer-wise roll-up; unlinked journals collect under "(Not Linked)"."""
    buckets = {}
    for row in rows:
        key = row.get("customer") or UNLINKED_LABEL
        bucket = buckets.setdefault(
            key,
            {
                "customer": row.get("customer"),
                "customer_name": row.get("customer_name") or key,
                "journals": 0,
                "invoices": set(),
                "reward_amount": 0.0,
                "invoice_total": 0.0,
            },
        )
        bucket["journals"] += 1
        bucket["reward_amount"] += flt(row["reward_amount"])
        if row.get("sales_invoice") and row["sales_invoice"] not in bucket["invoices"]:
            bucket["invoices"].add(row["sales_invoice"])
            # count each invoice's value once, however many journals reference it
            bucket["invoice_total"] += flt(row["invoice_total"])

    summary = []
    for bucket in buckets.values():
        invoice_total = flt(bucket["invoice_total"])
        summary.append(
            {
                "customer": bucket["customer"],
                "customer_name": bucket["customer_name"],
                "journals": bucket["journals"],
                "invoices": len(bucket["invoices"]),
                "reward_amount": flt(bucket["reward_amount"], 3),
                "invoice_total": invoice_total,
                "reward_pct": flt(bucket["reward_amount"] / invoice_total * 100, 2) if invoice_total else 0.0,
            }
        )

    summary.sort(key=lambda r: -r["reward_amount"])
    return summary


# ── Columns ───────────────────────────────────────────────────────────────────────

def get_columns():
    return [
        {"fieldname": "journal_entry", "label": _("Journal Entry"), "fieldtype": "Link",
         "options": "Journal Entry", "width": 160},
        {"fieldname": "posting_date", "label": _("Date"), "fieldtype": "Date", "width": 95},
        {"fieldname": "reward_amount", "label": _("Reward (Debit)"), "fieldtype": "Currency", "width": 130},
        {"fieldname": "sales_invoice", "label": _("Sales Invoice"), "fieldtype": "Link",
         "options": "Sales Invoice", "width": 170},
        {"fieldname": "customer", "label": _("Customer"), "fieldtype": "Link",
         "options": "Customer", "width": 130},
        {"fieldname": "customer_name", "label": _("Customer Name"), "fieldtype": "Data", "width": 210},
        {"fieldname": "invoice_total", "label": _("Invoice Amount"), "fieldtype": "Currency", "width": 130},
        {"fieldname": "reward_pct", "label": _("Reward % of Invoice"), "fieldtype": "Percent", "width": 130},
        {"fieldname": "invoice_outstanding", "label": _("Invoice Outstanding"), "fieldtype": "Currency",
         "width": 140},
        {"fieldname": "invoice_date", "label": _("Invoice Date"), "fieldtype": "Date", "width": 95},
        {"fieldname": "invoice_status", "label": _("Invoice Status"), "fieldtype": "Data", "width": 110},
        {"fieldname": "status", "label": _("JE Status"), "fieldtype": "Data", "width": 90},
        {"fieldname": "workflow_state", "label": _("Workflow State"), "fieldtype": "Data", "width": 120},
        {"fieldname": "reward_account", "label": _("Reward Account"), "fieldtype": "Data", "width": 220},
        {"fieldname": "funding_account", "label": _("Paid From"), "fieldtype": "Data", "width": 220},
        {"fieldname": "cost_center", "label": _("Cost Center"), "fieldtype": "Link",
         "options": "Cost Center", "width": 130},
        {"fieldname": "branch", "label": _("Branch"), "fieldtype": "Link", "options": "Branch", "width": 90},
        {"fieldname": "cheque_no", "label": _("Ref / Cheque No"), "fieldtype": "Data", "width": 110},
        {"fieldname": "epromise_vr_no", "label": _("ePromise VR"), "fieldtype": "Data", "width": 110},
        {"fieldname": "remark", "label": _("Remark"), "fieldtype": "Small Text", "width": 260},
    ]


def get_summary_columns():
    return [
        {"fieldname": "customer", "label": _("Customer"), "fieldtype": "Link",
         "options": "Customer", "width": 140},
        {"fieldname": "customer_name", "label": _("Customer Name"), "fieldtype": "Data", "width": 240},
        {"fieldname": "journals", "label": _("Reward Journals"), "fieldtype": "Int", "width": 130},
        {"fieldname": "invoices", "label": _("Invoices"), "fieldtype": "Int", "width": 100},
        {"fieldname": "reward_amount", "label": _("Total Reward"), "fieldtype": "Currency", "width": 140},
        {"fieldname": "invoice_total", "label": _("Total Invoice Amount"), "fieldtype": "Currency",
         "width": 160},
        {"fieldname": "reward_pct", "label": _("Reward % of Invoices"), "fieldtype": "Percent", "width": 150},
    ]
