# sf_trading/tests/test_comparison_statement.py
"""The Quotation Comparison Statement: what it prints for a Request for Quotation, and that the template renders it.

The comparison is patched, so no data is needed and nothing is written (never run a test module against a real site --
ERPNext's before_tests wipes Item Price):

    bench --site <scratch-site> run-tests --module sf_trading.tests.test_comparison_statement
"""

import json
import os
from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase

from sf_trading import comparison_statement as statement
from sf_trading.tests.test_supplier_quotation_comparison_extended import asked, charge, invited, quote, run

PRINT_FORMAT = os.path.join(
	os.path.dirname(statement.__file__),
	"sf_trading",
	"print_format",
	"rfq_quotation_comparison_statement",
	"rfq_quotation_comparison_statement.json",
)


def build(comparison, readable=True):
	head = frappe._dict(name="RFQ-1", company="SFB", transaction_date="2026-10-20", status="Submitted")
	with (
		patch.object(statement.frappe, "has_permission", return_value=readable),
		patch.object(statement.frappe.db, "get_value", return_value=head, create=True),
		patch.object(statement.report, "compare", return_value=comparison),
	):
		return statement.comparison_statement("RFQ-1")


def sample():
	"""S1 offers 40 of 100 bolts at 1.0, S2 the lot at 1.2 (so S2 covers the rest), S3 never answers; nut: only S1 quoted."""
	return run(
		[
			quote("S1", "bolt", 40, 1.0, quotation="SQ-S1"),
			quote("S2", "bolt", 100, 1.2, quotation="SQ-S2"),
			quote("S1", "nut", 10, 5.0, quotation="SQ-S1"),
		],
		requested=asked(bolt=100, nut=10, washer=5),
		invites=invited("S1", "S2", "S3"),
	)


class TestWhatTheStatementPrints(FrappeTestCase):
	def setUp(self):
		self.s = build(sample())
		self.items = {i["item"]: i for i in self.s["items"]}

	def test_the_header_and_the_basis(self):
		self.assertTrue(self.s.available)
		self.assertEqual((self.s.rfq, self.s.company, self.s.status), ("RFQ-1", "SFB", "Submitted"))
		self.assertIn("estimated charges", self.s.basis)

	def test_each_item_lists_its_quotations_ranked_with_the_recommended_one_flagged(self):
		bolt = self.items["bolt"]
		self.assertEqual([q["supplier"] for q in bolt["quotes"]], ["S1 Trading", "S2 Trading"])
		self.assertEqual([q["rank"] for q in bolt["quotes"]], ["1", "2"])
		self.assertEqual([q["recommended"] for q in bolt["quotes"]], [1, 0])
		self.assertEqual(bolt["requested"], 100.0)

	def test_the_award_is_split_where_the_cheaper_quotation_offered_less(self):
		awards = self.items["bolt"]["awards"]
		self.assertEqual([(a["supplier"], a["award_qty"]) for a in awards], [("S1 Trading", "40.0"), ("S2 Trading", "60.0")])
		self.assertEqual([a["supplier"] for a in self.items["nut"]["awards"]], ["S1 Trading"])

	def test_an_item_nobody_quoted_says_so(self):
		washer = self.items["washer"]
		self.assertEqual(washer["quotes"], [])
		self.assertEqual(washer["awards"], [])
		self.assertEqual(washer["note"], "No quotation received")

	def test_the_suppliers_and_the_open_points(self):
		suppliers = {r["supplier"]: r for r in self.s["suppliers"]}
		self.assertEqual(suppliers["S3 Trading"]["response"], "No response")
		self.assertEqual(suppliers["S1 Trading"]["quoted"], "2 / 3")
		gaps = {(g["gap"], g["supplier"], g["item"]) for g in self.s["gaps"]}
		self.assertIn(("No response", "S3 Trading", ""), gaps)
		self.assertTrue(any(g[0] == "No quotation received" and g[2] == "washer" for g in gaps))

	def test_the_total_awarded_is_there(self):
		self.assertTrue(self.s.award_total)

	def test_a_user_who_may_not_read_the_request_gets_a_note_not_a_page(self):
		denied = build(sample(), readable=False)
		self.assertFalse(denied.available)
		self.assertIn("may not read", denied.note)

	def test_a_request_nobody_answered_says_so(self):
		empty = build(run([], requested=asked(bolt=100), invites=invited("S1")))
		self.assertTrue(empty.available)
		self.assertIn("No quotation has been received", empty.note)


class TestChargesAreNamed(FrappeTestCase):
	def test_the_charge_names_are_on_the_line_and_on_the_page(self):
		compared = run(
			[
				quote("S1", "bolt", 100, 1.0, quotation="SQ-S1", charges=[charge("Local Delivery", 20)]),
				quote("S2", "bolt", 100, 1.2, quotation="SQ-S2"),
			],
			requested=asked(bolt=100),
		)
		data = build(compared)
		quotes = {q["supplier"]: q for q in data["items"][0]["quotes"]}
		self.assertEqual(quotes["S1 Trading"]["charge_detail"], "Local Delivery 20.0")
		self.assertEqual(quotes["S2 Trading"]["charge_detail"], "")
		try:
			import jinja2
		except ImportError:
			return
		with open(PRINT_FORMAT, encoding="utf-8") as handle:
			html = json.load(handle)["html"]
		environment = jinja2.Environment(autoescape=True)
		environment.globals["_"] = lambda text: text
		environment.globals["comparison_statement"] = lambda name: data
		self.assertIn("Local Delivery 20.0", environment.from_string(html).render(doc=frappe._dict(name="RFQ-1")))


class TestTheTemplate(FrappeTestCase):
	def render(self, data):
		try:
			import jinja2
		except ImportError:
			self.skipTest("jinja2 is not installed here")
		with open(PRINT_FORMAT, encoding="utf-8") as handle:
			html = json.load(handle)["html"]
		environment = jinja2.Environment(autoescape=True)
		environment.globals["_"] = lambda text: text
		environment.globals["comparison_statement"] = lambda name: data
		return environment.from_string(html).render(doc=frappe._dict(name="RFQ-1"))

	def test_it_renders_every_section_with_the_figures(self):
		page = self.render(build(sample()))
		for text in (
			"Quotation Comparison Statement",
			"1. Recommended award",
			"2. Quotations received, item by item",
			"3. Suppliers",
			"4. Open points",
			"Sign-off",
			"S1 Trading",
			"S2 Trading",
			"SQ-S1",
			"No quotation received",
			"Awarded the quantity the cheaper quotation did not cover",
			"Prepared by",
			"Approved by",
		):
			self.assertIn(text, page)

	def test_it_renders_a_page_that_says_why_it_is_empty(self):
		page = self.render(build(sample(), readable=False))
		self.assertIn("You may not read this Request for Quotation.", page)
		self.assertNotIn("1. Recommended award", page)

	def test_the_print_format_is_a_standard_jinja_format_on_the_request(self):
		with open(PRINT_FORMAT, encoding="utf-8") as handle:
			doc = json.load(handle)
		self.assertEqual(
			(doc["doctype"], doc["doc_type"], doc["print_format_type"], doc["standard"], doc["custom_format"]),
			("Print Format", "Request for Quotation", "Jinja", "Yes", 1),
		)
