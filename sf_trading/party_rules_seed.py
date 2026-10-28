# sf_trading/party_rules_seed.py
"""Today's Customer / Supplier rules, written as rows of the Party Master Rule Set.

Seeded once (patch seed_party_master_rules), never over a set that exists, so what an admin has changed is
never put back. The rows reproduce what the code enforces today:

* the approval's requirements (master_approval.missing_requirements) exactly -- the diff report holds the
  rule set to that on real data;
* the save-time and voucher-time rules of party_documents, party_completeness, supplier_validation and
  sales_order_governance as far as one row can say them: where the code treats a supplier's Payment Terms as
  needed from a new master's first save but from every supplier at a voucher, that is two rows. Each is
  checked against the module that holds it when that module is switched over (a later phase).

The order of the Required Details rows is the order the approval lists what is missing.
"""

from __future__ import annotations

import frappe

from sf_trading import party_rules as pr

SAUDI = "Saudi Arabia"
VAT_REGEX = r"^3\d{13}3$"
ADDRESS_FIELDS = (
	("address_line1", "Address Line 1"),
	("custom_building_number", "Building Number"),
	("custom_area", "District / Area"),
	("city", "City"),
	("pincode", "Postal Code"),
)


def req(requirement, kind="Any", credit="Any", new_only=False, save=0, approval=0, voucher=0, message="",
		minimum=1, fieldname="", country=""):
	return {
		"enabled": 1, "requirement": requirement, "fieldname": fieldname, "min_count": minimum, "party_kind": kind,
		"credit": credit, "company_country": country, "existing_masters": "New Masters Only" if new_only else "All Masters",
		"at_save": save, "at_approval": approval, "at_voucher": voucher, "message": message,
	}


def fmt(field, check, value, message, kind="Any"):
	return {"enabled": 1, "field": field, "check": check, "value": value, "party_kind": kind, "credit": "Any",
		"company_country": SAUDI, "existing_masters": "New or Changed Values Only", "message": message}


def unique(field, roles, scope="All Parties", kind="Any"):
	return {"enabled": 1, "field": field, "scope": scope, "party_kind": kind, "override_needs_reason": 1,
		"override_roles": [{"role": role} for role in roles], "message":
		"VAT Registration Number {value} is already used by {clash}." if field == "VAT / Tax ID"
		else "Commercial Registration Number {value} is already used by {clash}."}


def address_rows(kind_word):
	return [req(pr.ADDRESS_FIELD, kind="B2B", save=1, fieldname=field, country=SAUDI,
		message="{label} is required for B2B %s in Saudi Arabia." % kind_word) for field, _label in ADDRESS_FIELDS]


def customer_set() -> dict:
	return {
		"required": [
			req(pr.CR, kind="B2B", save=1, approval=1),
			req(pr.CONTACTS, minimum=1, approval=1, voucher=1, message="a Contact with a phone number"),
			req(pr.PAYMENT_TERMS, credit="Credit Customers Only", new_only=True, save=1, approval=1, voucher=1),
			req(pr.DOCUMENT_ROW, credit="Credit Customers Only", new_only=True, save=1, approval=1, voucher=1),
			req(pr.CONTACTS, credit="Credit Customers Only", minimum=2, approval=1, voucher=1),
			req(pr.EMAIL, credit="Credit Customers Only", approval=1, voucher=1, message="an email address"),
			req(pr.FILE_ANY, credit="Credit Customers Only", approval=1, voucher=1),
			req(pr.DOCUMENT_ANY, kind="B2B", approval=1, voucher=1, message="a supporting document (VAT registered)"),
			req(pr.EXPIRED, approval=1, voucher=1),
			*address_rows("customers"),
		],
		"formats": [
			fmt("VAT / Tax ID", "Regular Expression", VAT_REGEX,
				"VAT Registration Number must be exactly 15 digits, starting and ending with 3."),
			fmt("Mobile Number", "Minimum Digits", "10", "Mobile number must have at least 10 digits."),
			fmt("Postal Code", "Exact Digits", "5", "Postal Code must be exactly 5 digits.", kind="B2B"),
		],
		"uniques": [
			unique("VAT / Tax ID", ("Sales Manager", "Sales Master Manager", "System Manager")),
			unique("CR Number", ("Sales Manager", "Sales Master Manager", "System Manager"), scope="Enabled Only", kind="B2B"),
		],
		"exemptions": [],
	}


def supplier_set() -> dict:
	return {
		"required": [
			req(pr.PAYMENT_TERMS, new_only=True, save=1),
			req(pr.PAYMENT_TERMS, approval=1, voucher=1),
			req(pr.DOCUMENT_ROW, new_only=True, save=1, approval=1, voucher=1),
			req(pr.MOBILE, new_only=True, save=1, approval=1, voucher=1),
			req(pr.EMAIL, new_only=True, save=1, approval=1, voucher=1),
			req(pr.ADDRESS, kind="B2B", new_only=True, save=1, approval=1, voucher=1),
			req(pr.TAX_ID, kind="B2B", save=1, approval=1),
			req(pr.DOCUMENT_ANY, kind="B2B", voucher=1, message="a supporting document (Company supplier)"),
			req(pr.EXPIRED, approval=1, voucher=1),
			*address_rows("suppliers"),
		],
		"formats": [
			fmt("VAT / Tax ID", "Regular Expression", VAT_REGEX,
				"VAT Registration Number must be exactly 15 digits, starting and ending with 3."),
			fmt("Mobile Number", "Minimum Digits", "10", "Mobile number must have at least 10 digits."),
			fmt("Postal Code", "Exact Digits", "5", "Postal Code must be exactly 5 digits.", kind="B2B"),
		],
		"uniques": [unique("VAT / Tax ID", ("Purchase Manager", "Purchase Master Manager", "System Manager"))],
		"exemptions": [
			{"supplier_group": "Govt", "payment_terms": 1, "document": 1, "tax_id": 1},
			{"supplier_group": "W/O VAT", "payment_terms": 1, "document": 1, "tax_id": 1},
			{"supplier_group": "Other Supplier", "payment_terms": 0, "document": 1, "tax_id": 0},
		],
	}


SETS = {"Customer": customer_set, "Supplier": supplier_set}


def seed() -> list:
	"""Make the Customer and Supplier rule sets that do not exist yet. Never touches one that does."""
	if not frappe.db.exists("DocType", pr.RULE_SET):
		return []
	applies_from = frappe.db.get_single_value("SF Trading Settings", "master_rules_from")
	made = []
	for party_type, build in SETS.items():
		if frappe.db.exists(pr.RULE_SET, party_type):
			continue
		doc = frappe.get_doc({"doctype": pr.RULE_SET, "party_type": party_type, "enabled": 0,
			"applies_from": applies_from, "notes": "Seeded with the rules the code enforced when it was made. "
			"Phase 0: not enabled; the code's own rules still answer.", **build()})
		# a Supplier Group or Role the site does not have yet is still a rule the code applies by name
		doc.flags.ignore_links = True
		doc.insert(ignore_permissions=True)
		made.append(party_type)
	return made
