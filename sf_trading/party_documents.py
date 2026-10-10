# sf_trading/party_documents.py
"""Supporting documents and the other details a new customer or supplier must carry.

Who is governed: every Supplier, and every B2B Customer (a VAT Registration Number on file --
party_completeness.is_b2b_customer). Which of them are held to the rules: masters created at or
after SF Trading Settings > New-Master Rules From (a date and time, set to the moment of the
deploy). Older masters are never blocked by this module; the Master Data Completeness report lists
what they still lack so it can be filled in.

What a governed new master needs:

* Payment Terms, from its very first save -- the quick entry and the create dialogs ask for it.
* Once it exists: at least one Supporting Documents row with its file attached, and for a supplier
  a mobile number (on the master or a linked Contact). The quick entry cannot carry a file and the
  full form has no editable mobile, so the first save is let through and every save after it -- and
  every invoice, order or supplier payment against it (`validate_party_at_transaction`) -- needs them.

Every supplier of type Company needs a Tax ID on every save, old ones included: production had
that as a hand-made Property Setter (reqd), which the app's fixture replaces with a mark that only
applies to Company suppliers, so the server side of it lives here.

A row's type rules (applies to, needs a date of birth, disabled) and its date rules are checked on
rows that are new or whose type or dates changed -- an old row is never re-judged by a type flag
someone set later. A row whose expiry carries a Financial Implication blocks invoicing (and paying a
supplier) once it is past its grace days, on any master, because the row itself says so.

Imports, patches, installs, ERPNext's own "ignore mandatory" creators (the Opening Invoice Creation
Tool) and opening invoices stand aside; the report finds what they leave incomplete.
"""

from __future__ import annotations

import frappe
from frappe import _
from frappe.utils import add_days, cint, cstr, get_datetime, getdate, nowdate

from sf_trading.party_completeness import is_b2b_customer, only_status_fields_changed

SETTINGS = "SF Trading Settings"
TABLE = "custom_supporting_documents"
ROW_DOCTYPE = "Customer Supporting Document"
TYPE_DOCTYPE = "Supporting Document Type"
APPLIES_TO_BOTH = "Customer and Supplier"
PARTY_DOCTYPES = ("Customer", "Supplier")
ROW_RULE_FIELDS = ("document_type", "validate_expiry", "issue_date", "expiry_date", "date_of_birth")

#: buying documents and supplier payments are refused for a supplier that is not complete
SUPPLIER_SIDE = ("Purchase Order", "Purchase Receipt", "Purchase Invoice", "Supplier Quotation", "Payment Entry")
#: selling documents are refused for a customer that is not complete -- receiving money never is
CUSTOMER_SIDE = ("Sales Order", "Sales Invoice")

#: name, applies to, validate expiry, days ahead, grace days, financial implication, needs date of birth
DOCUMENT_TYPES = (
	("Commercial Registration", APPLIES_TO_BOTH, 1, 30, 0, 0, 0),
	("VAT Certificate", APPLIES_TO_BOTH, 0, 0, 0, 0, 0),
	("Trade License", APPLIES_TO_BOTH, 1, 30, 0, 0, 0),
	("National ID / CPR", APPLIES_TO_BOTH, 1, 30, 0, 0, 1),
	("Passport", APPLIES_TO_BOTH, 1, 60, 0, 0, 1),
	("Bank Letter / IBAN Certificate", APPLIES_TO_BOTH, 0, 0, 0, 0, 0),
	("Contract / Agreement", APPLIES_TO_BOTH, 1, 30, 0, 0, 0),
	("Credit Application", "Customer", 0, 0, 0, 0, 0),
	("Other", APPLIES_TO_BOTH, 0, 0, 0, 0, 0),
)


def seed_document_types():
	"""after_install / after_migrate: the standard Supporting Document Types, never overwriting one."""
	if not frappe.db.table_exists(TYPE_DOCTYPE):
		return
	for name, applies_to, validate, ahead, grace, financial, dob in DOCUMENT_TYPES:
		if frappe.db.exists(TYPE_DOCTYPE, name):
			continue
		frappe.get_doc({"doctype": TYPE_DOCTYPE, "document_name": name, "applies_to": applies_to,
			"validate_expiry": validate, "days_ahead": ahead, "grace_days": grace, "financial_implication": financial,
			"needs_date_of_birth": dob}).insert(ignore_permissions=True)


#: the documents grid sits in its own full-width section at the end of each master's Settings tab
GRID_PLACE = {"Customer": "disabled", "Supplier": "release_date"}
GRID_SECTION = "custom_supporting_documents_section"


def place_documents_grid():
	"""after_migrate: a field order saved from Customize Form pins every field it lists, so a grid
	listed there would stay where it was. Move the grid and its section to the end of the Settings
	tab in any such saved order (fields it does not list follow their own insert_after)."""
	import json

	for doctype, anchor in GRID_PLACE.items():
		name = frappe.db.get_value("Property Setter", {"doc_type": doctype, "property": "field_order"}, "name")
		if not name:
			continue
		order = json.loads(frappe.db.get_value("Property Setter", name, "value") or "[]")
		if TABLE not in order and GRID_SECTION not in order:
			continue
		order = [f for f in order if f not in (TABLE, GRID_SECTION)]
		if anchor not in order:
			continue
		at = order.index(anchor) + 1
		order[at:at] = [GRID_SECTION, TABLE]
		frappe.db.set_value("Property Setter", name, "value", json.dumps(order), update_modified=False)
		frappe.clear_cache(doctype=doctype)


# ─── policy ──────────────────────────────────────────────────────────────────────────────────────


def rules_from():
	"""The moment the new-master rules start from, or None when they are off."""
	value = (frappe.db.get_singles_dict(SETTINGS) or {}).get("master_rules_from")
	return get_datetime(value) if value else None


def is_governed(doc) -> bool:
	"""Suppliers always; customers when B2B (VAT on file)."""
	if doc.doctype == "Supplier":
		return True
	return doc.doctype == "Customer" and is_b2b_customer(doc)


def is_new_master(doc) -> bool:
	"""Created at or after the rules' start (a master being created now is new)."""
	start = rules_from()
	if not start:
		return False
	if doc.is_new() or not doc.get("creation"):
		return True
	return get_datetime(doc.creation) >= start


def attached_rows(doc) -> list:
	return [row for row in doc.get(TABLE) or [] if cstr(row.get("attachment")).strip()]


def has_mobile(doc) -> bool:
	if cstr(doc.get("custom_mobile_no")).strip() or cstr(doc.get("mobile_no")).strip():
		return True
	if doc.is_new():
		return False
	from sf_trading.party_contact_cache import party_phone_numbers

	return bool(party_phone_numbers(doc.doctype, doc.name))


def missing_details(doc, after_insert: bool) -> list:
	"""What a governed new master still lacks. `after_insert` adds what only an existing master can
	carry (an attached supporting document, a supplier's mobile)."""
	missing = []
	if not doc.get("payment_terms"):
		missing.append(_("Payment Terms"))
	if after_insert:
		if not attached_rows(doc):
			missing.append(_("a Supporting Document with its file attached"))
		if doc.doctype == "Supplier" and not has_mobile(doc):
			missing.append(_("Mobile Number"))
	return missing


def stands_aside(doc) -> bool:
	"""A data import, patch, install, or ERPNext's own ignore-mandatory creator: masters arrive in
	bulk and are completed afterwards (the Master Data Completeness report finds them)."""
	flags = frappe.flags
	return bool(flags.in_import or flags.in_patch or flags.in_install or flags.in_migrate
		or doc.flags.get("ignore_mandatory"))


# ─── the master's own save ───────────────────────────────────────────────────────────────────────


def _types(rows) -> dict:
	names = list({r.document_type for r in rows if r.get("document_type")})
	if not names:
		return {}
	return {
		t.name: t
		for t in frappe.get_all(TYPE_DOCTYPE, filters={"name": ["in", names]},
			fields=["name", "applies_to", "needs_date_of_birth", "disabled"])
	}


def _changed_rows(doc) -> list:
	"""Rows that are new, or whose type or dates changed since the last save."""
	getter = getattr(doc, "get_doc_before_save", None)
	before = getter() if callable(getter) else None
	old = {r.name: r for r in (before.get(TABLE) or [])} if before else {}
	changed = []
	for row in doc.get(TABLE) or []:
		was = old.get(row.get("name"))
		if row.is_new() or not was or any(cstr(row.get(f)) != cstr(was.get(f)) for f in ROW_RULE_FIELDS):
			changed.append(row)
	return changed


def validate_rows(doc):
	"""New or changed supporting-document rows: dates in order, expiry where it is validated, date
	of birth where the type asks for it, and an enabled type meant for this kind of party."""
	for row in doc.get(TABLE) or []:
		if not cint(row.get("validate_expiry")):
			row.financial_implication = 0
	rows = _changed_rows(doc)
	types = _types(rows)
	for row in rows:
		label = _("Supporting Documents row #{0}").format(row.idx)
		kind = types.get(row.get("document_type"))
		if kind and kind.applies_to not in (APPLIES_TO_BOTH, doc.doctype):
			frappe.throw(_("{0}: {1} is a {2} document.").format(label, frappe.bold(row.document_type), _(kind.applies_to)))
		if kind and cint(kind.disabled):
			frappe.throw(_("{0}: {1} is disabled.").format(label, frappe.bold(row.document_type)))
		if cint(row.get("validate_expiry")) and not row.get("expiry_date"):
			frappe.throw(_("{0}: {1} validates its expiry, so it needs an Expiry Date.").format(label, row.document_type))
		if row.get("issue_date") and row.get("expiry_date") and getdate(row.issue_date) > getdate(row.expiry_date):
			frappe.throw(_("{0}: the Issue Date is after the Expiry Date.").format(label))
		if kind and cint(kind.needs_date_of_birth) and not row.get("date_of_birth"):
			frappe.throw(_("{0}: {1} needs the holder's Date of Birth.").format(label, row.document_type))


def validate_supplier_tax_id(doc):
	"""A Company supplier carries a Tax ID (the server side of the Company-only required mark)."""
	if doc.doctype == "Supplier" and doc.get("supplier_type") == "Company" and not cstr(doc.get("tax_id")).strip():
		frappe.throw(_("Tax ID is mandatory for Supplier {0} (Supplier Type: Company).").format(
			frappe.bold(doc.get("supplier_name") or doc.name)), title=_("Mandatory Fields Missing"))


def validate_master(doc, _method=None):
	"""Customer / Supplier validate: row shape and a Company supplier's Tax ID on every master; the
	full rule on governed new ones."""
	if stands_aside(doc) or only_status_fields_changed(doc):
		return
	validate_rows(doc)
	validate_supplier_tax_id(doc)
	if not is_governed(doc) or not is_new_master(doc):
		return
	after_insert = not doc.is_new() and not doc.flags.get("in_insert")
	missing = missing_details(doc, after_insert)
	if missing:
		frappe.throw(
			_("{0} {1} needs: {2}.").format(_(doc.doctype), frappe.bold(doc.get("customer_name") or doc.get("supplier_name") or doc.name),
				", ".join(missing)),
			title=_("Master Details Missing"),
		)


def relink_attachments(doc, _method=None):
	"""Customer / Supplier on_update: a file a create dialog uploaded (unattached) belongs to the
	master its row names. (A grid upload on an unsaved form is relinked by Frappe itself.)"""
	for row in attached_rows(doc):
		url = cstr(row.attachment).strip()
		if not url.startswith(("/files", "/private/files")):
			continue
		loose = frappe.db.get_value("File", {"file_url": url, "attached_to_name": ["is", "not set"],
			"owner": frappe.session.user}, "name")
		if loose:
			frappe.db.set_value("File", loose, {"attached_to_doctype": doc.doctype, "attached_to_name": doc.name},
				update_modified=False)


def set_onload(doc, _method=None):
	"""Customer / Supplier onload: tell the form which rules apply and what is still missing."""
	applies = is_governed(doc) and is_new_master(doc)
	doc.set_onload("sf_master_rules", {
		"applies": applies,
		"rules_from": str(rules_from() or ""),
		"missing": missing_details(doc, after_insert=True) if applies and not doc.is_new() else [],
		"expired": [r.document_type for r in expired_financial_rows(doc.doctype, doc.name)] if not doc.is_new() else [],
	})


@frappe.whitelist()
def get_document_type_defaults(document_type: str) -> dict:
	"""What a new row copies from its Supporting Document Type."""
	frappe.has_permission(TYPE_DOCTYPE, "read", throw=True)
	values = frappe.db.get_value(TYPE_DOCTYPE, document_type,
		["validate_expiry", "days_ahead", "grace_days", "financial_implication", "needs_date_of_birth"], as_dict=True)
	return values or {}


@frappe.whitelist()
def get_master_rules(doctype: str, name: str = None, customer_vat: str = None) -> dict:
	"""For a form or dialog that has no onload to read: do the new-master rules apply here?"""
	if doctype not in PARTY_DOCTYPES:
		frappe.throw(_("Only Customer and Supplier carry these rules."))
	start = rules_from()
	if name:
		frappe.has_permission(doctype, "read", doc=name, throw=True)
		doc = frappe.get_doc(doctype, name)
		return {"applies": is_governed(doc) and is_new_master(doc), "rules_from": str(start or "")}
	b2b = doctype == "Supplier" or bool(cstr(customer_vat).strip())
	return {"applies": bool(start) and b2b, "rules_from": str(start or "")}


def dialog_document_row(party_type, document_type=None, document_number=None, expiry_date=None, attachment=None):
	"""The Supporting Documents row a create dialog's (or Quick Edit's) upload becomes."""
	document_type = cstr(document_type).strip() or "Other"
	kind = frappe.db.get_value(TYPE_DOCTYPE, document_type,
		["applies_to", "validate_expiry", "days_ahead", "grace_days", "financial_implication", "disabled"], as_dict=True)
	if not kind:
		frappe.throw(_("{0} is not a Supporting Document Type.").format(frappe.bold(document_type)))
	if cint(kind.disabled) or kind.applies_to not in (APPLIES_TO_BOTH, party_type):
		frappe.throw(_("{0} cannot be used for a {1}.").format(frappe.bold(document_type), _(party_type)))
	if cint(kind.validate_expiry) and not expiry_date:
		frappe.throw(_("{0} needs its Expiry Date.").format(frappe.bold(document_type)))
	return {
		"document_type": document_type,
		"document_number": cstr(document_number).strip() or None,
		"expiry_date": expiry_date or None,
		"attachment": attachment,
		"validate_expiry": cint(kind.validate_expiry),
		"days_ahead": cint(kind.days_ahead),
		"grace_days": cint(kind.grace_days),
		"financial_implication": cint(kind.financial_implication) if cint(kind.validate_expiry) else 0,
	}


# ─── documents raised against the party ──────────────────────────────────────────────────────────


def expired_financial_rows(parenttype, party) -> list:
	"""Rows whose expiry blocks business: validated, Financial Implication ticked, past grace days."""
	if not party or not frappe.db.has_column(ROW_DOCTYPE, "financial_implication"):
		return []
	today = getdate(nowdate())
	rows = frappe.get_all(ROW_DOCTYPE, filters={"parenttype": parenttype, "parent": party, "parentfield": TABLE,
		"validate_expiry": 1, "financial_implication": 1, "expiry_date": ["is", "set"]},
		fields=["document_type", "document_number", "expiry_date", "grace_days"])
	return [r for r in rows if add_days(getdate(r.expiry_date), cint(r.grace_days)) < today]


def _party_of(doc):
	if doc.doctype == "Payment Entry":
		# money received from a customer is never refused; paying a supplier is
		return ("Supplier", doc.party) if doc.get("party_type") == "Supplier" and doc.get("payment_type") == "Pay" else (None, None)
	if doc.doctype in SUPPLIER_SIDE:
		return "Supplier", doc.get("supplier")
	if doc.doctype in CUSTOMER_SIDE:
		return "Customer", doc.get("customer")
	return None, None


def validate_party_at_transaction(doc, _method=None):
	"""Buying documents, supplier payments (validate) and selling documents (before_submit): the
	party must be complete if it is a governed new master, and must hold no expired document that
	carries a Financial Implication. Returns and opening invoices are never refused."""
	if doc.get("is_return") or doc.get("is_opening") == "Yes":
		return
	party_type, party = _party_of(doc)
	if not party or not frappe.db.exists(party_type, party):
		return

	problems = []
	master = frappe.get_doc(party_type, party)
	if is_governed(master) and is_new_master(master):
		problems += missing_details(master, after_insert=True)
	for row in expired_financial_rows(party_type, party):
		problems.append(_("{0} {1} expired on {2}").format(row.document_type, row.document_number or "",
			frappe.format_value(row.expiry_date, "Date")).replace("  ", " "))
	if problems:
		frappe.throw(
			_("{0} {1} is not complete: {2}. Update the {0} first.").format(
				_(party_type), frappe.bold(master.get("customer_name") or master.get("supplier_name") or party),
				", ".join(problems)),
			title=_("{0} Details Incomplete").format(_(party_type)),
		)
