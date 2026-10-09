"""Delivery Notes Pending Return: credited on the invoice, never returned to stock.

    bench --site <scratch-site> run-tests --module sf_trading.tests.test_delivery_notes_pending_return

The allocation is pure, so it is tested on plain rows; the SQL that feeds it is checked against the
real data by running the report (MAT-DN-2026-00071 / invoice 20010000667 / credit note 20010000754
is the pattern this was built for: 5 and 3 units credited, no stock ledger entry, no DN return).
"""

import frappe
from frappe.tests.utils import FrappeTestCase

from sf_trading.sf_trading.report.delivery_notes_pending_return import (
	delivery_notes_pending_return as report,
)


def line(dn_row, delivered, credited, returned=0.0, si_row="SI-ROW-1", date="2026-07-27", dn=None, **extra):
	return frappe._dict(
		dn_row=dn_row,
		delivery_note=dn or f"DN-{dn_row}",
		posting_date=date,
		delivered_qty=delivered,
		credited_qty=credited,
		returned_qty=returned,
		si_row=si_row,
		rate=10.0,
		**extra,
	)


class TestAllocatePending(FrappeTestCase):
	def test_credited_and_never_returned_is_pending(self):
		# the example: 6 delivered, 5 credited on the invoice, no delivery return
		row = report.allocate_pending([line("a", delivered=6, credited=5)])[0]
		self.assertEqual(row.pending_qty, 5)
		self.assertEqual(row.status, report.PENDING)

	def test_a_part_return_leaves_the_rest_pending(self):
		row = report.allocate_pending([line("a", delivered=6, credited=5, returned=2)])[0]
		self.assertEqual(row.pending_qty, 3)
		self.assertEqual(row.status, report.PENDING)

	def test_fully_returned_is_not_pending(self):
		row = report.allocate_pending([line("a", delivered=6, credited=5, returned=5)])[0]
		self.assertEqual(row.pending_qty, 0)
		self.assertEqual(row.status, report.RETURNED)

	def test_more_returned_than_credited_is_not_pending(self):
		row = report.allocate_pending([line("a", delivered=6, credited=3, returned=5)])[0]
		self.assertEqual(row.pending_qty, 0)
		self.assertEqual(row.status, report.RETURNED)

	def test_nothing_is_pending_beyond_what_was_delivered(self):
		row = report.allocate_pending([line("a", delivered=3, credited=10)])[0]
		self.assertEqual(row.pending_qty, 3)

	def test_one_invoice_line_in_two_deliveries_is_shared_oldest_first(self):
		old = line("old", delivered=4, credited=6, date="2026-07-01")
		new = line("new", delivered=4, credited=6, date="2026-07-20")
		report.allocate_pending([new, old])
		self.assertEqual((old.pending_qty, new.pending_qty), (4, 2))

	def test_what_an_earlier_delivery_already_returned_is_taken_off_first(self):
		old = line("old", delivered=4, credited=6, returned=4, date="2026-07-01")
		new = line("new", delivered=4, credited=6, date="2026-07-20")
		report.allocate_pending([old, new])
		self.assertEqual((old.pending_qty, old.status), (0, report.RETURNED))
		self.assertEqual((new.pending_qty, new.status), (2, report.PENDING))

	def test_separate_invoice_lines_do_not_share_a_credit(self):
		a = line("a", delivered=5, credited=5, si_row="SI-A")
		b = line("b", delivered=5, credited=1, si_row="SI-B")
		report.allocate_pending([a, b])
		self.assertEqual((a.pending_qty, b.pending_qty), (5, 1))

	def test_fractional_quantities_are_kept(self):
		row = report.allocate_pending([line("a", delivered=2.5, credited=1.25)])[0]
		self.assertEqual(row.pending_qty, 1.25)

	def test_a_sliver_below_the_tolerance_is_not_a_pending_return(self):
		row = report.allocate_pending([line("a", delivered=6, credited=0.0004)])[0]
		self.assertEqual((row.pending_qty, row.status), (0, report.RETURNED))


class TestPermissionsAndSummary(FrappeTestCase):
	def test_no_restriction_shows_every_line(self):
		self.assertTrue(report.is_permitted(frappe._dict(customer="C1", warehouse="W1"), {}))

	def test_a_restricted_warehouse_hides_another(self):
		scope = {"Warehouse": {"SFSS - SFB"}}
		self.assertFalse(report.is_permitted(frappe._dict(warehouse="SFWH - SFB"), scope))
		self.assertTrue(report.is_permitted(frappe._dict(warehouse="SFSS - SFB"), scope))

	def test_a_blank_value_passes_like_frappe_does_under_a_user_permission(self):
		scope = {"Branch": {"SFSS"}, "Cost Center": {"X"}}
		self.assertTrue(report.is_permitted(frappe._dict(branch=None, cost_center=""), scope))

	def test_every_restriction_must_hold(self):
		scope = {"Customer": {"C2"}, "Warehouse": {"W1"}}
		self.assertFalse(report.is_permitted(frappe._dict(customer="C1", warehouse="W1"), scope))
		self.assertTrue(report.is_permitted(frappe._dict(customer="C2", warehouse="W1"), scope))

	def test_the_summary_counts_delivery_notes_not_lines(self):
		rows = [
			line("a", 6, 5, dn="DN-1", status=report.PENDING, pending_qty=5, pending_value=50.0),
			line("b", 5, 3, dn="DN-1", status=report.PENDING, pending_qty=3, pending_value=30.0),
			line("c", 2, 2, dn="DN-2", status=report.PENDING, pending_qty=2, pending_value=20.0),
			line("d", 4, 4, dn="DN-3", status=report.RETURNED, pending_qty=0, pending_value=0.0),
		]
		cards = {c["label"]: c["value"] for c in report.get_summary(rows, "BHD")}
		self.assertEqual(cards["Delivery Notes Pending Return"], 2)
		self.assertEqual(cards["Lines Pending"], 3)
		self.assertEqual(cards["Pending Qty"], 10)
		self.assertEqual(cards["Pending Value"], 100.0)

	def test_the_columns_carry_what_the_summary_reads(self):
		fieldnames = [c["fieldname"] for c in report.get_columns()]
		for needed in ("delivery_note", "invoice", "credit_notes", "credited_qty", "returned_qty", "pending_qty", "pending_value", "status"):
			self.assertIn(needed, fieldnames)
