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

Two more pieces make the same rule hold for EVERY Payment Entry and for the documents it pays:

* `fill_reference_terms` (before_validate on Payment Entry): a reference typed or picked by hand on the Payment Entry form
  has no term either, and core refuses it in its own validate ("... has Payment Term based allocation enabled. Select a Payment
  Term for Row #n"). The term is named, and the row split per open instalment, BEFORE core looks. It has to be before_validate:
  a doc event of `validate` runs after the controller, i.e. after the refusal.
* `repair_term_less_schedule` (validate on Purchase / Sales Invoice and Order): a document drafted before its supplier had a default
  Payment Terms Template, then saved again after the default arrived, got the template set by core WITHOUT its schedule being
  rebuilt: template CASH (term-based) on a schedule row with no term (seen 2026-10-17: 3 purchase invoices, Batelco's among them).
  Core then demands a term the document does not have, so the invoice could never be paid. The schedule is rebuilt from the template.
  `patches/v0_1/name_payment_terms_on_term_less_schedules.py` names the terms on the documents already submitted that way.
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


# fields a copied Payment Entry Reference row must not carry over
_ROW_SKIP = {"name", "idx", "owner", "creation", "modified", "modified_by", "docstatus", "parent", "parentfield", "parenttype", "doctype"}


def _row_values(row) -> dict:
	as_dict = getattr(row, "as_dict", None)
	values = as_dict() if callable(as_dict) else dict(row)
	return {k: v for k, v in values.items() if k not in _ROW_SKIP and not str(k).startswith("__")}


def fill_reference_terms(doc, method=None):
	"""before_validate on Payment Entry: name the Payment Term of every reference to a term-allocated document that has none.

	Same rule as `split_by_payment_term`, applied to the entry's own reference rows: a row with no term becomes one row per open
	instalment, oldest first, each capped at the instalment's outstanding (the last takes any remainder). Nothing is touched, and
	no query is made beyond one template read per reference, when every row already names its term or is not term-allocated.
	"""
	rows = doc.get("references") or []
	if not rows:
		return

	plan, changed = [], False
	for row in rows:
		values = _row_values(row)
		reference = {key: values.get(key) for key in ("reference_doctype", "reference_name", "allocated_amount", "payment_term")}
		parts = split_by_payment_term(reference) if values.get("reference_name") else [reference]
		if len(parts) == 1 and parts[0] is reference:
			plan.append(values)
			continue
		changed = True
		for part in parts:
			copy = dict(values)
			copy["payment_term"] = part.get("payment_term")
			copy["allocated_amount"] = part.get("allocated_amount")
			plan.append(copy)

	if not changed:
		return
	doc.set("references", [])
	for values in plan:
		doc.append("references", values)


def repair_term_less_schedule(doc, method=None):
	"""validate on Purchase / Sales Invoice and Order: a term-allocated template must come with a schedule that names its terms.

	When the template is term-allocated but a schedule row has no term, the schedule is rebuilt from the template by core's own
	`set_payment_schedule` (amounts net of any advance, due dates from the terms). Left alone: returns, POS and opening entries,
	a schedule that already holds a payment, a document with no schedule yet (core builds it from the template).
	"""
	template = doc.get("payment_terms_template")
	if not template or doc.get("is_return") or doc.get("is_pos") or doc.get("is_opening") == "Yes":
		return
	rows = doc.get("payment_schedule") or []
	if not rows or all(r.get("payment_term") for r in rows):
		return
	if any(flt(r.get("paid_amount")) for r in rows):
		return
	if not cint(frappe.db.get_value("Payment Terms Template", template, "allocate_payment_based_on_payment_terms")):
		return

	doc.set("payment_schedule", [])
	doc.set_payment_schedule()
	set_due_date = getattr(doc, "set_due_date", None)
	if set_due_date:
		set_due_date()
