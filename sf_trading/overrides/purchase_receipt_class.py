# sf_trading/overrides/purchase_receipt_class.py
"""Purchase Receipt, as ERPNext's, except that a return needs no Purchase Order.

Core's validate calls po_required() on every receipt, return or not, so with Buying Settings >
Purchase Order Required = Yes (switched on 2026-10-01) a return against a receipt with no order
behind it -- an ePromise-era GRN, or anything received before the setting -- could not be raised at
all ("Purchase Order number required for Item ..."). A return goes back against its own receipt,
not an order; the Purchase Invoice already stands aside for a debit note the same way (erpnext
purchase_invoice.validate: `if not self.is_return: self.po_required()`).
"""

from erpnext.stock.doctype.purchase_receipt.purchase_receipt import PurchaseReceipt
from frappe.utils import cint


class CustomPurchaseReceipt(PurchaseReceipt):
	def po_required(self):
		if cint(self.get("is_return")):
			return
		super().po_required()
