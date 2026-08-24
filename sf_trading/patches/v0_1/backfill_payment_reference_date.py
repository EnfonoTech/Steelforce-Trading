# apps/sf_trading/sf_trading/patches/v0_1/backfill_payment_reference_date.py
"""Fill `custom_reference_date` on Payment Entry reference rows that already exist.

The field is stamped on validate from now on (sf_trading/payment_entry_reference_date.py), but
every reference row already in the database was written before the field existed. Without this
the column reads blank on exactly the historical payments people go looking at.

Grouped by date rather than updated row by row: the rows are read in one query per referenced
doctype, bucketed by the voucher's own date, and written with one UPDATE per bucket. A site
with thirty thousand reference rows across six hundred posting dates costs six hundred
statements instead of thirty thousand.

Idempotent -- only rows whose value is still NULL are read, so a replay finds nothing to do.
"""

import frappe

from sf_trading.payment_entry_reference_date import FIELD, date_field_for

CHUNK = 500


def execute():
	if not frappe.db.has_column("Payment Entry Reference", FIELD):
		# the after_migrate hook creates it; nothing to fill if it has not run yet
		return

	doctypes = frappe.db.get_all(
		"Payment Entry Reference",
		filters={"reference_doctype": ["is", "set"], FIELD: ["is", "not set"]},
		distinct=True,
		pluck="reference_doctype",
	)

	table = frappe.qb.DocType("Payment Entry Reference")
	for doctype in doctypes:
		date_field = date_field_for(doctype)
		if not date_field:
			continue

		rows = frappe.get_all(
			"Payment Entry Reference",
			filters={"reference_doctype": doctype, FIELD: ["is", "not set"]},
			fields=["name", "reference_name"],
			ignore_permissions=True,
		)
		if not rows:
			continue

		names = {row.reference_name for row in rows if row.reference_name}
		dates = {}
		for chunk in _chunks(sorted(names)):
			for record in frappe.get_all(
				doctype,
				filters={"name": ["in", chunk]},
				fields=["name", date_field + " as reference_date"],
				ignore_permissions=True,
			):
				dates[record.name] = record.reference_date

		buckets = {}
		for row in rows:
			value = dates.get(row.reference_name)
			if value:
				buckets.setdefault(str(value), []).append(row.name)

		for value, row_names in buckets.items():
			for chunk in _chunks(row_names):
				(
					frappe.qb.update(table)
					.set(table[FIELD], value)
					.where(table.name.isin(chunk))
				).run()

		frappe.db.commit()


def _chunks(items):
	for start in range(0, len(items), CHUNK):
		yield items[start : start + CHUNK]
