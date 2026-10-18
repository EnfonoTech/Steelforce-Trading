# sf_trading/overrides/landed_cost_voucher_class.py
"""Landed Cost Voucher, with one of core's rules relaxed: a draft voucher may name a draft stock invoice.

Core's `validate_receipt_documents` refuses any receipt document that is not submitted. The voucher of an
import purchase is drawn up while the invoice is still a draft and submitted by the invoice's own approval
(sf_trading/landed_cost_with_invoice.py), so for exactly that case the rule is replaced:

* the voucher is itself a draft (a voucher being submitted goes to core, which then insists the invoice is
  submitted -- it is, by then -- and a clearer message is given if it is not),
* it names ONE document, a Purchase Invoice that is still a draft and updates stock,
* the company agrees, and every item row belongs to that invoice and carries a cost centre.

Everything else -- submitted receipts, Purchase Receipts, several documents on one voucher -- is core's own
method, untouched. `tests/test_lcv_with_invoice.py` fails if a future ERPNext changes the wording core's
checks rest on, so a drift is noticed rather than silently weakening a control.
"""

import frappe
from frappe import _
from frappe.utils import cint

from erpnext.stock.doctype.landed_cost_voucher.landed_cost_voucher import (
	IncorrectCompanyValidationError,
	LandedCostVoucher,
)

INVOICE = "Purchase Invoice"


class CustomLandedCostVoucher(LandedCostVoucher):
	def validate_receipt_documents(self):
		rows = self.get("purchase_receipts") or []
		drafts = [
			row
			for row in rows
			if row.get("receipt_document_type") == INVOICE
			and row.get("receipt_document")
			and cint(frappe.db.get_value(INVOICE, row.receipt_document, "docstatus")) == 0
		]
		if not drafts:
			return super().validate_receipt_documents()

		if cint(self.docstatus) != 0:
			frappe.throw(
				_("Row %s: Purchase Invoice %s is still a draft. This voucher is submitted with the invoice, when it is approved.")
				% (drafts[0].idx, frappe.bold(drafts[0].receipt_document)),
				title=_("Invoice Not Submitted"),
			)
		if len(rows) != 1:
			frappe.throw(
				_("A voucher drawn up against a draft Purchase Invoice names that invoice alone."),
				title=_("One Invoice Only"),
			)
		row = rows[0]
		company, update_stock = frappe.db.get_value(INVOICE, row.receipt_document, ["company", "update_stock"])
		if company != self.company:
			frappe.throw(
				_("Row %s: %s %s is linked to company %s. Please select a document belonging to company %s.")
				% (row.idx, row.receipt_document_type, frappe.bold(row.receipt_document), frappe.bold(company), frappe.bold(self.company)),
				title=_("Incorrect Company"),
				exc=IncorrectCompanyValidationError,
			)
		if not cint(update_stock):
			frappe.throw(
				_("Row %s: Purchase Invoice %s has no stock impact.") % (row.idx, frappe.bold(row.receipt_document))
				+ "<br>"
				+ _("Please create Landed Cost Vouchers against Invoices that have 'Update Stock' enabled."),
				title=_("Incorrect Invoice"),
			)
		for item in self.get("items"):
			if not item.receipt_document:
				frappe.throw(_("Item must be added using 'Get Items from Purchase Receipts' button"))
			elif item.receipt_document != row.receipt_document:
				frappe.throw(
					_("Item Row %s: %s %s does not exist in above '%s' table")
					% (item.idx, item.receipt_document_type, item.receipt_document, item.receipt_document_type)
				)
			if not item.cost_center:
				frappe.throw(_("Row %s: Cost center is required for an item %s") % (item.idx, item.item_code))
