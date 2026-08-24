# sf_trading/payment_entry_reference_date.py
"""Show, on a Payment Entry reference row, the date of the document it is paying.

The References table names the invoice and its outstanding but not its date, so answering
"which month's invoice is this payment settling?" meant opening every reference in turn. The
date is on the referenced document; this copies it onto the row.

`due_date` is already there and is not the same thing -- a payment term can put the due date
in a different month from the invoice, and an order has no due date at all.

The field is filled on `validate`, so it is written once with the row and never has to be kept
in step afterwards: the posting date of a submitted voucher does not move. Historical rows are
filled by the patch `v0_1/backfill_payment_reference_date`, which is why the field carries
`allow_on_submit` -- without it a submitted Payment Entry could not accept the value at all.

`validate` is not the only way a reference row appears, though. When an invoice consumes an
advance, ERPNext reaches into the **already submitted** payment and adds a row to it
(`erpnext.accounts.utils.update_reference_in_payment_entry`) -- an update-after-submit, which
runs `validate_update_after_submit` and `on_update_after_submit` and never `validate`. Payment
Reconciliation does the same. Those rows arrived blank until `fill_missing_reference_dates`
caught them on the way past.
"""

import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields

FIELD = "custom_reference_date"

# A voucher's own date field. Everything transactional in ERPNext uses one of these two, so
# the map is a fallback order rather than a list of special cases: posting date if the doctype
# has one, transaction date otherwise (Sales Order, Purchase Order).
DATE_FIELDS = ("posting_date", "transaction_date")


def ensure_custom_fields():
	"""after_migrate: the date column on the references grid."""
	create_custom_fields(
		{
			"Payment Entry Reference": [
				{
					"fieldname": FIELD,
					"label": "Transaction Date",
					"fieldtype": "Date",
					"insert_after": "due_date",
					"read_only": 1,
					"in_list_view": 1,
					"allow_on_submit": 1,
					"description": "Posting / transaction date of the referenced document.",
				}
			]
		},
		ignore_validate=True,
	)


def date_field_for(doctype: str) -> str | None:
	"""Which of the two date fields this doctype actually has."""
	if not doctype or not frappe.db.exists("DocType", doctype):
		return None
	meta = frappe.get_meta(doctype)
	for fieldname in DATE_FIELDS:
		if meta.get_field(fieldname):
			return fieldname
	return None


def reference_dates(rows) -> dict:
	"""{(doctype, name): date} for the reference rows, one query per doctype."""
	wanted = {}
	for row in rows:
		if not (row.get("reference_doctype") and row.get("reference_name")):
			continue
		wanted.setdefault(row.reference_doctype, set()).add(row.reference_name)

	found = {}
	for doctype, names in wanted.items():
		date_field = date_field_for(doctype)
		if not date_field:
			continue
		for record in frappe.get_all(
			doctype,
			filters={"name": ["in", list(names)]},
			fields=["name", date_field + " as reference_date"],
			ignore_permissions=True,
		):
			found[(doctype, record.name)] = record.reference_date

	return found


def set_reference_dates(doc, method=None):
	"""validate on Payment Entry: stamp each reference row with its document's date."""
	rows = doc.get("references") or []
	if not rows:
		return

	dates = reference_dates(rows)
	for row in rows:
		row.set(FIELD, dates.get((row.reference_doctype, row.reference_name)))


def fill_missing_reference_dates(doc, method=None):
	"""on_update_after_submit: catch the rows that never passed validate.

	An invoice allocating an advance, and Payment Reconciliation, both add reference rows to a
	payment that is already submitted. Written straight to the row: the parent is mid-save, and a
	value set on the object here would be lost with it.
	"""
	rows = [
		row
		for row in (doc.get("references") or [])
		if not row.get(FIELD) and row.get("reference_doctype") and row.get("reference_name")
	]
	if not rows:
		return

	dates = reference_dates(rows)
	for row in rows:
		value = dates.get((row.reference_doctype, row.reference_name))
		if not value:
			continue
		row.set(FIELD, value)
		if row.get("name"):
			frappe.db.set_value(
				"Payment Entry Reference", row.name, FIELD, value, update_modified=False
			)
