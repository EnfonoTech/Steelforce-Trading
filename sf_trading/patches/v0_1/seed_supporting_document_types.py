# sf_trading/patches/v0_1/seed_supporting_document_types.py
"""Carry the existing supporting-document rows onto Supporting Document Type.

The documents grid's "Document Name" used to be free text; it is now a link. The standard types are
seeded first (party_documents.seed_document_types, also run on every migrate and install), then
every text already typed into a row becomes a type of its own, so no existing row breaks its link.
A typed value is trimmed, and the row is rewritten to the trimmed name; one that cannot be a name
at all maps to "Other". Rows that carry an expiry date are marked Validate, so the expiry reminders
they already received keep coming.

Idempotent: an existing type is never touched, and the Validate flag is only set on rows that have
an expiry date and the flag still off.
"""

import frappe

from sf_trading.party_documents import APPLIES_TO_BOTH, ROW_DOCTYPE, TYPE_DOCTYPE, seed_document_types


def execute():
	if not frappe.db.table_exists(TYPE_DOCTYPE):
		return
	seed_document_types()

	for row in frappe.get_all(ROW_DOCTYPE, fields=["name", "document_type"]):
		typed = row.document_type or ""
		name = typed.strip()
		if not name:
			continue
		if not frappe.db.exists(TYPE_DOCTYPE, name):
			try:
				frappe.get_doc({"doctype": TYPE_DOCTYPE, "document_name": name, "applies_to": APPLIES_TO_BOTH,
					"validate_expiry": 1}).insert(ignore_permissions=True)
			except (frappe.NameError, frappe.ValidationError):
				frappe.clear_last_message()
				name = "Other"
		if name != typed:
			frappe.db.set_value(ROW_DOCTYPE, row.name, "document_type", name, update_modified=False)

	for row in frappe.get_all(ROW_DOCTYPE, filters={"expiry_date": ["is", "set"], "validate_expiry": 0}, pluck="name"):
		frappe.db.set_value(ROW_DOCTYPE, row, "validate_expiry", 1, update_modified=False)

	frappe.db.commit()
