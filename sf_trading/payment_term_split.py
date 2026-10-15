# sf_trading/payment_term_split.py
"""Name the Payment Term a Payment Advice row settles, the way a Payment Entry would have.

ERPNext lets a Payment Terms Template ask for "Allocate Payment Based On Payment Terms". An invoice (or
order) raised under such a template is paid term by term: every Payment Entry reference to it must name the
Payment Schedule row it settles, because `PaymentEntry.update_payment_schedule` writes the paid amount onto
THAT row and the Payment Ledger carries the term, so each instalment has its own outstanding. A reference with
no term cannot say which instalment it pays, so core refuses it:

    "<invoice> has Payment Term based allocation enabled. Select a Payment Term for Row #n ..."

A Payment Entry made by hand fills the term itself. An advice row picked by hand did not (get_reference_details
returned none), the column is read-only, and pe_reference_row then correctly omits a blank term, so the advice
could be approved and never paid. Two helpers close that:

* `single_open_term` -- what the advice row shows the moment the document is picked (only when exactly one
  instalment is still open; with several the row stands for the whole document and is split at payment time);
* `split_by_payment_term` -- applied to every Payment Entry reference built from an advice: a reference with no
  term, to a term-allocated document, becomes one reference per open instalment, oldest due date first, each
  capped at that instalment's outstanding (the last one takes any remainder, so core still reports an
  over-allocation as an over-allocation instead of this code hiding it).

Anything else passes through untouched: a reference that already names a term, a document under a template
without term-based allocation, a document with no open instalment, a credit / debit note (negative allocation).
"""

import frappe
from frappe.utils import cint, flt

#: the doctypes erpnext's term_based_allocation_enabled_for_reference looks at
TERM_DOCTYPES = ("Sales Invoice", "Sales Order", "Purchase Order", "Purchase Invoice")

TOLERANCE = 0.0005


def open_terms(doctype, name) -> list:
    """[{payment_term, outstanding, due_date}] still unpaid on a term-allocated document, oldest first.

    Empty when the document is not term-allocated (no template, or a template without the switch) or when
    every instalment is settled.
    """
    if doctype not in TERM_DOCTYPES or not name:
        return []
    template = frappe.db.get_value(doctype, name, "payment_terms_template")
    if not template:
        return []
    if not cint(frappe.db.get_value("Payment Terms Template", template, "allocate_payment_based_on_payment_terms")):
        return []
    rows = frappe.get_all(
        "Payment Schedule",
        filters={"parent": name, "parenttype": doctype},
        fields=["payment_term", "outstanding", "due_date", "idx"],
        order_by="due_date asc, idx asc",
    )
    return [r for r in rows if r.get("payment_term") and flt(r.get("outstanding")) > TOLERANCE]


def single_open_term(doctype, name):
    """The one open Payment Term of a term-allocated document, or None (none open, or more than one)."""
    terms = open_terms(doctype, name)
    return terms[0]["payment_term"] if len(terms) == 1 else None


def split_by_payment_term(reference) -> list:
    """The Payment Entry reference rows for one advice row: [reference] unchanged, or one per open term."""
    if reference.get("payment_term"):
        return [reference]
    allocated = flt(reference.get("allocated_amount"))
    if allocated <= TOLERANCE:
        return [reference]

    terms = open_terms(reference.get("reference_doctype"), reference.get("reference_name"))
    if not terms:
        return [reference]

    parts, left = [], allocated
    for i, term in enumerate(terms):
        last = i == len(terms) - 1
        take = left if last else min(left, flt(term.get("outstanding")))
        if take <= TOLERANCE:
            break
        part = dict(reference)
        part["payment_term"] = term["payment_term"]
        part["allocated_amount"] = flt(take, 3)
        parts.append(part)
        left = flt(left - take, 6)
        if left <= TOLERANCE:
            break
    return parts or [reference]
