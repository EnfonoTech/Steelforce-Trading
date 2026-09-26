# sf_trading/api/customer_quick_edit.py
"""Quick-edit provision on the Sales Invoice Customer field (client ask, 2026-09-26): fix exactly
the fields that gate billing for a customer -- CR/VAT (GS Issue 1), a phone number (GS Issue 20),
a second phone number for B2B (GS Issue 19's B2B rule), and an attachment (GS Issue 13's credit-
customer requirement, and -- 2026-09-26, second pass -- customer_override.validate's own
VAT-registration-needs-a-document rule) -- without leaving the draft invoice.

The attachment field IS editable here (2026-09-26, second pass): a bare Attach control in a
frappe.ui.Dialog uploads to the File doctype unassociated (no attached_to_doctype/name) since
there is no live frm to bind it to, so save_quick_edit_data links it to this Customer explicitly,
before the CR/VAT save -- both launch points (Customer master, Sales Invoice Customer field) only
ever open this dialog against an ALREADY-SAVED Customer, so there is no "parent doesn't exist yet"
problem to work around.

Deliberately reuses the SAME check functions the billing gates themselves call
(party_completeness.missing_company_fields, sales_order_governance.missing_credit_customer_requirements /
missing_b2b_phone_requirements, party_contact_cache.party_phone_numbers) -- this dialog and the gates
it is fixing can never disagree about what "complete" means, because they read the same functions.
"""

from __future__ import annotations

import frappe
from frappe import _

from sf_trading.party_completeness import CR_FIELD, VAT_FIELD, is_b2b_customer, missing_company_fields
from sf_trading.party_contact_cache import party_phone_numbers
from sf_trading.sales_order_governance import (
	missing_b2b_phone_requirements,
	missing_credit_customer_requirements,
)


def _primary_contact(customer: str) -> str | None:
	"""The customer's own Contact link if set, else the first linked Contact found, else None."""
	primary = frappe.db.get_value("Customer", customer, "customer_primary_contact")
	if primary:
		return primary
	return frappe.db.get_value(
		"Dynamic Link",
		{"parenttype": "Contact", "link_doctype": "Customer", "link_name": customer},
		"parent",
	)


def _primary_address(customer: str) -> str | None:
	"""The customer's own Address link if set, else the first linked Address found, else None."""
	primary = frappe.db.get_value("Customer", customer, "customer_primary_address")
	if primary:
		return primary
	return frappe.db.get_value(
		"Dynamic Link",
		{"parenttype": "Address", "link_doctype": "Customer", "link_name": customer},
		"parent",
	)


@frappe.whitelist()
def get_quick_edit_data(customer: str) -> dict:
	"""Everything the dialog needs: current values, which of the four gates is failing right now,
	and two independent field-visibility flags (is_company, is_b2b -- see below) so the form shows
	exactly the inputs needed to fix whatever the banner names, no more and no less."""
	frappe.has_permission("Customer", "read", doc=customer, throw=True)

	doc = frappe.get_cached_doc("Customer", customer)
	# Two DIFFERENT flags, kept deliberately independent even though missing_company_fields (GS
	# Issue 1) and missing_b2b_phone_requirements now share ONE gate (is_b2b_customer, VAT-presence,
	# 2026-09-26 second correction). is_company here is field-VISIBILITY, not the blocking rule: a
	# Company-type customer with no VAT yet is not blocked any more, but the dialog still shows the
	# CR/VAT inputs for it (is_company stays customer_type-based) so staff can fill VAT in and
	# thereby promote the customer to B2B -- hiding the fields would make that impossible. is_b2b
	# mirrors both billing gates' own shared criterion for the 2nd-phone requirement.
	is_company = doc.customer_type == "Company"
	is_b2b = is_b2b_customer(doc)

	contact = _primary_contact(customer)
	address = _primary_address(customer)
	phones = party_phone_numbers("Customer", customer)

	return {
		"customer_name": doc.customer_name,
		"customer_type": doc.customer_type,
		"is_company": is_company,
		"is_b2b": is_b2b,
		CR_FIELD: doc.get(CR_FIELD),
		VAT_FIELD: doc.get(VAT_FIELD),
		"contact": contact,
		"phone_1": phones[0] if len(phones) > 0 else "",
		"phone_2": phones[1] if len(phones) > 1 else "",
		"address": address,
		"address_line1": frappe.db.get_value("Address", address, "address_line1") if address else "",
		"address_city": frappe.db.get_value("Address", address, "city") if address else "",
		"has_attachment": bool(
			frappe.db.exists("File", {"attached_to_doctype": "Customer", "attached_to_name": customer})
		),
		"missing": {
			"company_fields": missing_company_fields(doc),
			"credit_customer": missing_credit_customer_requirements(customer),
			"b2b_phone": missing_b2b_phone_requirements(customer),
			"any_phone": [] if phones else [_("at least one contact number")],
		},
	}


@frappe.whitelist()
def save_quick_edit_data(customer: str, values) -> dict:
	"""Write back exactly the fields the dialog exposed.

	Phone and address are saved independently of Commercial Registration / VAT -- a partial CR/VAT
	fix that still fails party_completeness.missing_company_fields must not also discard a phone
	number the user fixed in the same dialog. Frappe wraps one request in one transaction that
	rolls back whole on an uncaught exception, so the CR/VAT save is caught deliberately, not left
	to propagate.
	"""
	frappe.has_permission("Customer", "write", doc=customer, throw=True)

	if isinstance(values, str):
		values = frappe.parse_json(values)

	result = {"saved": True, "warnings": []}

	# Linked BEFORE the CR/VAT save below, deliberately -- customer_override.validate (Customer's
	# own validate hook) refuses to save a VAT Registration Number with no attachment on file, so
	# an attachment uploaded in THIS SAME call must already be linked by the time doc.save() below
	# runs its validate, or the save rolls back the VAT/CR write for want of a document that in
	# fact was just supplied.
	attachment = (values.get("attachment") or "").strip()
	if attachment:
		_link_attachment(customer, attachment)

	phone_1 = (values.get("phone_1") or "").strip()
	phone_2 = (values.get("phone_2") or "").strip()
	if phone_1 or phone_2:
		_save_contact_phones(customer, phone_1, phone_2)

	address_line1 = (values.get("address_line1") or "").strip()
	address_city = (values.get("address_city") or "").strip()
	if address_line1 or address_city:
		_save_address(customer, address_line1, address_city)

	cr = (values.get(CR_FIELD) or "").strip()
	vat = (values.get(VAT_FIELD) or "").strip()
	if cr or vat:
		doc = frappe.get_doc("Customer", customer)
		if cr:
			doc.set(CR_FIELD, cr)
		if vat:
			doc.set(VAT_FIELD, vat)
		try:
			doc.save()
		except frappe.ValidationError as e:
			result["warnings"].append(str(e))
			# frappe.throw() queues its text into frappe.local.message_log the moment it's
			# called, for the "_server_messages" auto-popup every frappe.call() response
			# carries -- catching the exception here does NOT retroactively un-queue it, so
			# without this the client shows Frappe's own raw "Message" popup for the exact
			# text this except clause just downgraded to a warning (confirmed live, 2026-09-26:
			# customer_override.validate's VAT-attachment throw leaking through as a bare error
			# dialog even though save_quick_edit_data returned saved=true with the same text
			# in "warnings").
			frappe.clear_messages()

	return result


def _save_contact_phones(customer: str, phone_1: str, phone_2: str) -> None:
	"""Find-or-create the customer's primary Contact, then replace its Contact Phone rows with
	whatever the dialog was given (1 or 2 numbers). Never touches Address.phone, the OTHER source
	party_phone_numbers reads, so it does not fight a value already correct there."""
	contact_name = _primary_contact(customer)
	if contact_name:
		contact = frappe.get_doc("Contact", contact_name)
	else:
		contact = frappe.new_doc("Contact")
		contact.first_name = frappe.db.get_value("Customer", customer, "customer_name") or customer
		contact.append("links", {"link_doctype": "Customer", "link_name": customer})

	numbers = [n for n in (phone_1, phone_2) if n]
	contact.set("phone_nos", [])
	for i, number in enumerate(numbers):
		contact.append("phone_nos", {"phone": number, "is_primary_phone": 1 if i == 0 else 0})
	contact.save(ignore_permissions=True)

	if not frappe.db.get_value("Customer", customer, "customer_primary_contact"):
		frappe.db.set_value("Customer", customer, "customer_primary_contact", contact.name)


def _save_address(customer: str, address_line1: str, city: str) -> None:
	"""Find-or-create the customer's primary Address, then update its address line/city."""
	address_name = _primary_address(customer)
	if address_name:
		address = frappe.get_doc("Address", address_name)
	else:
		address = frappe.new_doc("Address")
		address.address_title = frappe.db.get_value("Customer", customer, "customer_name") or customer
		address.address_type = "Billing"
		address.country = "Bahrain"
		address.append("links", {"link_doctype": "Customer", "link_name": customer})

	if address_line1:
		address.address_line1 = address_line1
	if city:
		address.city = city
	address.save(ignore_permissions=True)

	if not frappe.db.get_value("Customer", customer, "customer_primary_address"):
		frappe.db.set_value("Customer", customer, "customer_primary_address", address.name)


def _link_attachment(customer: str, file_url: str) -> None:
	"""Attach an already-uploaded (but unassociated) File to this Customer.

	The dialog's Attach control has no live frm to bind to, so Frappe uploads the file straight to
	the File doctype with attached_to_doctype/attached_to_name left blank -- this is what turns
	that floating upload into a real Customer attachment, the same shape
	missing_credit_customer_requirements and customer_override.validate both look for
	(frappe.get_all("File", filters={"attached_to_doctype": ..., "attached_to_name": ...})).

	Silently a no-op if the file_url doesn't resolve to a File row -- the field is optional and a
	stale/malformed value here must not block the phone/address/CR/VAT fixes in the same call.
	"""
	file_name = frappe.db.get_value("File", {"file_url": file_url}, "name")
	if not file_name:
		return
	frappe.db.set_value(
		"File",
		file_name,
		{"attached_to_doctype": "Customer", "attached_to_name": customer},
	)
