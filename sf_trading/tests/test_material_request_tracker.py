# sf_trading/tests/test_material_request_tracker.py
"""Material Request Procurement Tracker: the stage of every line, request and Request for Quotation, and what the cards say.

The chain is served from a dict, so no data is needed and nothing is written (never run a test module against a real site --
ERPNext's before_tests wipes Item Price):

    bench --site <scratch-site> run-tests --module sf_trading.tests.test_material_request_tracker
"""

import unittest
from unittest.mock import patch

import frappe

from sf_trading.sf_trading.report.material_request_procurement_tracker import (
	material_request_procurement_tracker as tracker,
)

D = frappe._dict


def request(name, date="2026-10-10", required="2026-11-30", status="Pending"):
	return D(name=name, transaction_date=date, schedule_date=required, status=status, owner="buyer@example.com")


def mr_item(name, parent, code, qty=100, ordered=0):
	return D(
		name=name, parent=parent, idx=1, item_code=code, item_name=code.title(), qty=qty, stock_qty=qty, uom="Nos",
		stock_uom="Nos", warehouse="Main - T", schedule_date=None, ordered_qty=ordered,
	)


def rfq_line(name, rfq, mr, mr_item_name, code):
	return D(name=name, parent=rfq, material_request=mr, material_request_item=mr_item_name, item_code=code)


def quote_line(sq, rfq, rfq_line_name, code):
	return D(parent=sq, request_for_quotation=rfq, request_for_quotation_item=rfq_line_name, item_code=code)


class Chain:
	"""A small buying chain the tracker reads from."""

	def __init__(self):
		self.requests = [
			request("MR-1"),  # bolt fully quoted, nut only by S1
			request("MR-2", required="2026-10-20"),  # nothing raised, overdue
			request("MR-3"),  # a draft RFQ
			request("MR-4"),  # a submitted RFQ nobody answered
			request("MR-5"),  # ordered
			request("MR-6"),  # partly ordered
		]
		self.items = [
			mr_item("MRI-1a", "MR-1", "bolt"),
			mr_item("MRI-1b", "MR-1", "nut"),
			mr_item("MRI-2", "MR-2", "washer"),
			mr_item("MRI-3", "MR-3", "screw"),
			mr_item("MRI-4", "MR-4", "pin"),
			mr_item("MRI-5", "MR-5", "gear", ordered=100),
			mr_item("MRI-6", "MR-6", "cog", ordered=40),
		]
		self.tables = {
			"Request for Quotation Item": [
				rfq_line("RL-1a", "RFQ-1", "MR-1", "MRI-1a", "bolt"),
				rfq_line("RL-1b", "RFQ-1", "MR-1", "MRI-1b", "nut"),
				rfq_line("RL-3", "RFQ-3", "MR-3", "MRI-3", "screw"),
				rfq_line("RL-4", "RFQ-4", "MR-4", "MRI-4", "pin"),
				rfq_line("RL-5", "RFQ-5", "MR-5", "MRI-5", "gear"),
			],
			"Request for Quotation": [
				D(name="RFQ-1", docstatus=1, status="Submitted", transaction_date="2026-10-12"),
				D(name="RFQ-3", docstatus=0, status="Draft", transaction_date="2026-10-13"),
				D(name="RFQ-4", docstatus=1, status="Submitted", transaction_date="2026-10-14"),
				D(name="RFQ-5", docstatus=1, status="Submitted", transaction_date="2026-10-15"),
			],
			"Request for Quotation Supplier": [
				D(parent="RFQ-1", supplier="S1", supplier_name="Supplier One"),
				D(parent="RFQ-1", supplier="S2", supplier_name="Supplier Two"),
				D(parent="RFQ-1", supplier="S3", supplier_name="Supplier Three"),
				D(parent="RFQ-3", supplier="S1", supplier_name="Supplier One"),
				D(parent="RFQ-4", supplier="S1", supplier_name="Supplier One"),
				D(parent="RFQ-4", supplier="S2", supplier_name="Supplier Two"),
				D(parent="RFQ-5", supplier="S1", supplier_name="Supplier One"),
			],
			"Supplier Quotation Item": [
				quote_line("SQ-1", "RFQ-1", "RL-1a", "bolt"),
				quote_line("SQ-1", "RFQ-1", "RL-1b", "nut"),
				quote_line("SQ-2", "RFQ-1", "RL-1a", "bolt"),
				quote_line("SQ-3", "RFQ-1", "RL-1a", "bolt"),
				quote_line("SQ-5", "RFQ-5", "RL-5", "gear"),
			],
			"Supplier Quotation": [
				D(name="SQ-1", supplier="S1", supplier_name="Supplier One"),
				D(name="SQ-2", supplier="S2", supplier_name="Supplier Two"),
				D(name="SQ-3", supplier="S3", supplier_name="Supplier Three"),
				D(name="SQ-5", supplier="S1", supplier_name="Supplier One"),
			],
			"Purchase Order Item": [
				D(parent="PO-5", material_request="MR-5", material_request_item="MRI-5"),
				D(parent="PO-6", material_request="MR-6", material_request_item="MRI-6"),
			],
		}

	def fetch_in(self, doctype, values, in_field="name", filters=None, fields=None, **kwargs):
		wanted = set(values or [])
		return [row for row in self.tables.get(doctype, []) if row.get(in_field) in wanted]


def execute(chain=None, **filters):
	chain = chain or Chain()
	filters = D(company="SFB", from_date="2026-10-01", to_date="2026-10-31", **filters)
	with (
		patch.object(tracker, "get_material_requests", return_value=chain.requests),
		patch.object(tracker, "get_items", return_value=chain.items),
		patch.object(tracker, "fetch_in", chain.fetch_in),
		patch.object(tracker, "nowdate", return_value="2026-10-31"),
	):
		return tracker.execute(filters)


def rows_of(chain=None, **filters):
	columns, rows, message, chart, summary = execute(chain, **filters)
	return rows


class TestTheStagesOfARequest(unittest.TestCase):
	def setUp(self):
		self.rows = {row["material_request"]: row for row in rows_of()}

	def test_each_request_is_at_the_stage_its_chain_has_reached(self):
		stages = {name: row["stage"] for name, row in self.rows.items()}
		self.assertEqual(
			stages,
			{
				"MR-1": "Quotations Partly Received",
				"MR-2": "No RFQ",
				"MR-3": "RFQ Not Submitted",
				"MR-4": "Awaiting Quotations",
				"MR-5": "Ordered",
				"MR-6": "Partly Ordered",
			},
		)

	def test_a_request_is_as_far_on_as_its_least_advanced_line(self):
		# bolt has every quotation, nut only one of three: the request is Partly Received, and the counts say why
		row = self.rows["MR-1"]
		self.assertEqual((row["items"], row["items_with_rfq"], row["items_quoted"], row["items_fully_quoted"]), (2, 2, 2, 1))

	def test_the_rfq_and_its_suppliers_are_counted(self):
		row = self.rows["MR-1"]
		self.assertEqual(row["rfqs"], "RFQ-1")
		self.assertEqual(row["rfq_progress"], "RFQ-1 3/3")
		self.assertEqual(row["rfqs_complete"], "1 / 1")
		self.assertEqual((row["suppliers_invited"], row["suppliers_answered"], row["quotations"]), (3, 3, 3))

	def test_a_draft_request_for_quotation_is_marked_and_not_counted_complete(self):
		row = self.rows["MR-3"]
		self.assertEqual(row["rfq_progress"], "RFQ-3 0/1 (draft)")
		self.assertEqual(row["rfqs_complete"], "0 / 1")
		self.assertEqual(row["suppliers_invited"], 0)

	def test_a_submitted_request_nobody_answered(self):
		row = self.rows["MR-4"]
		self.assertEqual((row["suppliers_invited"], row["suppliers_answered"], row["quotations"]), (2, 0, 0))
		self.assertEqual(row["rfq_progress"], "RFQ-4 0/2")

	def test_the_ordered_quantity_and_the_orders(self):
		self.assertEqual((self.rows["MR-5"]["ordered_pct"], self.rows["MR-5"]["orders"]), (100.0, "PO-5"))
		self.assertEqual((self.rows["MR-6"]["ordered_pct"], self.rows["MR-6"]["orders"]), (40.0, "PO-6"))
		self.assertEqual(self.rows["MR-6"]["items_ordered"], 0)
		self.assertEqual(self.rows["MR-5"]["items_ordered"], 1)

	def test_a_request_past_its_required_date_and_not_ordered_is_overdue(self):
		self.assertEqual(self.rows["MR-2"]["overdue"], 1)
		self.assertEqual(self.rows["MR-1"]["overdue"], 0)
		self.assertEqual(self.rows["MR-5"]["overdue"], 0)

	def test_the_days_open_and_the_compare_link(self):
		self.assertEqual(self.rows["MR-1"]["days_open"], 21)
		self.assertEqual(self.rows["MR-1"]["compare"], "Compare")
		self.assertEqual(self.rows["MR-2"]["compare"], "")


class TestTheLines(unittest.TestCase):
	def test_each_line_has_its_own_stage_and_who_is_outstanding(self):
		rows = {(r["material_request"], r["item_code"]): r for r in rows_of(view="Item-wise")}
		self.assertEqual(rows[("MR-1", "bolt")]["stage"], "All Quotations Received")
		self.assertEqual(rows[("MR-1", "bolt")]["pending_names"], "")
		self.assertEqual(rows[("MR-1", "nut")]["stage"], "Quotations Partly Received")
		self.assertEqual(rows[("MR-1", "nut")]["pending_names"], "Supplier Three, Supplier Two")
		self.assertEqual(rows[("MR-1", "nut")]["quotations"], "SQ-1")
		self.assertEqual(rows[("MR-1", "bolt")]["quotations"], "SQ-1, SQ-2, SQ-3")
		self.assertEqual(rows[("MR-2", "washer")]["stage"], "No RFQ")
		self.assertEqual(rows[("MR-6", "cog")]["ordered_qty"], 40.0)

	def test_a_line_with_no_row_link_is_found_by_its_item_code(self):
		chain = Chain()
		chain.requests, chain.items = chain.requests[:1], chain.items[:2]
		chain.tables["Request for Quotation Item"] = [rfq_line("RL-9", "RFQ-1", "MR-1", "", "bolt")]
		chain.tables["Supplier Quotation Item"] = [quote_line("SQ-1", "RFQ-1", "", "bolt")]
		by_item = {r["item_code"]: r for r in rows_of(chain, view="Item-wise")}
		self.assertEqual(by_item["bolt"]["rfqs"], "RFQ-1")
		self.assertEqual(by_item["bolt"]["suppliers_answered"], 1)
		self.assertEqual(by_item["nut"]["rfqs"], "")


class TestTheRfqs(unittest.TestCase):
	def setUp(self):
		self.rows = {r["request_for_quotation"]: r for r in rows_of(view="RFQ-wise")}

	def test_an_rfq_is_complete_when_every_supplier_invited_has_answered_it(self):
		# RFQ-1: three invited, all three quoted something on it, though only one quoted the nut
		row = self.rows["RFQ-1"]
		self.assertEqual((row["stage"], row["complete"]), ("All Quotations Received", 1))
		self.assertEqual((row["suppliers_invited"], row["suppliers_answered"], row["quotations"]), (3, 3, 3))
		self.assertEqual(row["pending_names"], "")
		self.assertEqual(row["material_requests"], "MR-1")

	def test_a_draft_one_and_one_nobody_answered(self):
		self.assertEqual(self.rows["RFQ-3"]["stage"], "RFQ Not Submitted")
		self.assertEqual(self.rows["RFQ-3"]["complete"], 0)
		row = self.rows["RFQ-4"]
		self.assertEqual(row["stage"], "Awaiting Quotations")
		self.assertEqual(row["pending_names"], "Supplier One, Supplier Two")

	def test_the_compare_link_is_always_there(self):
		self.assertTrue(all(r["compare"] == "Compare" for r in self.rows.values()))

	def test_one_supplier_answering_of_two_is_partly_received(self):
		chain = Chain()
		chain.tables["Supplier Quotation Item"] = [quote_line("SQ-X", "RFQ-4", "RL-4", "pin")]
		chain.tables["Supplier Quotation"] = [D(name="SQ-X", supplier="S1", supplier_name="Supplier One")]
		rows = {r["request_for_quotation"]: r for r in rows_of(chain=chain, view="RFQ-wise")}
		self.assertEqual(rows["RFQ-4"]["stage"], "Quotations Partly Received")
		self.assertEqual(rows["RFQ-4"]["pending_names"], "Supplier Two")
		self.assertEqual(rows["RFQ-4"]["answered_names"], "Supplier One")


class TestTheFilters(unittest.TestCase):
	def test_the_stage_filter_keeps_that_stage_in_every_view(self):
		self.assertEqual([r["material_request"] for r in rows_of(stage="No RFQ")], ["MR-2"])
		self.assertEqual([r["material_request"] for r in rows_of(stage="Ordered")], ["MR-5"])
		self.assertEqual([r["item_code"] for r in rows_of(stage="No RFQ", view="Item-wise")], ["washer"])
		self.assertEqual([r["request_for_quotation"] for r in rows_of(stage="Awaiting Quotations", view="RFQ-wise")], ["RFQ-4"])

	def test_all_and_an_unknown_stage_keep_everything(self):
		self.assertEqual(len(rows_of(stage="All")), 6)
		self.assertEqual(len(rows_of(stage="nonsense")), 6)

	def test_the_supplier_filter_keeps_the_requests_that_invited_that_supplier(self):
		self.assertEqual(sorted(r["material_request"] for r in rows_of(supplier="S3")), ["MR-1"])
		self.assertEqual(sorted(r["material_request"] for r in rows_of(supplier="S1")), ["MR-1", "MR-3", "MR-4", "MR-5"])
		self.assertEqual(sorted(r["request_for_quotation"] for r in rows_of(supplier="S3", view="RFQ-wise")), ["RFQ-1"])

	def test_an_unknown_view_is_the_request_view(self):
		self.assertEqual(tracker.resolve_view(D(view="nonsense")), tracker.VIEW_MR)

	def test_the_period_must_make_sense(self):
		with self.assertRaises(frappe.ValidationError):
			tracker.validate_filters(D(company="SFB", from_date="2026-10-31", to_date="2026-10-01"))
		with self.assertRaises(frappe.ValidationError):
			tracker.validate_filters(D(from_date="2026-10-01", to_date="2026-10-31"))


class TestTheCardsAndTheChart(unittest.TestCase):
	def test_the_cards_count_requests_by_stage_whatever_the_stage_filter(self):
		for filters in ({}, {"stage": "No RFQ"}):
			columns, rows, message, chart, summary = execute(**filters)
			by_label = {card["label"]: card["value"] for card in summary}
			self.assertEqual(by_label["Material Requests"], 6)
			self.assertEqual(by_label["No RFQ Yet"], 1)
			self.assertEqual(by_label["Waiting for Quotations"], 2)
			self.assertEqual(by_label["Quotations Partly Received"], 1)
			self.assertEqual(by_label["All Quotations Received"], 0)
			self.assertEqual(by_label["Ordered or Partly Ordered"], 2)

	def test_the_chart_has_a_bar_for_every_stage(self):
		chart = execute()[3]
		self.assertEqual(chart["data"]["labels"], list(tracker.STAGES))
		self.assertEqual(chart["data"]["datasets"][0]["values"], [1, 1, 1, 1, 0, 1, 1])

	def test_every_key_a_row_carries_has_a_column(self):
		for view, columns in (
			("Material Request-wise", tracker.get_request_columns),
			("RFQ-wise", tracker.get_rfq_columns),
			("Item-wise", tracker.get_item_columns),
		):
			names = {column["fieldname"] for column in columns()}
			for row in rows_of(view=view):
				self.assertLessEqual(set(row), names, view)
