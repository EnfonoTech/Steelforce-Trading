# Copyright (c) 2026, Enfono Technologies and contributors
# For license information, please see license.txt

"""Show the Payment Advice a voucher was paid through, in its Connections.

An advice is raised FROM a voucher — an invoice, an order, a journal — but the link only
existed in one direction: you could open an advice and see its references, while standing on
the invoice there was nothing to say an advice had ever been raised for it, let alone which
one or what state it was in. Anyone chasing "has this been sent for payment?" had to search
the Payment Advice list.

The reference lives on the `Payment Advice Reference` child table as a Dynamic Link pair
(`reference_doctype` + `reference_record`), which is the same shape ERPNext uses for Payment
Entry, so it is declared the same way: name the field in `non_standard_fieldnames` and let
Frappe resolve the child table itself.

The Payment Entry raised from an advice is connected both ways (client, 2026-10-11): the entry
lists its advice in its Connections, from its own `custom_payment_advice` field -- so a draft
shows it too -- and the advice lists the entries that name it, which can be several when it is
paid in parts.

Wired through `override_doctype_dashboards` in hooks.py for every doctype an advice may
reference. Each handler receives the dashboard the previous owner built and returns it
extended, so ERPNext's own connections are kept rather than replaced.
"""

import frappe

PAYMENT_ADVICE = "Payment Advice"
PAYMENT_ENTRY = "Payment Entry"
REFERENCE_FIELD = "reference_record"
# Payment Entry.custom_payment_advice names the advice a payment was raised from; the advice
# stamps Payment Advice.payment_entry on the entry's submit (api/payment_advice_hooks.py)
ADVICE_ON_ENTRY = "custom_payment_advice"
ENTRY_ON_ADVICE = "payment_entry"

# where an advice belongs on the form: beside the other ways money moves
PAYMENT_GROUP_LABELS = ("Payment", "Payments")


def _list_in_payment_group(data, doctype):
    """Put `doctype` among the payment connections of the dashboard `data`."""
    transactions = data.setdefault("transactions", [])

    # already listed by someone else — nothing to do
    for group in transactions:
        if doctype in (group.get("items") or []):
            return

    for group in transactions:
        label = group.get("label")
        # labels arrive translated, so compare against the translation too
        if label in PAYMENT_GROUP_LABELS or label in [frappe._(l) for l in PAYMENT_GROUP_LABELS]:
            group.setdefault("items", []).append(doctype)
            return

    transactions.append({"label": frappe._("Payment"), "items": [doctype]})


def add_payment_advice(data):
    """Extend a dashboard with Payment Advice, leaving everything else intact."""
    data = frappe._dict(data or {})

    fieldnames = data.setdefault("non_standard_fieldnames", {})
    fieldnames[PAYMENT_ADVICE] = REFERENCE_FIELD

    _list_in_payment_group(data, PAYMENT_ADVICE)
    return data


def add_payment_advice_to_payment_entry(data):
    """A Payment Entry lists the Payment Advice it was raised from. The entry's own field is read
    first (a draft carries it already); the advice's stamp is the fallback."""
    data = frappe._dict(data or {})
    data.setdefault("fieldname", "payment_entry")
    data.setdefault("internal_links", {})[PAYMENT_ADVICE] = ADVICE_ON_ENTRY
    data.setdefault("non_standard_fieldnames", {})[PAYMENT_ADVICE] = ENTRY_ON_ADVICE
    _list_in_payment_group(data, PAYMENT_ADVICE)
    return data


def add_payment_entries_to_payment_advice(data):
    """A Payment Advice lists every Payment Entry that names it -- external, so a part-paid advice
    shows all of them, not just the one its own field remembers."""
    data = frappe._dict(data or {})
    data.setdefault("fieldname", ADVICE_ON_ENTRY)
    data.setdefault("non_standard_fieldnames", {})[PAYMENT_ENTRY] = ADVICE_ON_ENTRY
    _list_in_payment_group(data, PAYMENT_ENTRY)
    return data


# One entry point per doctype so hooks.py reads explicitly, and so a doctype can later
# diverge without disturbing the others.
def get_dashboard_data(data):
    return add_payment_advice(data)


def get_payment_entry_dashboard_data(data):
    return add_payment_advice_to_payment_entry(data)


def get_payment_advice_dashboard_data(data):
    return add_payment_entries_to_payment_advice(data)
