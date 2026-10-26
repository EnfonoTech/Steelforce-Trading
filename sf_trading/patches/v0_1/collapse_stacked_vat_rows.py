# sf_trading/patches/v0_1/collapse_stacked_vat_rows.py
"""Collapse the VAT Certificate rows the Customer / Supplier form stacked (0.1.75).

The VAT Registration Number field fires on every edit, and each edit added a VAT Certificate row
carrying the number as typed so far (22, 2200, 22000, ...), the old ones kept. Per party: the
VAT Certificate rows that hold only a number (no file, dates, remarks or date of birth) and a number
other than the party's current one are deleted. A row with anything else on it is a real document and
stays; a party with no row for its current number keeps one placeholder, retyped.
"""

import frappe
from frappe.utils import cstr

ROW_TABLE = "Customer Supporting Document"
VAT_TYPE = "VAT Certificate"
VAT_FIELD = {"Customer": "custom_vat_registration_number", "Supplier": "tax_id"}
EXTRA = ("attachment", "issue_date", "expiry_date", "remarks", "date_of_birth")


def execute():
	if not frappe.db.table_exists(ROW_TABLE):
		return
	rows = frappe.get_all(
		ROW_TABLE,
		filters={"document_type": VAT_TYPE},
		fields=["name", "parent", "parenttype", "document_number", *EXTRA],
		order_by="parent asc, idx asc",
	)
	by_party = {}
	for row in rows:
		if row.parenttype in VAT_FIELD:
			by_party.setdefault((row.parenttype, row.parent), []).append(row)

	for (doctype, party), party_rows in by_party.items():
		if len(party_rows) < 2:
			continue
		vat = cstr(frappe.db.get_value(doctype, party, VAT_FIELD[doctype])).strip()
		if not vat:
			continue
		loose = [r for r in party_rows if not any(cstr(r.get(f)).strip() for f in EXTRA)]
		keep = next((r for r in loose if cstr(r.document_number).strip() == vat), None)
		if not keep and vat and loose and not any(cstr(r.document_number).strip() == vat for r in party_rows):
			keep = loose[-1]
			frappe.db.set_value(ROW_TABLE, keep.name, "document_number", vat, update_modified=False)
		for row in loose:
			if row is not keep and cstr(row.document_number).strip() != vat:
				frappe.db.delete(ROW_TABLE, {"name": row.name})

		for idx, name in enumerate(
			frappe.get_all(ROW_TABLE, filters={"parent": party, "parenttype": doctype}, order_by="idx asc", pluck="name"),
			start=1,
		):
			frappe.db.set_value(ROW_TABLE, name, "idx", idx, update_modified=False)
	frappe.db.commit()
