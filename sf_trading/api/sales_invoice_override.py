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
	"""Block new credit invoice if customer has overdue unsettled credit."""
	if doc.is_return:
		return
	if doc.custom_payment_mode != "Credit":
		return
	if not doc.customer:
		return

	inv = _get_overdue_invoice(doc.customer, doc.company)
	if inv:
		frappe.throw(
			_(
				"Cannot create a new credit invoice for {0}. "
				"Invoice {1} dated {2} has an outstanding amount of {3} "
				"that is overdue. Please settle the outstanding balance first."
			).format(
				doc.customer,
				inv.name,
				inv.posting_date,
				frappe.utils.fmt_money(inv.outstanding_amount, currency=doc.currency),
			)
		)


def _get_overdue_invoice(customer, company):
	"""Return the oldest overdue credit invoice for the customer, or None.

	Uses the Credit Days configured on the Customer Credit Limit row for the
	given company. Returns None if no credit days are set (validation disabled).
	"""
	credit_days = frappe.db.get_value(
		"Customer Credit Limit",
		{"parent": customer, "company": company},
		"custom_credit_days",
	)
	if not credit_days:
		return None

	rows = frappe.db.sql(
		"""
		SELECT name, posting_date, outstanding_amount
		FROM `tabSales Invoice`
		WHERE customer = %s
		  AND company = %s
		  AND custom_payment_mode = 'Credit'
		  AND docstatus = 1
		  AND outstanding_amount > 0
		  AND DATEDIFF(%s, posting_date) > %s
		ORDER BY posting_date ASC
		LIMIT 1
		""",
		(customer, company, today(), frappe.utils.cint(credit_days)),
		as_dict=True,
	)
	return rows[0] if rows else None


@frappe.whitelist()
def check_customer_credit_overdue(customer, company):
	"""Client-side check: return the oldest overdue invoice dict or None."""
	return _get_overdue_invoice(customer, company)
