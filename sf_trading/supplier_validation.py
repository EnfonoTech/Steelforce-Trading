# sf_trading/supplier_validation.py
"""Supplier completeness: Tax ID (Company suppliers) and a document on the master; the same plus
phone and email before any buying document or payment.

The Supplier-side counterpart of the Customer gates (party_completeness, customer_override,
sales_order_governance). The client's call, field by field against Customer:

  * Customer's VAT Registration Number -> Supplier's own core `tax_id`: mandatory on the master
    for a supplier_type "Company" supplier, foreign ones included (their own country's tax
    number); not for an "Individual" (client). Unique whenever one is given. Import vs local is
    decided per document by currency, not on the master, so the master makes no such distinction.
  * Phone and email -> checked where Customer checks its phone: on the documents raised against
    the party (Purchase Order, Purchase Invoice, ...), not on the master, so a supplier can still
    be created and corrected first. The client, for now: Tax ID, phone and email.
  * Customer's CR number -> no Supplier equivalent, not enforced.
  * Customer's credit-limit / branch-access checks -> Supplier carries no credit limit, not
    applicable.
  * Customer's attachment rule -> at least one file attached to the Supplier, any file: the type
    is not checked (client, as on Customer). A file can only be attached once the supplier
    exists, so the first save is let through and every save after it needs one.

Applies to existing suppliers as much as new ones. A frozen or disabled supplier is exempt from
the master rule, as on Customer -- see party_completeness.only_status_fields_changed.
"""

from __future__ import annotations

import frappe
from frappe import _
from frappe.utils import cstr

from sf_trading.party_completeness import only_status_fields_changed
from sf_trading.party_contact_cache import party_email_addresses, party_phone_numbers


def validate(doc, _method=None):
	"""Supplier validate: Tax ID mandatory for a Company supplier and unique when given, and a
	file attached."""
	if only_status_fields_changed(doc):
		return

	doc.tax_id = cstr(doc.tax_id).strip() or None
	if not doc.tax_id and needs_tax_id(doc.supplier_type):
		frappe.throw(
			_("Tax ID is mandatory for Supplier {0} (Supplier Type: Company).").format(
				frappe.bold(doc.supplier_name or doc.name)
			),
			title=_("Mandatory Fields Missing"),
		)

	if doc.tax_id:
		validate_unique_tax_id(doc)
	validate_attachment(doc)


def needs_tax_id(supplier_type) -> bool:
	return supplier_type == "Company"


def has_attachment(supplier: str) -> bool:
	return bool(frappe.db.exists("File", {"attached_to_doctype": "Supplier", "attached_to_name": supplier}))


def validate_attachment(doc):
	# a file can only be attached once the supplier exists, so the first save cannot carry one
	if doc.is_new() or doc.flags.get("in_insert"):
		return

	if not has_attachment(doc.name):
		frappe.throw(
			_("Please attach a document to Supplier {0} (Attachments, in the sidebar) before saving.").format(
				frappe.bold(doc.supplier_name or doc.name)
			),
			title=_("Document Required"),
		)


def remind_attachment(doc, _method=None):
	"""Supplier after_insert: the first save cannot carry a file, so say right away that one is
	needed -- not only when the next save is refused."""
	# the Create Supplier dialog links its own upload straight after the insert
	if doc.flags.get("attachment_from_dialog"):
		return

	frappe.msgprint(
		_("Supplier created. Please attach a document now (Attachments, in the sidebar); it is required before the next save."),
		title=_("Document Required"),
		indicator="orange",
	)


def validate_unique_tax_id(doc):
	# set only by api.supplier.create_supplier_with_address after its manager-override check
	if doc.flags.get("allow_duplicate_tax_id"):
		return
	# only a new or changed Tax ID can create a duplicate; a pair that already exists (a manager
	# override, or one from before this rule) must not make both suppliers uneditable
	if not doc.is_new() and not doc.has_value_changed("tax_id"):
		return

	clash = frappe.db.get_value(
		"Supplier", {"tax_id": doc.tax_id, "name": ("!=", doc.name or "")}, "name"
	)
	if clash:
		frappe.throw(
			_("Tax ID {0} is already used by Supplier {1}.").format(
				frappe.bold(doc.tax_id), frappe.bold(clash)
			),
			title=_("Duplicate Tax ID"),
		)


def missing_supplier_details(supplier: str) -> list[str]:
	"""What the supplier still lacks before a document can be raised against it. Phone and email
	are read from the master and from its linked Contacts/Addresses, as for Customer -- the
	master's own fetch_from columns can sit blank while the real value lives on a Contact."""
	supplier_type, tax_id, mobile_no, email_id = frappe.db.get_value(
		"Supplier", supplier, ["supplier_type", "tax_id", "mobile_no", "email_id"]
	) or (None, None, None, None)

	missing = []
	if needs_tax_id(supplier_type) and not cstr(tax_id).strip():
		missing.append(_("Tax ID"))
	if not cstr(mobile_no).strip() and not party_phone_numbers("Supplier", supplier):
		missing.append(_("Phone Number"))
	if not cstr(email_id).strip() and not party_email_addresses("Supplier", supplier):
		missing.append(_("Email"))
	if not has_attachment(supplier):
		missing.append(_("Document Attachment"))
	return missing


def validate_supplier_at_transaction(doc, _method=None):
	"""validate on every buying document, and on a Payment Entry to a supplier: refuse to save
	while the supplier lacks Tax ID, phone, email or an attached document."""
	if doc.doctype == "Payment Entry":
		supplier = doc.party if doc.get("party_type") == "Supplier" else None
	else:
		supplier = doc.get("supplier")
	if not supplier:
		return

	missing = missing_supplier_details(supplier)
	if missing:
		frappe.throw(
			_("Supplier {0} is missing: {1}. Complete the Supplier first.").format(
				frappe.bold(doc.get("supplier_name") or doc.get("party_name") or supplier),
				", ".join(missing),
			),
			title=_("Supplier Details Incomplete"),
		)
