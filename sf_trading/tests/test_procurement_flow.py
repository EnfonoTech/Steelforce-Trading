# sf_trading/tests/test_procurement_flow.py
"""The buying chain is not skipped: a Request for Quotation needs a Material Request, a Supplier Quotation needs a
Request for Quotation, a Purchase Order needs all three, and only SF Trading Settings opens a gap.

Everything read from the database is patched, so no data is needed and nothing is written (never run a test module against
a real site -- ERPNext's before_tests wipes Item Price):

    bench --site <scratch-site> run-tests --module sf_trading.tests.test_procurement_flow
"""

import unittest
from unittest.mock import patch

import frappe

from sf_trading import procurement_flow as flow
from sf_trading import purchase_order_procurement as pop
from sf_trading.tests.test_purchase_order_procurement import World, line, order, settings


def document(rows, docstatus=0):
	return frappe._dict(docstatus=docstatus, items=rows)


def row(idx, **fields):
	return frappe._dict(idx=idx, **fields)


def refused(call, doc):
	"""The sentences a refusal carries, or None when the save went through."""
	try:
		call(doc)
	except frappe.ValidationError as error:
		return str(error)
	return None


class Records:
	"""frappe.get_all for the two lookups the gates make: Material Requests and Requests for Quotation."""

	def __init__(self, material_requests=None, rfqs=None):
		self.material_requests = material_requests or {}
		self.rfqs = rfqs or {}

	def get_all(self, doctype, filters=None, fields=None, **kwargs):
		names = filters["name"][1]
		if doctype == "Material Request":
			return [
				frappe._dict(name=n, docstatus=v[0], material_request_type=v[1])
				for n, v in self.material_requests.items()
				if n in names
			]
		if doctype == "Request for Quotation":
			return [frappe._dict(name=n, docstatus=d) for n, d in self.rfqs.items() if n in names]
		raise AssertionError("unexpected lookup of " + doctype)


def strict(**records):
	"""The gates with nothing opened in SF Trading Settings."""
	return (
		patch.object(flow, "allowed", return_value=False),
		patch.object(frappe, "get_all", Records(**records).get_all),
	)


class TestRequestForQuotation(unittest.TestCase):
	def check(self, doc, **records):
		first, second = strict(**records)
		with first, second:
			return refused(flow.validate_request_for_quotation, doc)

	def test_a_request_raised_from_a_submitted_purchase_material_request_passes(self):
		doc = document([row(1, material_request="MR-1"), row(2, material_request="MR-1")])
		self.assertIsNone(self.check(doc, material_requests={"MR-1": (1, "Purchase")}))

	def test_a_request_typed_in_with_no_material_request_is_refused(self):
		message = self.check(document([row(1, material_request=""), row(2)]))
		self.assertIn("Rows 1, 2: no Material Request.", message)
		self.assertIn("SF Trading Settings > Purchasing > Procurement Flow", message)

	def test_a_material_request_that_is_not_a_submitted_purchase_one_is_refused(self):
		records = {"MR-D": (0, "Purchase"), "MR-T": (1, "Material Transfer")}
		message = self.check(document([row(1, material_request="MR-D"), row(2, material_request="MR-T")]), material_requests=records)
		self.assertIn("Row 1: Material Request", message)
		self.assertIn("Row 2: Material Request", message)
		self.assertEqual(message.count("is not a submitted Material Request of type Purchase"), 2)

	def test_a_material_request_that_does_not_exist_is_refused(self):
		message = self.check(document([row(1, material_request="MR-GONE")]))
		self.assertIn("does not exist", message)

	def test_the_tick_opens_the_gap(self):
		with patch.object(flow, "allowed", return_value=True):
			self.assertIsNone(refused(flow.validate_request_for_quotation, document([row(1, material_request="")])))

	def test_a_cancelled_request_is_not_checked(self):
		first, second = strict()
		with first, second:
			self.assertIsNone(refused(flow.validate_request_for_quotation, document([row(1)], docstatus=2)))

	def test_the_tick_asked_for_is_the_one_for_requests(self):
		asked = []
		with patch.object(flow, "allowed", side_effect=lambda name: asked.append(name) or False), patch.object(
			frappe, "get_all", Records(material_requests={"MR-1": (1, "Purchase")}).get_all
		):
			flow.validate_request_for_quotation(document([row(1, material_request="MR-1")]))
		self.assertEqual(asked, ["allow_rfq_without_material_request"])


class TestSupplierQuotation(unittest.TestCase):
	def check(self, doc, **records):
		first, second = strict(**records)
		with first, second:
			return refused(flow.validate_supplier_quotation, doc)

	def test_a_quotation_that_answers_a_submitted_request_for_quotation_passes(self):
		doc = document([row(1, request_for_quotation="RFQ-1")])
		self.assertIsNone(self.check(doc, rfqs={"RFQ-1": 1}))

	def test_a_quotation_with_no_request_for_quotation_is_refused(self):
		message = self.check(document([row(1, request_for_quotation=None)]))
		self.assertIn("Row 1: no Request for Quotation.", message)
		self.assertIn("Create > Supplier Quotation", message)

	def test_a_quotation_made_straight_from_a_material_request_is_refused(self):
		# core's Create > Supplier Quotation on a Material Request names the request but no Request for Quotation
		doc = document([row(1, material_request="MR-1", request_for_quotation="")])
		self.assertIn("no Request for Quotation", self.check(doc))

	def test_a_request_for_quotation_that_is_a_draft_or_missing_is_refused(self):
		doc = document([row(1, request_for_quotation="RFQ-D"), row(2, request_for_quotation="RFQ-GONE")])
		message = self.check(doc, rfqs={"RFQ-D": 0})
		self.assertIn("Row 1: Request for Quotation", message)
		self.assertIn("is not submitted", message)
		self.assertIn("does not exist", message)

	def test_a_quotation_with_some_lines_answering_and_one_not_names_the_line(self):
		doc = document([row(1, request_for_quotation="RFQ-1"), row(2, request_for_quotation="")])
		message = self.check(doc, rfqs={"RFQ-1": 1})
		self.assertIn("Row 2: no Request for Quotation.", message)
		self.assertNotIn("Row 1", message)

	def test_the_tick_opens_the_gap_and_is_the_one_for_quotations(self):
		asked = []
		with patch.object(flow, "allowed", side_effect=lambda name: asked.append(name) or True):
			self.assertIsNone(refused(flow.validate_supplier_quotation, document([row(1)])))
		self.assertEqual(asked, ["allow_quotation_without_rfq"])

	def test_a_cancelled_quotation_is_not_checked(self):
		first, second = strict()
		with first, second:
			self.assertIsNone(refused(flow.validate_supplier_quotation, document([row(1)], docstatus=2)))


class TestAllowed(unittest.TestCase):
	def test_a_tick_that_was_never_saved_reads_as_off(self):
		for stored in (None, 0, "0", ""):
			with patch.object(frappe.db, "get_single_value", return_value=stored, create=True):
				self.assertFalse(flow.allowed("allow_quotation_without_rfq"))
		with patch.object(frappe.db, "get_single_value", return_value=1, create=True):
			self.assertTrue(flow.allowed("allow_quotation_without_rfq"))

	def test_a_site_that_cannot_answer_reads_as_off(self):
		with patch.object(frappe.db, "get_single_value", side_effect=Exception("no such column"), create=True):
			self.assertFalse(flow.allowed("allow_quotation_without_rfq"))


class TestTheConnections(unittest.TestCase):
	def test_the_material_request_is_added_to_the_requests_connections(self):
		core = {"docstatus": 1, "fieldname": "request_for_quotation", "transactions": [{"items": ["Supplier Quotation"]}]}
		data = flow.rfq_dashboard(core)
		self.assertEqual(data["internal_links"]["Material Request"], ["items", "material_request"])
		self.assertIn({"label": "Reference", "items": ["Material Request"]}, data["transactions"])
		self.assertEqual(data["transactions"][0], {"items": ["Supplier Quotation"]})
		self.assertEqual(data["fieldname"], "request_for_quotation")

	def test_it_is_added_once(self):
		core = {"transactions": [{"items": ["Supplier Quotation"]}]}
		again = flow.rfq_dashboard(flow.rfq_dashboard(core))
		self.assertEqual(sum("Material Request" in group["items"] for group in again["transactions"]), 1)

	def test_nothing_in_the_dashboard_still_gets_the_link(self):
		self.assertEqual(flow.rfq_dashboard(None)["internal_links"], {"Material Request": ["items", "material_request"]})


class TestNoDirectPurchaseOrder(unittest.TestCase):
	"""With "Allow a Purchase Order Without the Whole Chain" off, no order escapes the chain."""

	def problems(self, doc, rule=None, direct=False):
		with World(), patch.object(pop, "direct_orders_allowed", return_value=direct):
			return pop.problems_for(doc, rule or settings())

	def test_a_small_local_order_is_held_to_the_chain_too(self):
		bare = order([line(1, mr="", sq="", sqi="")], total=50)
		self.assertEqual(len(self.problems(bare)), 2)
		self.assertEqual(self.problems(bare, direct=True), [])

	def test_a_company_with_no_rule_is_held_to_the_chain(self):
		bare = order([line(1, mr="", sq="", sqi="")], total=50)
		none = settings(enforce=False)
		self.assertEqual(len(self.problems(bare, none)), 2)
		self.assertEqual(self.problems(bare, none, direct=True), [])

	def test_the_chain_needs_at_least_one_quotation_and_the_rule_asks_for_more_where_it_does(self):
		doc = order(total=50)
		with patch.object(frappe, "get_cached_value", return_value="BHD"), patch.object(pop, "direct_orders_allowed", return_value=False):
			self.assertEqual(pop.quotations_needed(doc, settings()), 1)
			self.assertEqual(pop.quotations_needed(doc, settings(enforce=False)), 1)
			self.assertEqual(pop.quotations_needed(order(total=900), settings()), 3)
			self.assertEqual(pop.quotations_needed(order(currency="USD", total=5), settings()), 2)
		with patch.object(frappe, "get_cached_value", return_value="BHD"), patch.object(pop, "direct_orders_allowed", return_value=True):
			self.assertEqual(pop.quotations_needed(doc, settings()), 0)
			self.assertEqual(pop.quotations_needed(doc, settings(enforce=False)), 0)

	def test_a_small_order_with_the_whole_chain_and_one_quotation_passes(self):
		world = World(competing={("RFQ-1", "STEEL-1"): {"Alpha Steel"}})
		with world, patch.object(pop, "direct_orders_allowed", return_value=False):
			self.assertEqual(pop.problems_for(order(total=50), settings()), [])

	def test_a_verbal_order_and_an_internal_supplier_stay_exempt(self):
		bare = [line(1, mr="", sq="", sqi="")]
		self.assertEqual(self.problems(order(bare, verbal=1, total=50)), [])
		self.assertEqual(self.problems(order(bare, total=50, is_internal_supplier=1)), [])

	def test_the_refusal_says_where_to_allow_it(self):
		bare = order([line(1, mr="", sq="", sqi="")], total=50)
		with World(), patch.object(pop, "rules", return_value=settings()), patch.object(pop, "direct_orders_allowed", return_value=False):
			message = refused(pop.validate, bare)
		self.assertIn("Procurement Flow", message)
		self.assertIn("an order needs a Material Request, one Request for Quotation raised from it and 1 Supplier Quotation", message)
		with World(), patch.object(pop, "rules", return_value=settings()), patch.object(pop, "direct_orders_allowed", return_value=True):
			self.assertIsNone(refused(pop.validate, bare))

	def test_the_tick_read_is_the_one_for_orders(self):
		with patch.object(frappe.db, "get_single_value", return_value=1, create=True) as read:
			self.assertTrue(pop.direct_orders_allowed())
		read.assert_called_once_with("SF Trading Settings", "allow_direct_purchase_order")
		with patch.object(frappe.db, "get_single_value", side_effect=Exception("no column"), create=True):
			self.assertFalse(pop.direct_orders_allowed())
