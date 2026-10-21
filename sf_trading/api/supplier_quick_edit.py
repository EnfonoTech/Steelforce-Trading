# sf_trading/api/supplier_quick_edit.py
"""Quick Edit Billing Fields for a supplier -- the supplier-side twin of customer_quick_edit.

Opened from the Supplier master and from the Supplier field of a Purchase Order, Purchase Invoice or
Purchase Receipt, so whoever is raising the document can see -- and fix, without leaving it -- what
stops that supplier being bought from or paid. "What blocks" is read from the very rules the gates
run, for an old supplier as much as a new one:

* party_documents (Purchase Order / Receipt / Invoice / supplier payment): a
  supplier created on or after New-Master Rules From needs Payment Terms, a Supporting Documents row
  with its file and a mobile number; ANY supplier without Payment Terms (2026-10-13) or holding an
  expired document marked Financial Implication is refused;
* party_documents.validate_supplier_tax_id: a Company supplier cannot even be saved without a Tax ID;
* supplier_validation (only while SF Trading Settings > Enforce Supplier Completeness is on): Tax ID,
  phone, email and an attached document, for every supplier.

Every launch point opens the dialog against a saved Supplier, so an upload is linked to it and made
a Supporting Documents row before the supplier is saved.
"""

from __future__ import annotations

import frappe
from frappe import _
from frappe.utils import cstr

from sf_trading.party_contact_cache import party_email_addresses, party_phone_numbers

PARTY = "Supplier"


def _primary(supplier: str, kind: str) -> str | None:
	"""The supplier's own primary Contact / Address link, else the first one linked to it."""
	primary = frappe.db.get_value(PARTY, supplier, "supplier_primary_" + kind.lower())
	if primary:
		return primary
	return frappe.db.get_value("Dynamic Link", {"parenttype": kind, "link_doctype": PARTY, "link_name": supplier},
		"parent")


def _expired_text(row) -> str:
	parts = [row.document_type, row.document_number or "", _("expired on"), frappe.format_value(row.expiry_date, "Date")]
	return " ".join(p for p in parts if p)


def blocking(doc) -> frappe._dict:
	"""What stops this supplier being bought from or paid, by the rules the gates run."""
	from sf_trading import party_documents as pd
	from sf_trading import supplier_validation as sv

	applies = pd.is_governed(doc) and pd.is_new_master(doc)
	enforced = sv.enforcement_enabled()
	items = []
	if applies:
		items += pd.missing_details(doc, after_insert=True)
	# old suppliers too (a new one's are already named above; the duplicate is dropped below)
	items += pd.missing_payment_terms(doc)
	if not any("Supporting Document" in i for i in items):
		items += pd.missing_document(doc)
	if doc.get("supplier_type") == "Company" and not cstr(doc.get("tax_id")).strip() and not pd.completeness_exempt(doc):
		items.append(_("Tax ID"))
	if enforced:
		items += sv.missing_supplier_details(doc.name)
	seen, unique = set(), []
	for item in items:
		if item not in seen:
			seen.add(item)
			unique.append(item)
	rows = bool(pd.attached_rows(doc))
	return frappe._dict(
		applies=applies,
		missing=unique,
		expired=[_expired_text(r) for r in pd.expired_financial_rows(PARTY, doc.name)],
		has_document_row=rows,
		enforced=enforced,
		needs_document=bool(not pd.documents_exempt(doc) and ((applies and not rows) or pd.missing_document(doc)
			or (enforced and not sv.has_attachment(doc.name)))),
	)


@frappe.whitelist()
def get_quick_edit_data(supplier: str) -> dict:
	"""Current values and what blocks billing right now."""
	from sf_trading import party_documents as pd

	frappe.has_permission(PARTY, "read", doc=supplier, throw=True)
	doc = frappe.get_doc(PARTY, supplier)
	phones = party_phone_numbers(PARTY, supplier)
	emails = party_email_addresses(PARTY, supplier)
	address = _primary(supplier, "Address")
	state = blocking(doc)
	mobile = cstr(doc.get("custom_mobile_no")).strip() or cstr(doc.get("mobile_no")).strip() or (phones[0] if phones else "")
	email = cstr(doc.get("email_id")).strip() or (emails[0] if emails else "")
	# Mobile and Email are mandatory on every supplier; for an older one the purchase gates do not
	# yet refuse without them, so they are listed apart from "blocking"
	named = " ".join(state.missing).lower()
	required = []
	if not mobile and "mobile" not in named and "phone" not in named:
		required.append(_("Mobile Number"))
	if not email and "email" not in named:
		required.append(_("Email"))
	return {
		"supplier_name": doc.supplier_name,
		"supplier_type": doc.supplier_type,
		"is_company": doc.supplier_type == "Company",
		# Govt and W/O VAT suppliers are not asked for a Tax ID, Payment Terms or a document (party_documents)
		"completeness_exempt": pd.completeness_exempt(doc),
		"tax_id_required": doc.supplier_type == "Company" and not pd.completeness_exempt(doc),
		"tax_id": doc.get("tax_id"),
		"payment_terms": doc.get("payment_terms"),
		"mobile": mobile,
		"email": email,
		"required_missing": required,
		"address_line1": frappe.db.get_value("Address", address, "address_line1") if address else "",
		"address_city": frappe.db.get_value("Address", address, "city") if address else "",
		"rules_apply": state.applies,
		# the dialog marks Payment Terms with the asterisk exactly when a rule asks for it
		"payment_terms_required": pd.payment_terms_required(PARTY) and not pd.completeness_exempt(doc),
		"has_document_row": state.has_document_row,
		"enforced": state.enforced,
		"needs_document": state.needs_document,
		"blocking": state.missing,
		"expired": state.expired,
	}


@frappe.whitelist()
def save_quick_edit_data(supplier: str, values) -> dict:
	"""Write back what the dialog exposed. Contact and Address are saved on their own, so a supplier
	save that is still refused (say, a duplicate Tax ID) does not throw away a fixed phone number."""
	frappe.has_permission(PARTY, "write", doc=supplier, throw=True)
	if isinstance(values, str):
		values = frappe.parse_json(values)
	values = frappe._dict(values or {})
	result = {"saved": True, "warnings": []}

	attachment = cstr(values.get("attachment")).strip()
	if attachment:
		_link_attachment(supplier, attachment)

	mobile, email = cstr(values.get("mobile")).strip(), cstr(values.get("email")).strip()
	if mobile or email:
		_save_contact(supplier, mobile, email)
	line1, city = cstr(values.get("address_line1")).strip(), cstr(values.get("address_city")).strip()
	if line1 or city:
		_save_address(supplier, line1, city)

	tax_id, terms = cstr(values.get("tax_id")).strip(), cstr(values.get("payment_terms")).strip()
	if mobile or tax_id or terms or attachment:
		doc = frappe.get_doc(PARTY, supplier)
		if mobile and doc.meta.has_field("custom_mobile_no"):
			doc.custom_mobile_no = mobile
		if tax_id:
			doc.tax_id = tax_id
		if terms:
			doc.payment_terms = terms
		if attachment:
			from sf_trading.party_documents import TABLE, dialog_document_row

			doc.append(TABLE, dialog_document_row(PARTY, values.get("document_type") or "Commercial Registration",
				values.get("document_number"), values.get("expiry_date"), attachment))
		try:
			doc.save()
		except frappe.ValidationError as e:
			result["warnings"].append(str(e))
			# the throw already queued its own popup text; the warning carries it instead
			frappe.clear_messages()
	return result


def _save_contact(supplier: str, mobile: str, email: str) -> None:
	name = _primary(supplier, "Contact")
	if name:
		contact = frappe.get_doc("Contact", name)
	else:
		contact = frappe.new_doc("Contact")
		contact.first_name = frappe.db.get_value(PARTY, supplier, "supplier_name") or supplier
		contact.append("links", {"link_doctype": PARTY, "link_name": supplier})
	if mobile:
		contact.set("phone_nos", [])
		contact.append("phone_nos", {"phone": mobile, "is_primary_phone": 1, "is_primary_mobile_no": 1})
	if email:
		contact.set("email_ids", [])
		contact.append("email_ids", {"email_id": email, "is_primary": 1})
	contact.save(ignore_permissions=True)
	if not frappe.db.get_value(PARTY, supplier, "supplier_primary_contact"):
		frappe.db.set_value(PARTY, supplier, "supplier_primary_contact", contact.name)


def _save_address(supplier: str, line1: str, city: str) -> None:
	name = _primary(supplier, "Address")
	if name:
		address = frappe.get_doc("Address", name)
	else:
		address = frappe.new_doc("Address")
		address.address_title = frappe.db.get_value(PARTY, supplier, "supplier_name") or supplier
		address.address_type = "Billing"
		address.country = frappe.db.get_value(PARTY, supplier, "country") or "Bahrain"
		address.append("links", {"link_doctype": PARTY, "link_name": supplier})
	if line1:
		address.address_line1 = line1
	if city:
		address.city = city
	address.save(ignore_permissions=True)
	if not frappe.db.get_value(PARTY, supplier, "supplier_primary_address"):
		frappe.db.set_value(PARTY, supplier, "supplier_primary_address", address.name)


def _link_attachment(supplier: str, file_url: str) -> None:
	"""The dialog's upload lands unattached; it belongs to this supplier."""
	name = frappe.db.get_value("File", {"file_url": file_url}, "name")
	if name:
		frappe.db.set_value("File", name, {"attached_to_doctype": PARTY, "attached_to_name": supplier})
