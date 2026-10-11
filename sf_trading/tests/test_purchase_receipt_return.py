# sf_trading/tests/test_purchase_receipt_return.py
"""A Purchase Receipt return needs no Purchase Order (sf_trading/overrides/purchase_receipt_class.py).

    bench --site <site> run-tests --module sf_trading.tests.test_purchase_receipt_return
"""

from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase

from sf_trading.overrides.purchase_receipt_class import CustomPurchaseReceipt


def _po_required(value):
	real = frappe.db.get_single_value

	def get_single_value(doctype, fieldname, *args, **kwargs):
		# answer only the one setting; everything else stays real
		if doctype == "Buying Settings" and fieldname == "po_required":
			return value
		return real(doctype, fieldname, *args, **kwargs)

	return patch.object(frappe.db, "get_single_value", side_effect=get_single_value)


class TestPurchaseReceiptReturn(FrappeTestCase):
	def _receipt(self, is_return):
		doc = frappe.new_doc("Purchase Receipt")
		doc.is_return = is_return
		doc.append("items", {"item_code": "_SF Test Item", "qty": -1 if is_return else 1})
		return doc

	def test_the_override_is_the_class_in_use(self):
		self.assertIsInstance(frappe.new_doc("Purchase Receipt"), CustomPurchaseReceipt)

	def test_a_return_needs_no_purchase_order(self):
		with _po_required("Yes"):
			self._receipt(is_return=1).po_required()

	def test_a_receipt_still_needs_its_order(self):
		with _po_required("Yes"), self.assertRaises(frappe.ValidationError):
			self._receipt(is_return=0).po_required()
