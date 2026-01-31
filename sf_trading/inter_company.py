"""
Auto-create draft Inter Company Purchase Invoice (from Sales Invoice) on submit.
Uses built-in methods, avoids duplicates.
"""

from __future__ import annotations

import frappe
from frappe import _


def sales_invoice_on_submit(doc, method=None):
	"""Auto-create draft Inter Company Purchase Invoice on SI submit (no duplicate)."""
	if not doc.is_internal_customer or not doc.represents_company:
		return
	if frappe.db.exists(
		"Purchase Invoice",
		{"inter_company_invoice_reference": doc.name},
	):
		return

	try:
		import erpnext
		from erpnext.accounts.doctype.sales_invoice.sales_invoice import (
			make_inter_company_purchase_invoice,
		)

		pi = make_inter_company_purchase_invoice(doc.name)
		# Fetch Supplier Invoice No & Date from source Sales Invoice (built-in does not set these)
		pi.bill_no = doc.name
		pi.bill_date = doc.posting_date
		# Cost center: use Inter Company Branch if selected, else buying company's default
		target_cc = _get_pi_cost_center(doc, pi.company)
		if target_cc:
			pi.cost_center = target_cc
			for item in pi.items:
				if hasattr(item, "cost_center"):
					item.cost_center = target_cc
		pi.insert(ignore_permissions=True)
		frappe.msgprint(
			_("Inter Company Purchase Invoice {0} created as draft.").format(pi.name),
			alert=True,
		)
	except Exception as e:
		frappe.log_error(title="Inter Company PI auto-create", message=frappe.get_traceback())
		frappe.msgprint(
			_("Could not auto-create Inter Company Purchase Invoice: {0}").format(str(e)),
			indicator="orange",
			alert=True,
		)


def _get_pi_cost_center(doc, buying_company: str) -> str | None:
	"""Get cost center for PI: from Inter Company Branch if set, else company default."""
	import erpnext

	branch = doc.get("inter_company_branch")
	if branch and buying_company:
		cc = frappe.db.get_value(
			"Inter Company Branch Cost Center",
			{"parent": branch, "company": buying_company},
			"cost_center",
		)
		if cc:
			return cc
	return erpnext.get_default_cost_center(buying_company)
