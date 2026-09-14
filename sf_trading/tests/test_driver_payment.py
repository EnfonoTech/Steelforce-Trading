"""Tests for the delivery-person overdue gate, which runs on the order as well as the invoice.

The gate is a decision over a handful of header fields, so the documents here are stubs and the
lookup is patched -- what is under test is which date the gate measures from, which document it
excludes from its own search, and that an order (which has no `posting_date` at all) survives it.

    bench --site <scratch-site> run-tests --module sf_trading.tests.test_driver_payment
"""

from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase

from sf_trading.api import sales_invoice_override

DRIVER = "_Test SF Driver"
LOOKUP = "sf_trading.api.sales_invoice_override._get_driver_overdue_invoice"


class StubDoc:
	"""A document that answers like frappe's: `get` finds nothing, attribute access raises.

	`frappe._dict` hands back None for a missing attribute, which is exactly what would hide the
	bug this module regresses -- a real Document raises AttributeError, so the stub does too.
	"""

	def __init__(self, doctype, **fields):
		self.doctype = doctype
		self.__dict__.update(fields)

	def get(self, key, default=None):
		return self.__dict__.get(key, default)


def order(**overrides):
	fields = {
		"name": "SAL-ORD-2026-00057",
		"transaction_date": "2026-09-14",
		"custom_payment_mode": "Cash",
		"custom_driver": DRIVER,
		"currency": "BHD",
	}
	fields.update(overrides)
	return StubDoc("Sales Order", **fields)


def invoice(**overrides):
	fields = {
		"name": "SINV-2026-00099",
		"posting_date": "2026-09-14",
		"custom_payment_mode": "Cash",
		"custom_driver": DRIVER,
		"currency": "BHD",
		"is_return": 0,
	}
	fields.update(overrides)
	return StubDoc("Sales Invoice", **fields)


class TestDriverPayment(FrappeTestCase):
	def test_order_is_measured_from_its_transaction_date(self):
		"""A Sales Order has no `posting_date`; reading one raised AttributeError on save."""
		with patch(LOOKUP, return_value=None) as lookup:
			sales_invoice_override.validate_driver_payment(order())
		self.assertEqual(lookup.call_args.kwargs["as_of_date"], "2026-09-14")

	def test_order_excludes_nothing_from_the_invoice_search(self):
		"""The search reads `tabSales Invoice`, so an order's own name excludes nothing."""
		with patch(LOOKUP, return_value=None) as lookup:
			sales_invoice_override.validate_driver_payment(order())
		self.assertIsNone(lookup.call_args.args[1])

	def test_invoice_is_measured_from_its_posting_date_and_excludes_itself(self):
		with patch(LOOKUP, return_value=None) as lookup:
			sales_invoice_override.validate_driver_payment(invoice())
		self.assertEqual(lookup.call_args.kwargs["as_of_date"], "2026-09-14")
		self.assertEqual(lookup.call_args.args[1], "SINV-2026-00099")

	def test_an_overdue_driver_invoice_blocks_the_order(self):
		overdue = frappe._dict(
			name="SINV-2026-00001", posting_date="2026-09-01", outstanding_amount=120.5
		)
		with patch(LOOKUP, return_value=overdue), patch(
			"frappe.db.get_value", return_value=1
		), self.assertRaises(frappe.ValidationError):
			sales_invoice_override.validate_driver_payment(order())

	def test_the_three_guards_never_reach_the_lookup(self):
		for doc in (
			invoice(is_return=1),
			order(custom_payment_mode="Credit"),
			order(custom_driver=None),
		):
			with self.subTest(doctype=doc.doctype, mode=doc.get("custom_payment_mode")):
				with patch(LOOKUP) as lookup:
					sales_invoice_override.validate_driver_payment(doc)
				lookup.assert_not_called()
