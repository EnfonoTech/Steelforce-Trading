"""Payment Advice with open debit / credit notes, journals already paid, and unapplied advances.

    bench --site <scratch-site> run-tests --module sf_trading.tests.test_payment_advice_credit_notes
"""

from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import flt, nowdate

import sf_trading.sf_trading.doctype.payment_advice.payment_advice as pa

PI = "Purchase Invoice"


class TestCreditNotes(FrappeTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		cls.company = frappe.db.get_value("Company", {}, "name")

	def _advice(self, rows, payment_amount):
		advice = frappe.new_doc("Payment Advice")
		advice.update({"company": self.company, "party_type": "Supplier", "transaction_date": nowdate(),
			"payment_amount": payment_amount})
		for i, payable in enumerate(rows):
			advice.append("payment_advice_reference", {"reference_doctype": PI,
				"reference_record": "PI-%s" % i, "net_payable_amount": payable})
		return advice

	def _allocations(self, advice):
		return [flt(r.allocated_amount) for r in advice.payment_advice_reference]

	def test_a_debit_note_nets_the_payment(self):
		advice = self._advice([500, 200, -65], 635)
		advice.compute_totals()
		self.assertEqual(flt(advice.amount_to_be_settled), 635)
		advice.validate_payment_amount()
		advice.allocate_payment()
		self.assertEqual(self._allocations(advice), [500, 200, -65])
		self.assertEqual(flt(sum(self._allocations(advice)), 3), flt(advice.payment_amount))

	def test_a_part_payment_still_sets_the_whole_note_off(self):
		advice = self._advice([500, 200, -65], 300)
		advice.compute_totals()
		advice.validate_payment_amount()
		advice.allocate_payment()
		# 300 of cash plus the 65 note settles 365 of invoices, oldest row first
		self.assertEqual(self._allocations(advice), [365, 0, -65])
		self.assertEqual(flt(advice.payment_amount), 300)

	def test_an_over_large_payment_is_trimmed_to_the_net(self):
		advice = self._advice([500, -65], 900)
		advice.compute_totals()
		advice.validate_payment_amount()
		self.assertEqual(flt(advice.payment_amount), 435)

	def test_notes_that_cover_everything_are_refused_with_the_reason(self):
		advice = self._advice([50, -65], 10)
		advice.compute_totals()
		with self.assertRaises(frappe.ValidationError) as caught:
			advice.validate_payment_amount()
		self.assertIn("Payment Reconciliation", str(caught.exception))

	def test_a_negative_order_row_is_not_a_note(self):
		row = frappe._dict(reference_doctype="Purchase Order", net_payable_amount=-5)
		self.assertFalse(pa.is_credit_row(row))
		self.assertTrue(pa.is_credit_row(frappe._dict(reference_doctype=PI, net_payable_amount=-5)))

	def test_route_reads_what_is_settled_not_the_net_cash(self):
		advice = frappe._dict(payment_amount=300, payment_advice_reference=[
			frappe._dict(reference_doctype=PI, reference_record="PI-A", allocated_amount=4800),
			frappe._dict(reference_doctype=PI, reference_record="PI-B", allocated_amount=-4500),
		])
		self.assertEqual(pa.route_amount(advice), 4800)
		with patch.object(pa, "_has_overdue_invoice", return_value=False):
			self.assertEqual(pa.compute_approval_route(advice), pa.ROUTE_FINANCE)

	def test_outstanding_rows_keep_notes_after_the_invoices_and_outside_the_amount_window(self):
		vouchers = [
			{"voucher_type": PI, "voucher_no": "PI-RET", "invoice_amount": -65, "outstanding_amount": -65,
				"posting_date": "2026-09-01", "due_date": "2026-09-01"},
			{"voucher_type": PI, "voucher_no": "PI-1", "invoice_amount": 500, "outstanding_amount": 500,
				"posting_date": "2026-08-01", "due_date": "2026-08-01"},
			{"voucher_type": "Purchase Order", "voucher_no": "PO-NEG", "invoice_amount": 10, "outstanding_amount": -1,
				"posting_date": "2026-08-01"},
		]
		with patch.object(frappe.db, "get_value", return_value=frappe._dict()):
			rows = pa.shape_reference_rows(vouchers, from_amount=100)
		self.assertEqual([r["reference_record"] for r in rows], ["PI-1", "PI-RET"])
		self.assertEqual(rows[1]["net_payable_amount"], -65)

	def test_payment_entry_carries_the_negative_allocation(self):
		advice = self._advice([500, -65], 435)
		advice.update({"party": "SUP-X", "mode_of_payment": None, "name": "PA-TEST"})
		advice.payment_advice_reference[0].allocated_amount = 500
		advice.payment_advice_reference[1].allocated_amount = -65
		with patch.object(pa, "resolve_party_account", return_value="Creditors"), patch.object(
			pa, "get_company_account", return_value="Bank"
		), patch("erpnext.controllers.accounts_controller.get_supplier_block_status", return_value={}), patch.object(
			frappe.db, "get_value", side_effect=lambda *a, **k: -65 if a[-1] == "outstanding_amount" else "BHD"
		), patch.object(pa, "get_company_currency", return_value="BHD"):
			pe = pa.build_payment_entry(advice)
		self.assertEqual([flt(r.allocated_amount) for r in pe.references], [500, -65])
		self.assertEqual(flt(pe.paid_amount), 435)

	def test_a_note_used_up_since_approval_is_named(self):
		advice = self._advice([500, -65], 435)
		advice.update({"party": "SUP-X", "name": "PA-TEST"})
		advice.payment_advice_reference[0].allocated_amount = 500
		advice.payment_advice_reference[1].allocated_amount = -65
		with patch.object(frappe.db, "get_value", return_value=-20):
			with self.assertRaises(frappe.ValidationError) as caught:
				pa.build_payment_entry(advice)
		self.assertIn("PI-1", str(caught.exception))


class TestJournalAndAdvances(FrappeTestCase):
	def test_journal_payable_falls_as_it_is_paid(self):
		with patch.object(frappe, "get_all", return_value=[frappe._dict(amount=300), frappe._dict(amount=-300)]):
			self.assertEqual(pa.journal_outstanding("JV-1"), 0)
		with patch.object(frappe, "get_all", return_value=[]):
			self.assertIsNone(pa.journal_outstanding("JV-1"))

	def test_unapplied_advances_query_runs(self):
		company = frappe.db.get_value("Company", {}, "name")
		supplier = frappe.db.get_value("Supplier", {}, "name")
		rows = pa.unapplied_advances(company, "Supplier", supplier)
		self.assertIsInstance(rows, list)
		self.assertTrue(all(flt(r.amount) > 0 for r in rows))
		self.assertEqual(pa.unapplied_advances(None, "Supplier", supplier), [])
