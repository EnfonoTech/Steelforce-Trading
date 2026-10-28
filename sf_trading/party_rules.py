# sf_trading/party_rules.py
"""The Customer / Supplier rules as data: Party Master Rule Set and the engine that reads it.

One document per party type (named "Customer" or "Supplier") holds what a master must carry (Required
Details), how a value must look (Format Checks), what may not repeat (Unique Checks) and which Supplier
Groups are exempt (Exemptions). An admin changes a rule in the desk; this module is the one place that
answers "what does this party lack?" for the server checks, the approval, the reports and -- through
`get_rules_for_client` -- every dialog and quick form.

Phase 0 (this module): the rule set exists, is seeded with today's rules and can be asked -- `evaluate`,
`format_failures`, `unique_failures`, `address_failures`, the Test Rules button and the diff report -- but
nothing refuses on it yet. A set answers for real only once its `enabled` box is ticked, and each consumer
(party_documents, party_master_rules, master_approval, the dialogs) is switched over in a later phase, one
module per commit, each against the diff report. The facts a rule reads (a mobile, an email, an address,
the contact numbers, the document rows, any file attached) come from the same helpers the code's own rules
use, so the two cannot disagree about what a party has.

Stages. A Required Details row says where it bites: `save` (the master's own save), `approval` (before Send
for Approval and Approve) and `voucher` (an order, invoice or payment raised for the party). What only an
existing master can carry -- a document file, a mobile, an email, an address, the contact numbers -- is not
asked at the very first save, because core makes those after the master's own insert.
"""

from __future__ import annotations

import re

import frappe
from frappe import _
from frappe.utils import cint, cstr, get_datetime

RULE_SET = "Party Master Rule Set"
ROLE = "Master Rules Manager"
PARTY_TYPES = ("Customer", "Supplier")
STAGES = ("save", "approval", "voucher")
STAGE_FLAG = {"save": "at_save", "approval": "at_approval", "voucher": "at_voucher"}

PAYMENT_TERMS = "Payment Terms"
MOBILE = "Mobile Number"
EMAIL = "Email"
ADDRESS = "Address"
ADDRESS_FIELD = "Address Field"
TAX_ID = "Tax ID / VAT"
CR = "CR Number"
CONTACTS = "Contact Numbers"
DOCUMENT_ROW = "Supporting Document with File"
DOCUMENT_ANY = "Any Document on File"
FILE_ANY = "Any File Attached"
EXPIRED = "No Expired Financial Document"
ANY_FIELD = "Any Field"

#: what only an existing master can carry: the first save is not asked for them
AFTER_INSERT = {MOBILE, EMAIL, ADDRESS, CONTACTS, DOCUMENT_ROW, DOCUMENT_ANY, FILE_ANY}
#: the Exemptions tick that stands a requirement aside for a supplier in the group
EXEMPT_TICK = {PAYMENT_TERMS: "payment_terms", DOCUMENT_ROW: "document", DOCUMENT_ANY: "document", TAX_ID: "tax_id"}

#: the wording when a row has no message of its own
DEFAULT_TEXT = {
	PAYMENT_TERMS: "Payment Terms",
	MOBILE: "Mobile Number",
	EMAIL: "Email",
	ADDRESS: "an Address",
	TAX_ID: "Tax ID",
	CR: "Commercial Registration Number",
	CONTACTS: "at least {min} contact numbers (found {found})",
	DOCUMENT_ROW: "a Supporting Document with its file attached",
	DOCUMENT_ANY: "a supporting document",
	FILE_ANY: "at least one attachment",
}

VAT_FIELD = {"Customer": "custom_vat_registration_number", "Supplier": "tax_id"}
CR_FIELD = "custom_commercial_registration_number"


# ─── the rule set ────────────────────────────────────────────────────────────────────────────────


def get_rules(party_type: str):
	"""The rule set document of this party type (cached by Frappe, dropped when it is saved), or None."""
	if party_type not in PARTY_TYPES:
		return None
	try:
		return frappe.get_cached_doc(RULE_SET, party_type)
	except frappe.DoesNotExistError:
		return None


def is_active(party_type: str) -> bool:
	"""Whether the rule set answers for real (its Enabled box), rather than the code's own rules."""
	rules = get_rules(party_type)
	return bool(rules and cint(rules.enabled))


def digits(value) -> str:
	return re.sub(r"\D", "", cstr(value))


# ─── what is true of a party ─────────────────────────────────────────────────────────────────────


class Facts:
	"""What is true of one party, read once and only when a rule asks. The helpers are the code's own."""

	def __init__(self, doc):
		self.doc = doc
		self._values = {}

	def _once(self, key, read):
		if key not in self._values:
			self._values[key] = read()
		return self._values[key]

	@property
	def payment_terms(self) -> bool:
		return bool(cstr(self.doc.get("payment_terms")).strip())

	@property
	def mobile(self) -> bool:
		from sf_trading import party_documents as pd

		return self._once("mobile", lambda: pd.has_mobile(self.doc))

	@property
	def email(self) -> bool:
		from sf_trading import party_documents as pd

		return self._once("email", lambda: pd.has_email(self.doc))

	@property
	def address(self) -> bool:
		from sf_trading import party_documents as pd

		return self._once("address", lambda: pd.has_address(self.doc))

	@property
	def phones(self) -> int:
		def read():
			if not self.doc.get("name") or self.doc.is_new():
				return 0
			from sf_trading.party_contact_cache import party_phone_numbers

			return len(party_phone_numbers(self.doc.doctype, self.doc.name))

		return self._once("phones", read)

	@property
	def document_rows(self) -> bool:
		from sf_trading import party_documents as pd

		return self._once("document_rows", lambda: bool(pd.attached_rows(self.doc)))

	@property
	def document_any(self) -> bool:
		from sf_trading import party_documents as pd

		return self._once("document_any", lambda: bool(pd.has_document(self.doc)))

	@property
	def file_any(self) -> bool:
		def read():
			if not self.doc.get("name") or self.doc.is_new():
				return False
			return bool(frappe.db.exists("File", {"attached_to_doctype": self.doc.doctype,
				"attached_to_name": self.doc.name}))

		return self._once("file_any", read)

	@property
	def tax_id(self) -> str:
		from sf_trading import party_documents as pd

		return pd.vat_number(self.doc)

	@property
	def cr(self) -> str:
		return cstr(self.doc.get(CR_FIELD)).strip()

	@property
	def expired(self) -> list:
		def read():
			from sf_trading import party_documents as pd

			return [
				_("{0} {1} expired on {2}").format(row.document_type, row.document_number or "",
					frappe.format_value(row.expiry_date, "Date")).replace("  ", " ")
				for row in pd.expired_financial_rows(self.doc.doctype, self.doc.get("name"))
			]

		return self._once("expired", read)

	@property
	def is_b2b(self) -> bool:
		"""A customer with a VAT number on file; a Company supplier."""
		if self.doc.doctype == "Supplier":
			return self.doc.get("supplier_type") == "Company"
		return bool(self.tax_id)

	@property
	def is_credit(self) -> bool:
		def read():
			from sf_trading import party_documents as pd

			return self.doc.doctype == "Customer" and pd.is_credit_customer(self.doc)

		return self._once("is_credit", read)

	@property
	def country(self) -> str:
		def read():
			company = self.doc.get("custom_company") if self.doc.doctype == "Customer" else None
			return company_country(company)

		return self._once("country", read)


def company_country(company=None) -> str:
	"""The country of this company (else the user's default one), lower case: the party's company is a
	customer's own Company, else the saving user's default company -- what the dialogs use."""
	company = company or frappe.defaults.get_user_default("company")
	value = frappe.db.get_value("Company", company, "country") if company else ""
	return cstr(value).strip().lower()


# ─── which rows apply ────────────────────────────────────────────────────────────────────────────


def _is_new_master(doc, rules) -> bool:
	"""Created at or after the set's Applies From (a master being made now is new), or an older customer the
	Customer approval took in because it turned credit."""
	if not rules.applies_from:
		return False
	if doc.is_new() or not doc.get("creation"):
		return True
	if get_datetime(doc.creation) >= get_datetime(rules.applies_from):
		return True
	from sf_trading.master_approval import comes_under_approval

	return comes_under_approval(doc)


def _exempt(doc, rules, requirement) -> bool:
	tick = EXEMPT_TICK.get(requirement)
	if not tick or doc.doctype != "Supplier":
		return False
	group = cstr(doc.get("supplier_group")).strip().lower()
	return bool(group) and any(cstr(row.supplier_group).strip().lower() == group and cint(row.get(tick))
		for row in rules.get("exemptions") or [])


def _fits(row, doc, facts) -> bool:
	"""The party-side conditions every kind of row shares: kind, credit, company country."""
	if not cint(row.get("enabled", 1)):
		return False
	kind = row.get("party_kind") or "Any"
	if kind == "B2B" and not facts.is_b2b:
		return False
	if kind == "B2C" and facts.is_b2b:
		return False
	if row.get("credit") == "Credit Customers Only" and not facts.is_credit:
		return False
	country = cstr(row.get("company_country")).strip().lower()
	return not country or country == facts.country


def applies(row, doc, facts, rules, stage: str | None = None) -> bool:
	"""Does this Required Details row hold for this party at this stage?"""
	if not _fits(row, doc, facts):
		return False
	if stage and not cint(row.get(STAGE_FLAG[stage])):
		return False
	if stage in ("save", "voucher") and row.get("existing_masters") == "New Masters Only" \
			and not _is_new_master(doc, rules):
		return False
	if stage == "save" and row.requirement in AFTER_INSERT and (doc.is_new() or doc.flags.get("in_insert")):
		return False
	return not _exempt(doc, rules, row.requirement)


# ─── Required Details ────────────────────────────────────────────────────────────────────────────


def _say(row, requirement, **values):
	text = cstr(row.get("message")).strip() or _(DEFAULT_TEXT.get(requirement, requirement))
	for key, value in values.items():
		text = text.replace("{" + key + "}", cstr(value))
	return text


def _unmet(row, doc, facts) -> list:
	"""The texts for what this row asks and the party lacks (empty when it has it)."""
	requirement = row.requirement
	if requirement == PAYMENT_TERMS:
		lacks = not facts.payment_terms
	elif requirement == MOBILE:
		lacks = not facts.mobile
	elif requirement == EMAIL:
		lacks = not facts.email
	elif requirement == ADDRESS:
		lacks = not facts.address
	elif requirement == TAX_ID:
		lacks = not facts.tax_id
	elif requirement == CR:
		lacks = not facts.cr
	elif requirement == CONTACTS:
		minimum, found = cint(row.get("min_count")) or 1, facts.phones
		return [_say(row, requirement, min=minimum, found=found)] if found < minimum else []
	elif requirement == DOCUMENT_ROW:
		lacks = not facts.document_rows
	elif requirement == DOCUMENT_ANY:
		lacks = not facts.document_any
	elif requirement == FILE_ANY:
		lacks = not facts.file_any
	elif requirement == EXPIRED:
		return list(facts.expired)
	elif requirement == ANY_FIELD:
		meta = frappe.get_meta(doc.doctype)
		field = cstr(row.get("fieldname")).strip()
		if not field or not meta.has_field(field) or cstr(doc.get(field)).strip():
			return []
		return [cstr(row.get("message")).strip() or _(meta.get_label(field))]
	else:
		return []
	return [_say(row, requirement)] if lacks else []


def _tidy(failures: list) -> list:
	"""A document row that is missing already says the document is: the other document asks stand down. Then
	one line per text."""
	if any(f.requirement == DOCUMENT_ROW for f in failures):
		failures = [f for f in failures if f.requirement not in (DOCUMENT_ANY, FILE_ANY)]
	out, seen = [], set()
	for failure in failures:
		if failure.text not in seen:
			seen.add(failure.text)
			out.append(failure)
	return out


def evaluate(doc, stage: str, rules=None, facts=None) -> list:
	"""What this party lacks at this stage, by the rule set: a list of {requirement, text, fieldname}."""
	rules = rules or get_rules(doc.doctype)
	if not rules or stage not in STAGES:
		return []
	facts = facts or Facts(doc)
	failures = []
	for row in rules.get("required") or []:
		if row.requirement == ADDRESS_FIELD or not applies(row, doc, facts, rules, stage):
			continue
		for text in _unmet(row, doc, facts):
			failures.append(frappe._dict(requirement=row.requirement, text=text,
				fieldname=cstr(row.get("fieldname")) or None))
	return _tidy(failures)


# ─── Format Checks and Unique Checks ─────────────────────────────────────────────────────────────


def passes(row, value: str) -> bool:
	"""Does this value satisfy the Format Checks row? A rule the row cannot read counts as passing."""
	rule = cstr(row.get("value")).strip()
	check = row.get("check")
	if check == "Regular Expression":
		try:
			return bool(re.match(rule, value))
		except re.error:
			return True
	if check == "Exact Digits":
		return len(digits(value)) == cint(rule)
	if check == "Minimum Digits":
		return len(digits(value)) >= cint(rule)
	if check == "Starts With":
		return value.startswith(rule)
	if check == "Ends With":
		return value.endswith(rule)
	return True


def _target(doc, field: str):
	"""(the value, its fieldname) a Format or Unique row reads on the master; Postal Code lives on the Address."""
	from sf_trading import party_documents as pd

	if field == "VAT / Tax ID":
		return pd.vat_number(doc), VAT_FIELD.get(doc.doctype)
	if field == "CR Number" and doc.doctype == "Customer":
		return cstr(doc.get(CR_FIELD)).strip(), CR_FIELD
	if field == "Mobile Number":
		return cstr(doc.get("mobile_no") or doc.get("custom_mobile_no")).strip(), "mobile_no"
	return "", None


def _is_changed(doc, fieldname) -> bool:
	return doc.is_new() or bool(fieldname and doc.has_value_changed(fieldname))


def format_failures(doc, rules=None, facts=None) -> list:
	"""The Format Checks the master's own values fail: a list of {field, fieldname, text}."""
	rules = rules or get_rules(doc.doctype)
	if not rules:
		return []
	facts = facts or Facts(doc)
	out = []
	for row in rules.get("formats") or []:
		if row.field == "Postal Code" or not _fits(row, doc, facts):
			continue
		value, fieldname = _target(doc, row.field)
		if not value:
			continue
		if row.get("existing_masters") != "All Values" and not _is_changed(doc, fieldname):
			continue
		if not passes(row, value):
			out.append(frappe._dict(field=row.field, fieldname=fieldname, text=cstr(row.message)))
	return out


def unique_failures(doc, rules=None, facts=None) -> list:
	"""The Unique Checks the master breaks: {field, fieldname, value, clash, text, override_roles, needs_reason}.
	The override itself (a manager's reason) belongs to the caller; the roles that may use it are returned."""
	rules = rules or get_rules(doc.doctype)
	if not rules:
		return []
	facts = facts or Facts(doc)
	out = []
	for row in rules.get("uniques") or []:
		if not cint(row.get("enabled", 1)):
			continue
		kind = row.get("party_kind") or "Any"
		if (kind == "B2B" and not facts.is_b2b) or (kind == "B2C" and facts.is_b2b):
			continue
		value, fieldname = _target(doc, row.field)
		if not value or not fieldname or not _is_changed(doc, fieldname):
			continue
		filters = {fieldname: value, "name": ("!=", doc.name or "")}
		if row.get("scope") == "Enabled Only":
			filters["disabled"] = 0
		clash = frappe.db.get_value(doc.doctype, filters, "name")
		if clash:
			out.append(frappe._dict(field=row.field, fieldname=fieldname, value=value, clash=clash,
				text=cstr(row.get("message")).strip(), needs_reason=cint(row.get("override_needs_reason")),
				override_roles=[r.role for r in row.get("override_roles") or []]))
	return out


# ─── the Contact and Address core makes for a party ──────────────────────────────────────────────


def address_failures(address, party, rules=None) -> list:
	"""What a new or edited Address of this party lacks: Address Field rows, then the Postal Code format. Only a
	new address, or a field that changed, is asked."""
	rules = rules or get_rules(party.doctype)
	if not rules:
		return []
	facts = Facts(party)
	out = []
	for row in rules.get("required") or []:
		if row.requirement != ADDRESS_FIELD or not _fits(row, party, facts):
			continue
		field = cstr(row.get("fieldname")).strip()
		if not field or cstr(address.get(field)).strip():
			continue
		if not (address.is_new() or address.has_value_changed(field)):
			continue
		label = _(address.meta.get_label(field) or field)
		text = (cstr(row.get("message")).strip() or "{label} is required.").replace("{label}", label)
		out.append(frappe._dict(requirement=ADDRESS_FIELD, fieldname=field, text=text))
	for row in rules.get("formats") or []:
		if row.field != "Postal Code" or not _fits(row, party, facts):
			continue
		value = cstr(address.get("pincode")).strip()
		if value and (address.is_new() or address.has_value_changed("pincode")) and not passes(row, value):
			out.append(frappe._dict(requirement="Postal Code", fieldname="pincode", text=cstr(row.message)))
	return out


def contact_phone_failures(contact, party, rules=None) -> list:
	"""The Mobile Number format a new phone row of this party's Contact fails."""
	rules = rules or get_rules(party.doctype)
	if not rules:
		return []
	facts = Facts(party)
	before = contact.get_doc_before_save()
	known = {cstr(row.phone).strip() for row in (before.get("phone_nos") if before else None) or []}
	out = []
	for row in rules.get("formats") or []:
		if row.field != "Mobile Number" or not _fits(row, party, facts):
			continue
		for phone in contact.get("phone_nos") or []:
			value = cstr(phone.phone).strip()
			if value and value not in known and not passes(row, value):
				out.append(frappe._dict(requirement="Mobile Number", fieldname="phone", text=cstr(row.message),
					value=value))
	return out


# ─── for forms and dialogs, and for the people who keep the rules ────────────────────────────────

CLIENT_REQUIRED = ("requirement", "fieldname", "min_count", "document_type", "party_kind", "credit",
	"company_country", "existing_masters", "at_save", "at_approval", "at_voucher", "message")
CLIENT_FORMAT = ("field", "check", "value", "party_kind", "credit", "company_country", "existing_masters", "message")
CLIENT_UNIQUE = ("field", "scope", "party_kind", "override_needs_reason", "message")
CLIENT_EXEMPT = ("supplier_group", "payment_terms", "document", "tax_id")


def _plain(row, keys) -> dict:
	return {key: row.get(key) for key in keys}


@frappe.whitelist()
def get_rules_for_client(party_type: str) -> dict:
	"""The enabled rows of this party type, for a form or dialog to draw its marks and checks from. The server
	stays the authority: this only saves the browser keeping a copy of every rule."""
	if party_type not in PARTY_TYPES:
		frappe.throw(_("Only Customer and Supplier carry party rules."))
	frappe.has_permission(party_type, "read", throw=True)
	rules = get_rules(party_type)
	if not rules:
		return {"exists": False}
	return {
		"exists": True,
		"enabled": bool(cint(rules.enabled)),
		"applies_from": str(rules.applies_from or ""),
		"country": company_country(),
		"required": [_plain(r, CLIENT_REQUIRED) for r in rules.get("required") or [] if cint(r.enabled)],
		"formats": [_plain(r, CLIENT_FORMAT) for r in rules.get("formats") or [] if cint(r.enabled)],
		"uniques": [dict(_plain(r, CLIENT_UNIQUE), override_roles=[o.role for o in r.get("override_roles") or []])
			for r in rules.get("uniques") or [] if cint(r.enabled)],
		"exemptions": [_plain(r, CLIENT_EXEMPT) for r in rules.get("exemptions") or []],
	}


def legacy_approval_missing(doc) -> list:
	"""What the code's own approval gate says the party lacks (the thing the rule set must agree with)."""
	from sf_trading import master_approval as ma

	return ma.missing_requirements(doc)


@frappe.whitelist()
def test_rules(party_type: str, party: str) -> dict:
	"""For the Test Rules button: what the rule set says of this party at each stage, what its format and unique
	checks say, and -- for the approval -- whether it agrees with the code's own gate."""
	frappe.only_for(("System Manager", ROLE))
	if party_type not in PARTY_TYPES:
		frappe.throw(_("Only Customer and Supplier carry party rules."))
	rules = get_rules(party_type)
	if not rules:
		frappe.throw(_("There is no Party Master Rule Set for {0} yet.").format(_(party_type)))
	doc = frappe.get_doc(party_type, party)
	facts = Facts(doc)
	stages = {stage: [f.text for f in evaluate(doc, stage, rules, facts)] for stage in STAGES}
	legacy = legacy_approval_missing(doc)
	return {
		"party": party,
		"enabled": bool(cint(rules.enabled)),
		"stages": stages,
		"format": [f.text for f in format_failures(doc, rules, facts)],
		"unique": [dict(field=u.field, value=u.value, clash=u.clash) for u in unique_failures(doc, rules, facts)],
		"legacy_approval": legacy,
		"approval_agrees": sorted(legacy) == sorted(stages["approval"]),
	}


# ─── setup ───────────────────────────────────────────────────────────────────────────────────────


def setup():
	"""after_migrate: the Master Rules Manager role, and its access to the rule set."""
	if not frappe.db.exists("DocType", RULE_SET):
		return
	from frappe.permissions import add_permission, update_permission_property

	if not frappe.db.exists("Role", ROLE):
		frappe.get_doc({"doctype": "Role", "role_name": ROLE, "desk_access": 1}).insert(ignore_permissions=True)
	if frappe.db.exists("Custom DocPerm", {"parent": RULE_SET, "role": ROLE, "permlevel": 0, "write": 1}):
		return
	add_permission(RULE_SET, ROLE, 0)
	for ptype in ("read", "write", "create", "report"):
		update_permission_property(RULE_SET, ROLE, 0, ptype, 1, validate=False)
