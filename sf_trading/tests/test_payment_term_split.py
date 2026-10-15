# sf_trading/tests/test_payment_term_split.py
"""A Payment Advice row for a term-allocated document settles a NAMED Payment Term (sf_trading/payment_term_split.py).

Pure logic over two reads (the document's template and its Payment Schedule), so both are patched and no
fixtures are created:

    bench --site <site> run-tests --module sf_trading.tests.test_payment_term_split
"""

from datetime import date
from unittest.mock import patch

from frappe.tests.utils import FrappeTestCase

from sf_trading import payment_term_split as pts


def reference(doctype, name, amount, term=None):
	row = {"reference_doctype": doctype, "reference_name": name, "allocated_amount": amount}
	if term:
		row["payment_term"] = term
	return row


def schedule(*terms):
	"""(payment_term, outstanding, due_date) -> Payment Schedule rows, idx in the order given."""
	return [
		{"payment_term": t, "outstanding": o, "due_date": d, "idx": i + 1} for i, (t, o, d) in enumerate(terms)
	]


class TestPaymentTermSplit(FrappeTestCase):
	def run_with(self, template, flag, rows, fn):
		"""Run `fn` with the document's template, the template's switch and its schedule faked."""

		def get_value(doctype, name, field):
			if doctype == "Payment Terms Template":
				return flag
			return template

		with patch.object(pts.frappe.db, "get_value", side_effect=get_value), patch.object(
			pts.frappe, "get_all", return_value=rows
		):
			return fn()

	def split(self, ref, template="CASH", flag=1, rows=None):
		return self.run_with(template, flag, rows or [], lambda: pts.split_by_payment_term(ref))

	def test_one_open_term_is_named(self):
		"""The live case: PA-26-0092 -> Purchase Invoice 20020000384 under CASH, one term of 60.500."""
		rows = schedule(("CASH", 60.5, date(2026, 10, 11)))
		out = self.split(reference("Purchase Invoice", "20020000384", 60.5), rows=rows)
		self.assertEqual(out, [{**reference("Purchase Invoice", "20020000384", 60.5), "payment_term": "CASH"}])
		self.assertEqual(
			self.run_with("CASH", 1, rows, lambda: pts.single_open_term("Purchase Invoice", "20020000384")), "CASH"
		)

	def test_two_terms_are_split_oldest_first(self):
		rows = schedule(("T2", 70.0, date(2026, 11, 1)), ("T1", 30.0, date(2026, 10, 1)))
		# the database orders by due_date; get_all is faked, so give it in that order
		rows = sorted(rows, key=lambda r: r["due_date"])
		out = self.split(reference("Purchase Invoice", "PI", 50), rows=rows)
		self.assertEqual([(p["payment_term"], p["allocated_amount"]) for p in out], [("T1", 30.0), ("T2", 20.0)])
		self.assertEqual(round(sum(p["allocated_amount"] for p in out), 3), 50)

	def test_allocation_inside_the_first_term_uses_only_it(self):
		rows = schedule(("T1", 30.0, date(2026, 10, 1)), ("T2", 70.0, date(2026, 11, 1)))
		out = self.split(reference("Purchase Invoice", "PI", 20), rows=rows)
		self.assertEqual([(p["payment_term"], p["allocated_amount"]) for p in out], [("T1", 20.0)])

	def test_over_allocation_lands_on_the_last_term_so_core_still_reports_it(self):
		rows = schedule(("T1", 30.0, date(2026, 10, 1)), ("T2", 70.0, date(2026, 11, 1)))
		out = self.split(reference("Purchase Invoice", "PI", 120), rows=rows)
		self.assertEqual([(p["payment_term"], p["allocated_amount"]) for p in out], [("T1", 30.0), ("T2", 90.0)])

	def test_several_open_terms_show_no_single_term(self):
		rows = schedule(("T1", 30.0, date(2026, 10, 1)), ("T2", 70.0, date(2026, 11, 1)))
		self.assertIsNone(self.run_with("50-50", 1, rows, lambda: pts.single_open_term("Purchase Invoice", "PI")))

	def test_untouched_when_nothing_to_name(self):
		rows = schedule(("T1", 30.0, date(2026, 10, 1)))
		named = reference("Purchase Invoice", "PI", 10, term="T1")
		self.assertEqual(self.split(named, rows=rows), [named])

		plain = reference("Purchase Invoice", "PI", 10)
		# template without term-based allocation
		self.assertEqual(self.split(plain, template="30 Days Credit", flag=0, rows=rows), [plain])
		# no template at all
		self.assertEqual(self.split(plain, template=None, flag=0, rows=rows), [plain])
		# every instalment already settled: core says what it says
		self.assertEqual(self.split(plain, rows=schedule(("T1", 0.0, date(2026, 10, 1)))), [plain])
		# a debit / credit note sets off, it does not pay a term
		note = reference("Purchase Invoice", "PI", -5)
		self.assertEqual(self.split(note, rows=rows), [note])
		# a document that is never term-allocated
		journal = reference("Journal Entry", "JV", 10)
		self.assertEqual(self.split(journal, rows=rows), [journal])

	def test_the_input_reference_is_not_changed(self):
		rows = schedule(("T1", 30.0, date(2026, 10, 1)), ("T2", 70.0, date(2026, 11, 1)))
		original = reference("Purchase Invoice", "PI", 50)
		self.split(original, rows=rows)
		self.assertEqual(original, reference("Purchase Invoice", "PI", 50))
