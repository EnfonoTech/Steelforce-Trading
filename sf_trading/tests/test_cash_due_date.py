# sf_trading/tests/test_cash_due_date.py
"""A Cash sale is due the day it is made, whatever terms its customer carries (sf_trading/cash_due_date.py).

Pure logic over the document in hand, so a small stand-in document is used and nothing is created:

    bench --site <site> run-tests --module sf_trading.tests.test_cash_due_date
"""

from datetime import date
from types import SimpleNamespace

import frappe
from frappe.tests.utils import FrappeTestCase

from sf_trading.cash_due_date import apply_cash_due_date


class FakeDoc:
	"""Just enough of a Document: get / set / append, attribute access and meta.has_field."""

	doctype = "Sales Invoice"

	def __init__(self, fields, **values):
		self.__dict__.update(values)
		self.meta = SimpleNamespace(has_field=lambda f: f in fields)

	def precision(self, fieldname):
		return 3

	def get(self, key, default=None):
		return getattr(self, key, default)

	def set(self, key, value):
		setattr(self, key, value)

	def append(self, key, row):
		getattr(self, key).append(frappe._dict(row))


def term_rows(*portions, today=date(2026, 10, 15)):
	"""Rows as core builds them from a template: named terms, due dates in the future."""
	return [
		frappe._dict(
			payment_term=f"Term {i}",
			due_date=date(2026, 11, 24),
			invoice_portion=p,
			payment_amount=100 * p / 100,
			base_payment_amount=100 * p / 100,
			outstanding=100 * p / 100,
			base_outstanding=100 * p / 100,
			paid_amount=0,
		)
		for i, p in enumerate(portions, start=1)
	]


def invoice(**over):
	values = dict(
		custom_payment_mode="Cash",
		is_return=0,
		is_pos=0,
		is_opening="No",
		posting_date=date(2026, 10, 15),
		due_date=date(2026, 11, 24),
		payment_terms_template="40 Days Credit",
		payment_schedule=term_rows(50, 50),
	)
	values.update(over)
	return FakeDoc({"due_date", "payment_terms_template", "payment_schedule"}, **values)


class TestCashDueDate(FrappeTestCase):
	def test_cash_sale_of_a_credit_customer_is_due_the_same_day(self):
		doc = invoice()
		apply_cash_due_date(doc)
		self.assertIsNone(doc.payment_terms_template)
		self.assertEqual(doc.due_date, date(2026, 10, 15))
		self.assertEqual(len(doc.payment_schedule), 1)
		row = doc.payment_schedule[0]
		self.assertEqual(row.due_date, date(2026, 10, 15))
		self.assertEqual(row.invoice_portion, 100)
		self.assertFalse(row.get("payment_term"))
		# nothing moved but the dates and the term: the two halves add back up
		self.assertEqual(row.payment_amount, 100)
		self.assertEqual(row.base_payment_amount, 100)
		self.assertEqual(row.outstanding, 100)

	def test_one_row_carries_the_exact_total_not_the_sum_of_rounded_halves(self):
		# 3.675 split in two is 1.838 + 1.838 = 3.676: a thousandth off the invoice
		halves = term_rows(50, 50)
		for row in halves:
			row.payment_amount = row.base_payment_amount = row.outstanding = row.base_outstanding = 1.838
		doc = invoice(payment_schedule=halves, grand_total=3.675, base_grand_total=3.675, rounded_total=0, base_rounded_total=0)
		apply_cash_due_date(doc)
		row = doc.payment_schedule[0]
		self.assertEqual((row.payment_amount, row.base_payment_amount, row.outstanding), (3.675, 3.675, 3.675))

	def test_write_off_is_taken_off_the_total_like_core_does(self):
		doc = invoice(grand_total=100, base_grand_total=100, write_off_amount=0.05, base_write_off_amount=0.05)
		apply_cash_due_date(doc)
		self.assertEqual(doc.payment_schedule[0].payment_amount, 99.95)

	def test_with_an_advance_the_rows_sum_is_kept(self):
		doc = invoice(grand_total=100, base_grand_total=100, total_advance=30)
		apply_cash_due_date(doc)
		self.assertEqual(doc.payment_schedule[0].payment_amount, 100)  # the rows' own sum, core already netted the advance

	def test_a_total_far_from_the_rows_is_not_papered_over(self):
		doc = invoice(grand_total=500, base_grand_total=500)  # rows add up to 100: a real disagreement, kept as it is
		apply_cash_due_date(doc)
		self.assertEqual(doc.payment_schedule[0].payment_amount, 100)

	def test_a_back_dated_invoice_is_due_on_its_own_date(self):
		doc = invoice(posting_date=date(2026, 9, 30))
		apply_cash_due_date(doc)
		self.assertEqual(doc.due_date, date(2026, 9, 30))
		self.assertEqual(doc.payment_schedule[0].due_date, date(2026, 9, 30))

	def test_a_sales_order_has_no_due_date_field_and_uses_its_transaction_date(self):
		doc = FakeDoc(
			{"payment_terms_template", "payment_schedule"},
			custom_payment_mode="Cash",
			transaction_date=date(2026, 10, 15),
			payment_terms_template="30 Days Credit",
			payment_schedule=term_rows(100),
		)
		apply_cash_due_date(doc)
		self.assertIsNone(doc.payment_terms_template)
		self.assertFalse(hasattr(doc, "due_date"))
		self.assertEqual([r.due_date for r in doc.payment_schedule], [date(2026, 10, 15)])

	def test_other_modes_are_left_alone(self):
		for mode in ("Credit", "Cheque", None):
			doc = invoice(custom_payment_mode=mode)
			rows = doc.payment_schedule
			apply_cash_due_date(doc)
			self.assertEqual(doc.payment_terms_template, "40 Days Credit", mode)
			self.assertEqual(doc.due_date, date(2026, 11, 24), mode)
			self.assertIs(doc.payment_schedule, rows, mode)

	def test_returns_pos_and_opening_entries_are_left_alone(self):
		for over in ({"is_return": 1}, {"is_pos": 1}, {"is_opening": "Yes"}):
			doc = invoice(**over)
			apply_cash_due_date(doc)
			self.assertEqual(doc.payment_terms_template, "40 Days Credit", over)
			self.assertEqual(len(doc.payment_schedule), 2, over)

	def test_a_schedule_that_already_holds_a_payment_is_not_rewritten(self):
		doc = invoice()
		doc.payment_schedule[0].paid_amount = 10
		apply_cash_due_date(doc)
		self.assertEqual(len(doc.payment_schedule), 2)
		self.assertEqual(doc.payment_terms_template, "40 Days Credit")

	def test_already_right_is_not_touched_again(self):
		doc = invoice()
		apply_cash_due_date(doc)
		rows = doc.payment_schedule
		apply_cash_due_date(doc)
		self.assertIs(doc.payment_schedule, rows)
		self.assertEqual(len(doc.payment_schedule), 1)

	def test_no_schedule_built_only_clears_the_terms_and_sets_the_date(self):
		doc = invoice(payment_schedule=[])
		apply_cash_due_date(doc)
		self.assertIsNone(doc.payment_terms_template)
		self.assertEqual(doc.due_date, date(2026, 10, 15))
		self.assertEqual(doc.payment_schedule, [])
