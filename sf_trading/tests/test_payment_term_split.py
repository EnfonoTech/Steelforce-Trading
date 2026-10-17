# sf_trading/tests/test_payment_term_split.py
"""A Payment Advice row for a term-allocated document settles a NAMED Payment Term (sf_trading/payment_term_split.py).

Pure logic over two reads (the document's template and its Payment Schedule), so both are patched and no
fixtures are created:

    bench --site <site> run-tests --module sf_trading.tests.test_payment_term_split
"""

from datetime import date
from unittest.mock import patch

import frappe
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


class FakeDoc:
	"""Just enough of a Document: get / set / append, attribute access."""

	def __init__(self, **values):
		self.__dict__.update(values)
		self.rebuilt = 0

	def get(self, key, default=None):
		return getattr(self, key, default)

	def set(self, key, value):
		setattr(self, key, value)

	def append(self, key, row):
		getattr(self, key).append(frappe._dict(row))

	def set_payment_schedule(self):
		self.rebuilt += 1
		self.payment_schedule = [frappe._dict(payment_term="CASH", paid_amount=0)]


def pe_row(name, amount, term=None, doctype="Purchase Invoice"):
	return frappe._dict(
		name="row-" + name, idx=1, reference_doctype=doctype, reference_name=name, allocated_amount=amount, payment_term=term,
		total_amount=100, outstanding_amount=100, exchange_rate=1,
	)


class TestFillReferenceTerms(FrappeTestCase):
	def fill(self, doc, template="CASH", flag=1, rows=None):
		def get_value(doctype, name, field):
			return flag if doctype == "Payment Terms Template" else template

		with patch.object(pts.frappe.db, "get_value", side_effect=get_value), patch.object(pts.frappe, "get_all", return_value=rows or []):
			pts.fill_reference_terms(doc)
		return doc

	def test_a_row_picked_by_hand_gets_its_term(self):
		"""The live case: a new Payment Entry to Batelco, reference 20020000379, no Payment Term chosen."""
		doc = FakeDoc(references=[pe_row("20020000379", 16.773)])
		self.fill(doc, rows=schedule(("CASH", 16.773, date(2026, 10, 11))))
		self.assertEqual([(r.reference_name, r.payment_term, r.allocated_amount) for r in doc.references], [("20020000379", "CASH", 16.773)])
		# the other fields of the row travel with it
		self.assertEqual((doc.references[0].total_amount, doc.references[0].outstanding_amount), (100, 100))

	def test_a_row_is_split_over_the_open_instalments(self):
		doc = FakeDoc(references=[pe_row("PI-1", 50)])
		self.fill(doc, rows=schedule(("T1", 30.0, date(2026, 10, 1)), ("T2", 70.0, date(2026, 11, 1))))
		self.assertEqual([(r.payment_term, r.allocated_amount) for r in doc.references], [("T1", 30.0), ("T2", 20.0)])

	def test_the_other_rows_keep_their_place_and_values(self):
		doc = FakeDoc(references=[pe_row("PI-0", 5, term="X"), pe_row("PI-1", 20), pe_row("JV-1", 7, doctype="Journal Entry")])
		self.fill(doc, rows=schedule(("CASH", 20.0, date(2026, 10, 1))))
		got = [(r.reference_name, r.payment_term, r.allocated_amount) for r in doc.references]
		# PI-0 already names a term; the Journal Entry is not a term document, so only PI-1 is rewritten
		self.assertEqual(got[0], ("PI-0", "X", 5))
		self.assertEqual(got[1][:2], ("PI-1", "CASH"))
		self.assertEqual(got[2], ("JV-1", None, 7))

	def test_nothing_changes_when_no_row_needs_a_term(self):
		rows = [pe_row("PI-0", 5, term="X")]
		doc = FakeDoc(references=rows)
		self.fill(doc, rows=schedule(("CASH", 20.0, date(2026, 10, 1))))
		self.assertIs(doc.references, rows)

	def test_a_template_without_term_allocation_is_untouched(self):
		rows = [pe_row("PI-1", 20)]
		doc = FakeDoc(references=rows)
		self.fill(doc, template="30 Days Credit", flag=0, rows=schedule(("30 Days", 20.0, date(2026, 10, 1))))
		self.assertIs(doc.references, rows)

	def test_no_references_is_fine(self):
		doc = FakeDoc(references=[])
		self.fill(doc)
		self.assertEqual(doc.references, [])


class TestRepairTermLessSchedule(FrappeTestCase):
	def repair(self, doc, flag=1):
		with patch.object(pts.frappe.db, "get_value", return_value=flag):
			pts.repair_term_less_schedule(doc)
		return doc

	def invoice(self, **over):
		values = dict(
			payment_terms_template="CASH", is_return=0, is_pos=0, is_opening="No",
			payment_schedule=[frappe._dict(payment_term=None, paid_amount=0)],
		)
		values.update(over)
		return FakeDoc(**values)

	def test_a_term_allocated_template_on_a_term_less_schedule_is_rebuilt(self):
		doc = self.repair(self.invoice())
		self.assertEqual(doc.rebuilt, 1)
		self.assertEqual([r.payment_term for r in doc.payment_schedule], ["CASH"])

	def test_left_alone_when_the_terms_are_named_or_nothing_applies(self):
		named = self.repair(self.invoice(payment_schedule=[frappe._dict(payment_term="CASH", paid_amount=0)]))
		self.assertEqual(named.rebuilt, 0)
		self.assertEqual(self.repair(self.invoice(), flag=0).rebuilt, 0)  # template without term allocation
		self.assertEqual(self.repair(self.invoice(payment_terms_template=None)).rebuilt, 0)
		self.assertEqual(self.repair(self.invoice(payment_schedule=[])).rebuilt, 0)  # core builds it from the template
		self.assertEqual(self.repair(self.invoice(is_return=1)).rebuilt, 0)
		self.assertEqual(self.repair(self.invoice(is_pos=1)).rebuilt, 0)
		self.assertEqual(self.repair(self.invoice(is_opening="Yes")).rebuilt, 0)

	def test_a_schedule_that_already_holds_a_payment_is_not_rebuilt(self):
		doc = self.repair(self.invoice(payment_schedule=[frappe._dict(payment_term=None, paid_amount=5)]))
		self.assertEqual(doc.rebuilt, 0)
