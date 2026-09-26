# sf_trading/party_completeness.py
"""Mandatory-field rule for Customer, B2B (Company-type) only.

Scoped to Customer only, deliberately. The client's own words split the rule by B2C/B2B, which are
Customer-side terms -- Supplier has no Individual/Company distinction at all (its own `supplier_type`
is a link to a Supplier Type master such as "Distributor" or "Local", not a party-kind flag), and
Supplier does not yet carry CR/VAT custom fields the way Customer does. Extending this rule to
Supplier is a real, separate ask (the Excel tracker's "Masters" item names Supplier too) but needs
its own fields added first and its own confirmation of which fields apply -- out of scope for this
pass rather than guessed at here.

One rule, called from two places, so the client's own requirement -- "must apply to EXISTING
customers too, not just new ones" -- is actually met:

1. Customer ``validate`` (this module) checks the two fields that live directly on the master:
   Commercial Registration number and VAT Registration number, for a Company-type customer. These
   can be checked the moment the master itself is saved, with no ``is_new()`` guard, so editing an
   already-existing incomplete customer is blocked exactly like creating a new one.
2. Sales Invoice / Sales Order ``validate`` (sales_order_governance.py) additionally checks that the
   customer has a linked Contact carrying a phone number -- the B2C "mobile number" half of the
   rule, and the B2B "contact number" half. This CANNOT be checked inside Customer's own ``validate``
   on a first save: core creates the linked Contact/Address in a follow-up call, after the Customer's
   own insert has already committed, so a ``validate``/``after_insert`` hook on Customer never sees
   it (the account's own trap notes call this out: a Customer ``after_insert`` hook can never see
   the Address/Contact core is about to create). Checking at the *transaction* instead sidesteps the
   ordering problem entirely, and doubles as the "block billing on missing data, for existing records
   too" requirement (GS Issue 20).

Field-list note: the 09-22 client call gave B2B as CR + VAT + one contact number. A separately
relayed point (the account's own "W9") asked for a broader list on existing B2B customers -- CR +
email + two contact numbers + attachment -- but only for the *credit-customer* subset per the same
call's Issue 13. That broader list is deliberately NOT enforced by this module yet: it is still an
open question for the client. Only the narrower, undisputed rule below is enforced here.
"""

from __future__ import annotations

import frappe
from frappe import _
from frappe.utils import cstr

CR_FIELD = "custom_commercial_registration_number"
VAT_FIELD = "custom_vat_registration_number"


def is_b2b_customer(doc_or_customer) -> bool:
	"""B2B iff a VAT Registration Number is on file. customer_type is NOT consulted (client
	correction, 2026-09-26, after live UAT test: "Havelock One Interiors WLL" is customer_type
	"Company" with a blank VAT Registration Number and was still showing as B2B in the quick-edit
	dialog -- the client wants VAT presence to be the sole signal, not an OR with customer_type).

	This DOES narrow behaviour versus the prior widened version: a Company-type customer with no
	VAT filled in now reads as B2C here (missing_b2b_phone_requirements no longer demands a 2nd
	contact number for it, and the quick-edit dialog no longer shows the CR/VAT section for it)
	until VAT is filled in. missing_company_fields's own Company-only CR/VAT-mandatory rule just
	below (GS Issue 1) is a SEPARATE, untouched rule -- it still keys off customer_type=="Company"
	alone and still blocks that same customer's save until CR+VAT are both filled in, so the gap
	self-closes the moment VAT is entered there.

	Accepts either a customer name (str) or an already-loaded Customer doc/dict -- callers that
	already hold the doc (e.g. a validate hook) should pass it directly rather than pay for a
	second query."""
	if isinstance(doc_or_customer, str):
		vat = frappe.db.get_value("Customer", doc_or_customer, VAT_FIELD)
	else:
		vat = doc_or_customer.get(VAT_FIELD)

	return bool(cstr(vat).strip())


def missing_company_fields(doc) -> list[str]:
	"""For a Company-type Customer: which of CR / VAT is blank. Empty list = complete or not a Company."""
	if doc.get("customer_type") != "Company":
		return []

	missing = []
	if not cstr(doc.get(CR_FIELD)).strip():
		missing.append(_("Commercial Registration Number"))
	if not cstr(doc.get(VAT_FIELD)).strip():
		missing.append(_("VAT Registration Number"))
	return missing


def validate_company_fields(doc, _method=None):
	"""Customer validate: refuse to save a Company-type customer missing CR or VAT.

	Applies to an existing record being edited exactly as much as a new one -- there is no
	``is_new()`` guard -- which is what the client asked for ("must apply to EXISTING customers
	too... fill first, then entry passes").
	"""
	missing = missing_company_fields(doc)
	if not missing:
		return

	label = doc.name or doc.get("customer_name")
	frappe.throw(
		_("Customer %s is missing required field(s): %s") % (label, ", ".join(missing)),
		title=_("Mandatory Fields Missing"),
	)
