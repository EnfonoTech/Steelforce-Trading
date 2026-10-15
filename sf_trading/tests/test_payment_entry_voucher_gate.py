# sf_trading/tests/test_payment_entry_voucher_gate.py
"""A Pay or Receive Payment Entry must name a voucher (sf_trading/payment_entry_voucher_gate.py).

The switch is patched and the entry is a stand-in, so nothing is created:

    bench --site <site> run-tests --module sf_trading.tests.test_payment_entry_voucher_gate
"""

from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase

from sf_trading import payment_entry_voucher_gate as gate


def entry(payment_type="Pay", party_type="Supplier", references=()):
	return frappe._dict(
		payment_type=payment_type,
		party_type=party_type,
		references=[frappe._dict(reference_doctype="Purchase Invoice", reference_name=r) for r in references],
	)


class TestPaymentEntryVoucherGate(FrappeTestCase):
	def refused(self, doc, switch=True):
		with patch.object(gate.switches, "is_on", return_value=switch):
			try:
				gate.validate_has_voucher(doc)
			except frappe.ValidationError:
				return True
		return False

	def test_pay_and_receive_without_a_voucher_are_refused_for_every_party_type(self):
		for payment_type in ("Pay", "Receive"):
			for party_type in ("Supplier", "Customer", "Employee", "Shareholder"):
				self.assertTrue(self.refused(entry(payment_type, party_type)), (payment_type, party_type))

	def test_a_voucher_row_lets_it_through(self):
		self.assertFalse(self.refused(entry("Pay", "Supplier", ["PINV-1"])))
		self.assertFalse(self.refused(entry("Receive", "Customer", ["SINV-1", "SINV-2"])))

	def test_a_blank_row_is_not_a_voucher(self):
		doc = entry("Pay", "Supplier")
		doc.references.append(frappe._dict(reference_doctype="Purchase Invoice", reference_name=None))
		self.assertTrue(self.refused(doc))

	def test_an_internal_transfer_is_not_covered(self):
		self.assertFalse(self.refused(entry("Internal Transfer", None)))

	def test_the_switch_turns_it_off(self):
		self.assertFalse(self.refused(entry("Pay", "Supplier"), switch=False))
