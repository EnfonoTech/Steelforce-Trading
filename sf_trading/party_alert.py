# sf_trading/party_alert.py
"""Say what would refuse a customer or supplier the moment it is picked on a voucher.

The gates that refuse a party (party_documents, supplier_validation, sales_order_governance,
credit_customer_approval) run on save or before_submit, so a clerk fills a whole Purchase Invoice and
an approver clicks Approve before anyone learns the supplier has no supporting document
(2026-10-13: Stc Bahrain on Purchase Invoice 20020000378). public/js/party_gate_alert.js calls
`get_party_alert` when the party is picked and when a draft is opened, and shows what comes back as a
red alert and banner.

Nothing here restates a rule. Each gate is run -- as the user in front of the form, against the
document as it stands -- and the text it refused with is handed back, so the alert and the refusal can
never disagree, and a rule added to a gate shows up in the alert with no change here. A gate is only
asked: nothing is written, and a gate that fails for any reason but a refusal is ignored rather than
shown, because a preview must never be what breaks the form.

`GATES` mirrors hooks.py doc_events (the gates registered on each voucher); tests/test_party_alert.py
fails when the two drift apart.
"""

from __future__ import annotations

import frappe
from frappe.utils import cstr

from sf_trading import party_documents as pd

#: the field that carries the party on each voucher a gate covers
PARTY_FIELD = {
	**{doctype: "supplier" for doctype in pd.SUPPLIER_SIDE},
	"Payment Entry": "party",
	**{doctype: "customer" for doctype in pd.CUSTOMER_SIDE},
}

_SUPPLIER_GATES = (
	"sf_trading.supplier_validation.validate_supplier_at_transaction",
	"sf_trading.party_documents.validate_party_at_transaction",
)
_CUSTOMER_GATES = (
	"sf_trading.sales_order_governance.validate_customer_contact_at_transaction",
	"sf_trading.sales_order_governance.validate_credit_customer_requirements_at_transaction",
	"sf_trading.party_documents.validate_party_at_transaction",
)

#: the party gates registered on each voucher, in hooks.py doc_events
GATES = {
	**{doctype: _SUPPLIER_GATES for doctype in pd.SUPPLIER_SIDE},
	"Sales Order": _CUSTOMER_GATES,
	# an invoice is also held back while its customer is not approved
	"Sales Invoice": _CUSTOMER_GATES + ("sf_trading.credit_customer_approval.validate_customer_approved_for_invoicing",),
}


def _voucher(doctype: str, values):
	"""The header of the voucher as the form holds it: scalars only (the gates read the party and a few
	flags, never a child table), and nothing the client marks as its own bookkeeping."""
	header = {
		key: value
		for key, value in (frappe.parse_json(values) or {}).items()
		if (not key.startswith("__") or key == "__islocal")
		and (value is None or isinstance(value, (str, int, float, bool)))
	}
	header["doctype"] = doctype
	return frappe.get_doc(header)


def _refusal(gate, voucher) -> str | None:
	"""Ask one gate. The text it refused with, or None when it let the voucher through."""
	messages = list(frappe.local.message_log)
	try:
		gate(voucher)
	except frappe.ValidationError as refusal:
		return cstr(refusal).strip() or None
	except Exception:
		frappe.logger("sf_trading").exception("party alert: %s failed", getattr(gate, "__name__", gate))
	finally:
		# a refusal also queues a popup for the response; this one was only a question
		frappe.local.message_log = messages
	return None


@frappe.whitelist()
def get_party_alert(doctype: str, doc) -> dict:
	"""What the voucher's customer or supplier would be refused for, by the gates that refuse it.

	`doc` is the header of the voucher as it stands on the form (a draft the user has not saved yet is
	fine). Returns the party and `problems`, one sentence per refusing gate, empty when nothing does.
	"""
	gates = GATES.get(doctype)
	if not gates:
		return {"party": None, "problems": []}
	frappe.has_permission(doctype, "read", throw=True)

	voucher = _voucher(doctype, doc)
	party = voucher.get(PARTY_FIELD[doctype])
	party_type = "Customer" if doctype in pd.CUSTOMER_SIDE else "Supplier"
	if not party or not frappe.db.exists(party_type, party):
		return {"party": None, "problems": []}
	# a payment entry only answers to a supplier it pays; its other parties are nobody's business here
	if doctype == "Payment Entry" and voucher.get("party_type") != "Supplier":
		return {"party": None, "problems": []}
	frappe.has_permission(party_type, "read", doc=party, throw=True)

	problems = []
	for path in gates:
		text = _refusal(frappe.get_attr(path), voucher)
		if text and text not in problems:
			problems.append(text)
	return {
		"party_type": party_type,
		"party": party,
		"party_name": frappe.db.get_value(party_type, party, party_type.lower() + "_name") or party,
		"problems": problems,
		# the banner offers to fix it only to someone who may
		"can_fix": bool(frappe.has_permission(party_type, "write", doc=party)),
	}
