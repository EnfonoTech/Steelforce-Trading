# sf_trading/party_master_rules.py
"""The Create Customer / Create Supplier dialogs' own rules, on the master itself, so they hold
wherever a Customer or Supplier is made or edited: the master form, ERPNext's "+ Create a new ..."
link popup, Quick Edit, an API call, and the two dialogs.

Until now these lived only in api/customer.py and api/supplier.py, so a party made any other way
skipped them:

  * VAT Registration Number / Tax ID: exactly 15 digits, starting and ending with 3 (Saudi companies);
  * the same number on no other Customer / Supplier (a manager's override, with a reason, is the one
    exception -- it only exists in the two dialogs, which set `flags.allow_duplicate_vat`);
  * a B2B customer's Commercial Registration number on no other customer (same override as the VAT
    number; a disabled customer does not hold its CR, so disabling a duplicate frees it);
  * a mobile number of at least 10 digits (Saudi companies), checked on the master's own field and
    on the Contact core makes for it;
  * a B2B party's address: Line 1, Building Number, District / Area, City and a 5-digit Postal Code
    (Saudi companies), checked on the Address core makes for it.

Only a NEW value is checked (a new party, a changed number, a new phone row, a new or edited
address): an old master whose number already breaks a rule must not become uneditable.

Saudi-only means the party's company is in Saudi Arabia: a Customer's own Company, else the saving
user's default company -- what the dialogs use. A bench with no Saudi company is untouched by the
format rules; the duplicate rule applies everywhere.

Stands aside, like party_documents, for a data import, patch, install, migrate or ignore_mandatory
creation, and for a frozen or disabled party (party_completeness.only_status_fields_changed).
"""

from __future__ import annotations

import re

import frappe
from frappe import _
from frappe.utils import cstr

from sf_trading.party_completeness import only_status_fields_changed
from sf_trading.party_documents import stands_aside, vat_number

VAT_FORMAT = re.compile(r"^3\d{13}3$")
VAT_FIELD = {"Customer": "custom_vat_registration_number", "Supplier": "tax_id"}
CR_FIELD = "custom_commercial_registration_number"
PARTY_DOCTYPES = ("Customer", "Supplier")

#: who may knowingly save a second party under one number (the dialogs ask for a reason as well)
VAT_OVERRIDE_ROLES = {
	"Customer": {"Sales Manager", "Sales Master Manager", "System Manager"},
	"Supplier": {"Purchase Manager", "Purchase Master Manager", "System Manager"},
}

#: (fieldname, label) -- the label is translated where it is shown
ADDRESS_REQUIRED = (
	("address_line1", "Address Line 1"),
	("custom_building_number", "Building Number"),
	("custom_area", "District / Area"),
	("city", "City"),
	("pincode", "Postal Code"),
)


def digits(value) -> str:
	return re.sub(r"\D", "", cstr(value))


def is_saudi(company: str | None = None) -> bool:
	"""Whether this company (else the user's default one) is in Saudi Arabia."""
	company = company or frappe.defaults.get_user_default("company")
	country = frappe.db.get_value("Company", company, "country") if company else ""
	return cstr(country).strip().lower() == "saudi arabia"


def party_is_saudi(doc) -> bool:
	return is_saudi(doc.get("custom_company") if doc.doctype == "Customer" else None)


def can_override_duplicate(doctype: str, user: str | None = None) -> bool:
	user = user or frappe.session.user
	return user == "Administrator" or bool(set(frappe.get_roles(user)) & VAT_OVERRIDE_ROLES[doctype])


def _number_is_new(doc) -> bool:
	return doc.is_new() or doc.has_value_changed(VAT_FIELD[doc.doctype])


def validate_vat_format(doc):
	vat = vat_number(doc)
	if vat and _number_is_new(doc) and party_is_saudi(doc) and not VAT_FORMAT.match(vat):
		frappe.throw(
			_("VAT Registration Number must be exactly 15 digits, starting and ending with 3."),
			title=_("Invalid VAT Registration Number"),
		)


def validate_unique_vat(doc):
	"""No second Customer / Supplier under one VAT Registration Number / Tax ID."""
	vat = vat_number(doc)
	if not vat or not _number_is_new(doc):
		return
	# set by the Create dialogs after their own manager-and-reason check
	if doc.flags.get("allow_duplicate_vat") or doc.flags.get("allow_duplicate_tax_id"):
		return

	clash = frappe.db.get_value(
		doc.doctype, {VAT_FIELD[doc.doctype]: vat, "name": ("!=", doc.name or "")}, "name"
	)
	if clash:
		frappe.throw(
			_("VAT Registration Number {0} is already used by {1} {2}.").format(
				frappe.bold(vat), _(doc.doctype), frappe.bold(clash)
			),
			title=_("Duplicate VAT Registration Number"),
		)


def validate_unique_cr(doc):
	"""A B2B customer (a VAT number on file) holds a Commercial Registration number no other customer holds."""
	if doc.doctype != "Customer" or not vat_number(doc):
		return
	cr = cstr(doc.get(CR_FIELD)).strip()
	if not cr or not (doc.is_new() or doc.has_value_changed(CR_FIELD)):
		return
	if doc.flags.get("allow_duplicate_vat"):
		return

	clash = frappe.db.get_value("Customer", {CR_FIELD: cr, "disabled": 0, "name": ("!=", doc.name or "")}, "name")
	if clash:
		frappe.throw(
			_("Commercial Registration Number {0} is already used by Customer {1}.").format(
				frappe.bold(cr), frappe.bold(clash)
			),
			title=_("Duplicate Commercial Registration Number"),
		)


def validate_mobile(doc):
	"""The master's own mobile field. A quick-entry party has none yet: its Contact carries the
	number and validate_contact_phones checks it there."""
	mobile = cstr(doc.get("mobile_no") or doc.get("custom_mobile_no")).strip()
	if not mobile or not (doc.is_new() or doc.has_value_changed("mobile_no")):
		return
	if party_is_saudi(doc) and len(digits(mobile)) < 10:
		frappe.throw(_("Mobile number must have at least 10 digits."), title=_("Invalid Mobile Number"))


def validate_master(doc, _method=None):
	"""Customer / Supplier validate."""
	if stands_aside(doc) or only_status_fields_changed(doc):
		return
	validate_vat_format(doc)
	validate_unique_vat(doc)
	validate_unique_cr(doc)
	validate_mobile(doc)


# ─── the Contact and Address core makes for a new party ─────────────────────────────────────────


def _linked_parties(doc) -> list[tuple[str, str]]:
	return [
		(link.link_doctype, link.link_name)
		for link in doc.get("links") or []
		if link.link_doctype in PARTY_DOCTYPES and link.link_name
	]


def _party_saudi(doctype: str, name: str) -> bool:
	company = frappe.db.get_value("Customer", name, "custom_company") if doctype == "Customer" else None
	return is_saudi(company)


def validate_contact_phones(doc, _method=None):
	"""Contact validate: a phone row added to a Saudi party's Contact has at least 10 digits."""
	if stands_aside(doc):
		return
	if not any(_party_saudi(*party) for party in _linked_parties(doc)):
		return
	before = doc.get_doc_before_save()
	known = {cstr(row.phone).strip() for row in (before.get("phone_nos") if before else None) or []}
	for row in doc.get("phone_nos") or []:
		phone = cstr(row.phone).strip()
		if phone and phone not in known and len(digits(phone)) < 10:
			frappe.throw(
				_("Phone number {0} must have at least 10 digits.").format(frappe.bold(phone)),
				title=_("Invalid Mobile Number"),
			)


def _is_b2b(doctype: str, name: str) -> bool:
	"""A Company supplier; a customer with a VAT number on file."""
	if doctype == "Supplier":
		return frappe.db.get_value("Supplier", name, "supplier_type") == "Company"
	vat = cstr(frappe.db.get_value(doctype, name, VAT_FIELD[doctype])).strip()
	return bool(vat) and vat != "0"


def validate_b2b_address(doc, _method=None):
	"""Address validate: a Saudi B2B (VAT-bearing) party's new or edited address is complete."""
	if stands_aside(doc):
		return
	parties = [p for p in _linked_parties(doc) if _party_saudi(*p) and _is_b2b(*p)]
	if not parties:
		return
	if not doc.is_new() and not any(doc.has_value_changed(f) for f, _label in ADDRESS_REQUIRED):
		return

	kind = _("customers") if parties[0][0] == "Customer" else _("suppliers")
	for fieldname, label in ADDRESS_REQUIRED:
		if not cstr(doc.get(fieldname)).strip():
			frappe.throw(
				_("{0} is required for B2B {1} in Saudi Arabia.").format(_(label), kind),
				title=_("Address Incomplete"),
			)
	if len(digits(doc.pincode)) != 5:
		frappe.throw(_("Postal Code must be exactly 5 digits."), title=_("Invalid Postal Code"))
