"""Purchase Receipts Pending Return: debited on the invoice, never returned out of stock.

    bench --site <scratch-site> run-tests --module sf_trading.tests.test_purchase_receipts_pending_return

The allocation is pure, so it is tested on plain rows; the SQL that feeds it is checked against the
real data by running the report (on production MAT-PRE-2026-01081, invoice ACC-PINV-2026-01993: two
units debited on a debit note that moved no stock, no Purchase Receipt return).
"""

import frappe
from frappe.tests.utils import FrappeTestCase

from sf_trading.sf_trading.report.purchase_receipts_pending_return import (
	purchase_receipts_pending_return as report,
)


def line(pr_row, received, debited, returned=0.0, pi_row="PI-ROW-1", date="2026-07-27", pre=None, **extra):
	return frappe._dict(
		pr_row=pr_row,
		purchase_receipt=pre or f"PRE-{pr_row}",
		posting_date=date,
		received_qty=received,
		debited_qty=debited,
		returned_qty=returned,
		pi_row=pi_row,
		rate=10.0,
		**extra,
	)


class TestAllocatePending(FrappeTestCase):
	def test_debited_and_never_returned_is_pending(self):
		# the example: 6 received, 5 debited on the invoice, no receipt return
		row = report.allocate_pending([line("a", received=6, debited=5)])[0]
		self.assertEqual(row.pending_qty, 5)
		self.assertEqual(row.status, report.PENDING)

	def test_a_part_return_leaves_the_rest_pending(self):
		row = report.allocate_pending([line("a", received=6, debited=5, returned=2)])[0]
		self.assertEqual(row.pending_qty, 3)
		self.assertEqual(row.status, report.PENDING)

	def test_fully_returned_is_not_pending(self):
		row = report.allocate_pending([line("a", received=6, debited=5, returned=5)])[0]
		self.assertEqual(row.pending_qty, 0)
		self.assertEqual(row.status, report.RETURNED)

	def test_more_returned_than_debited_is_not_pending(self):
		row = report.allocate_pending([line("a", received=6, debited=3, returned=5)])[0]
		self.assertEqual(row.pending_qty, 0)
		self.assertEqual(row.status, report.RETURNED)

	def test_nothing_is_pending_beyond_what_was_received(self):
		row = report.allocate_pending([line("a", received=3, debited=10)])[0]
		self.assertEqual(row.pending_qty, 3)

	def test_one_invoice_line_in_two_receipts_is_shared_oldest_first(self):
		old = line("old", received=4, debited=6, date="2026-07-01")
		new = line("new", received=4, debited=6, date="2026-07-20")
		report.allocate_pending([new, old])
		self.assertEqual((old.pending_qty, new.pending_qty), (4, 2))

	def test_what_an_earlier_receipt_already_returned_is_taken_off_first(self):
		old = line("old", received=4, debited=6, returned=4, date="2026-07-01")
		new = line("new", received=4, debited=6, date="2026-07-20")
		report.allocate_pending([old, new])
		self.assertEqual((old.pending_qty, old.status), (0, report.RETURNED))
		self.assertEqual((new.pending_qty, new.status), (2, report.PENDING))

	def test_separate_invoice_lines_do_not_share_a_credit(self):
		a = line("a", received=5, debited=5, pi_row="PI-A")
		b = line("b", received=5, debited=1, pi_row="PI-B")
		report.allocate_pending([a, b])
		self.assertEqual((a.pending_qty, b.pending_qty), (5, 1))

	def test_fractional_quantities_are_kept(self):
		row = report.allocate_pending([line("a", received=2.5, debited=1.25)])[0]
		self.assertEqual(row.pending_qty, 1.25)

	def test_a_sliver_below_the_tolerance_is_not_a_pending_return(self):
		row = report.allocate_pending([line("a", received=6, debited=0.0004)])[0]
		self.assertEqual((row.pending_qty, row.status), (0, report.RETURNED))


class TestPermissionsAndSummary(FrappeTestCase):
	def test_no_restriction_shows_every_line(self):
		self.assertTrue(report.is_permitted(frappe._dict(supplier="S1", warehouse="W1"), {}))

	def test_a_restricted_warehouse_hides_another(self):
		scope = {"Warehouse": {"SFSS - SFB"}}
		self.assertFalse(report.is_permitted(frappe._dict(warehouse="SFWH - SFB"), scope))
		self.assertTrue(report.is_permitted(frappe._dict(warehouse="SFSS - SFB"), scope))

	def test_a_blank_value_passes_like_frappe_does_under_a_user_permission(self):
		scope = {"Branch": {"SFSS"}, "Cost Center": {"X"}}
		self.assertTrue(report.is_permitted(frappe._dict(branch=None, cost_center=""), scope))

	def test_every_restriction_must_hold(self):
		scope = {"Supplier": {"S2"}, "Warehouse": {"W1"}}
		self.assertFalse(report.is_permitted(frappe._dict(supplier="S1", warehouse="W1"), scope))
		self.assertTrue(report.is_permitted(frappe._dict(supplier="S2", warehouse="W1"), scope))

	def test_the_summary_counts_purchase_receipts_not_lines(self):
		rows = [
			line("a", 6, 5, pre="PRE-1", status=report.PENDING, pending_qty=5, pending_value=50.0),
			line("b", 5, 3, pre="PRE-1", status=report.PENDING, pending_qty=3, pending_value=30.0),
			line("c", 2, 2, pre="PRE-2", status=report.PENDING, pending_qty=2, pending_value=20.0),
			line("d", 4, 4, pre="PRE-3", status=report.RETURNED, pending_qty=0, pending_value=0.0),
		]
		cards = {c["label"]: c["value"] for c in report.get_summary(rows, "BHD")}
		self.assertEqual(cards["Purchase Receipts Pending Return"], 2)
		self.assertEqual(cards["Lines Pending"], 3)
		self.assertEqual(cards["Pending Qty"], 10)
		self.assertEqual(cards["Pending Value"], 100.0)

	def test_the_columns_carry_what_the_summary_reads(self):
		fieldnames = [c["fieldname"] for c in report.get_columns()]
		for needed in ("purchase_receipt", "invoice", "debit_notes", "debited_qty", "returned_qty", "pending_qty", "pending_value", "status"):
			self.assertIn(needed, fieldnames)
