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
		# Cost center & warehouse: from Inter Company Branch if selected, else company default
		branch_data = _get_branch_data(doc, pi.company)
		if branch_data.get("cost_center"):
			pi.cost_center = branch_data["cost_center"]
			for item in pi.items:
				if hasattr(item, "cost_center"):
					item.cost_center = branch_data["cost_center"]
		if branch_data.get("warehouse"):
			pi.set_warehouse = branch_data["warehouse"]
			for item in pi.items:
				if hasattr(item, "warehouse"):
					item.warehouse = branch_data["warehouse"]
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


def _get_branch_data(doc, buying_company: str) -> dict:
	"""Get cost_center and warehouse for PI from Inter Company Branch, else company defaults."""
	import erpnext

	result = {}
	branch = doc.get("inter_company_branch")
	if branch and buying_company:
		row = frappe.db.get_value(
			"Inter Company Branch Cost Center",
			{"parent": branch, "company": buying_company},
			["cost_center", "warehouse"],
			as_dict=True,
		)
		if row:
			if row.cost_center:
				result["cost_center"] = row.cost_center
			if row.warehouse:
				result["warehouse"] = row.warehouse
	if "cost_center" not in result:
		result["cost_center"] = erpnext.get_default_cost_center(buying_company)
	return result
