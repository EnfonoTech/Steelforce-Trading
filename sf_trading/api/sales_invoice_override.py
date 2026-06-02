"""
Sales Invoice overrides: remove empty item rows before validation (barcode scanner scan row).
"""

from __future__ import annotations

import frappe
from frappe import _
from frappe.utils import today


def before_validate(doc, _method=None):
	"""Remove item rows that have no item_code (leftover scan row from barcode). Runs before validation."""
	if not doc.get("items"):
		frappe.throw(_("Please add at least one item before saving."))

	# Remove in reverse so indices stay valid
	to_remove = [row for row in doc.items if not (row.get("item_code") or "").strip()]
	for row in to_remove:
		doc.remove(row)
	for i, row in enumerate(doc.items, start=1):
		row.idx = i

	if not doc.items:
		frappe.throw(_("Please add at least one item before saving."))


def validate(doc, _method=None):
	"""Block new credit invoice if customer has overdue unsettled credit (> 30 days)."""
	if doc.custom_payment_mode != "Credit":
		return
	if not doc.customer:
		return

	overdue = frappe.db.sql(
		"""
		SELECT name, posting_date, outstanding_amount
		FROM `tabSales Invoice`
		WHERE customer = %s
		  AND company = %s
		  AND custom_payment_mode = 'Credit'
		  AND docstatus = 1
		  AND outstanding_amount > 0
		  AND DATEDIFF(%s, posting_date) > 30
		LIMIT 1
		""",
		(doc.customer, doc.company, today()),
		as_dict=True,
	)

	if overdue:
		inv = overdue[0]
		frappe.throw(
			_(
				"Cannot create a new credit invoice for {0}. "
				"Invoice {1} dated {2} has an outstanding amount of {3} "
				"that is more than 30 days overdue. "
				"Please settle the outstanding balance first."
			).format(
				doc.customer,
				inv.name,
				inv.posting_date,
				frappe.utils.fmt_money(inv.outstanding_amount, currency=doc.currency),
			)
		)
