# sf_trading/tests/test_purchase_orders_by_verbal_quotation.py
"""The Purchase Orders by Verbal Quotation report.

    bench --site <site> run-tests --module sf_trading.tests.test_purchase_orders_by_verbal_quotation

No database: the orders and their links are handed in, so what is tested is the grouping, the figures and the filters.
"""

import json
import os
import unittest
from unittest.mock import MagicMock, patch

import frappe

from sf_trading.sf_trading.report.purchase_orders_by_verbal_quotation import (
	purchase_orders_by_verbal_quotation as rpt,
)

CURRENCY = "BHD"


def po(name, verbal=0, total=100.0, currency=CURRENCY, **extra):
	order = frappe._dict(
		name=name,
		transaction_date="2026-10-01",
		supplier="Alpha Steel",
		currency=currency,
		grand_total=total,
		base_grand_total=total,
		status="To Receive and Bill",
		docstatus=1,
		owner="buyer@example.com",
		custom_verbal=verbal,
		custom_verbal_comment="Phoned in by the supplier" if verbal else None,
	)
	order.update(extra)
	return order


LINKS = {
	"PO-2": {"material_requests": ["MR-1", "MR-2"], "quotations": ["SQ-1", "SQ-2", "SQ-3"]},
	"PO-3": {"material_requests": ["MR-3"], "quotations": []},
}


def orders():
	return [po("PO-1", verbal=1, total=300), po("PO-2", total=1200), po("PO-3", verbal=1, total=50, currency="USD")]


def names(rows):
	return [r["purchase_order"] for r in rows]


class TestBlocks(unittest.TestCase):
	def test_the_two_kinds_are_listed_apart_each_under_its_heading(self):
		rows = rpt.build_rows(orders(), LINKS, CURRENCY)
		self.assertEqual(
			names(rows),
			["With Verbal Quotation (2)", "PO-1", "PO-3", "Without Verbal Quotation (1)", "PO-2", "Total (3)"],
		)

	def test_each_heading_carries_its_value_and_the_total_closes_the_report(self):
		rows = {r["purchase_order"]: r for r in rpt.build_rows(orders(), LINKS, CURRENCY)}
		self.assertEqual(rows["With Verbal Quotation (2)"]["base_grand_total"], 350)
		self.assertEqual(rows["Without Verbal Quotation (1)"]["base_grand_total"], 1200)
		self.assertEqual(rows["Total (3)"]["base_grand_total"], 1550)
		for key in ("With Verbal Quotation (2)", "Without Verbal Quotation (1)", "Total (3)"):
			self.assertEqual(rows[key]["is_group"], 1)
			self.assertEqual(rows[key]["company_currency"], CURRENCY)

	def test_only_the_picked_kind_is_shown_and_there_is_no_total(self):
		with_rows = rpt.build_rows(orders(), LINKS, CURRENCY, rpt.WITH_VERBAL)
		self.assertEqual(names(with_rows), ["With Verbal Quotation (2)", "PO-1", "PO-3"])
		without = rpt.build_rows(orders(), LINKS, CURRENCY, rpt.WITHOUT_VERBAL)
		self.assertEqual(names(without), ["Without Verbal Quotation (1)", "PO-2"])

	def test_a_block_with_nothing_still_says_so(self):
		rows = rpt.build_rows([po("PO-9")], {}, CURRENCY)
		self.assertEqual(names(rows)[0], "With Verbal Quotation (0)")

	def test_no_orders_gives_two_empty_blocks(self):
		rows = rpt.build_rows([], {}, CURRENCY)
		self.assertEqual(names(rows), ["With Verbal Quotation (0)", "Without Verbal Quotation (0)", "Total (0)"])

	def test_the_blocks_add_up_to_the_total(self):
		rows = rpt.build_rows(orders(), LINKS, CURRENCY)
		headings = [r for r in rows if r["is_group"]]
		self.assertEqual(sum(r["base_grand_total"] for r in headings[:2]), headings[2]["base_grand_total"])


class TestARow(unittest.TestCase):
	def _row(self, order):
		return rpt.order_row(order, LINKS, CURRENCY)

	def test_a_verbal_order_shows_its_comment_and_no_quotations(self):
		row = self._row(po("PO-3", verbal=1, currency="USD"))
		self.assertEqual(row["verbal"], 1)
		self.assertEqual(row["verbal_comment"], "Phoned in by the supplier")
		self.assertEqual(row["quotation_count"], 0)
		self.assertEqual(row["supplier_quotations"], "")
		self.assertEqual(row["material_requests"], "MR-3")

	def test_an_order_with_quotations_lists_them_and_its_requests(self):
		row = self._row(po("PO-2"))
		self.assertEqual(row["verbal"], 0)
		self.assertEqual(row["quotation_count"], 3)
		self.assertEqual(row["supplier_quotations"], "SQ-1, SQ-2, SQ-3")
		self.assertEqual(row["material_requests"], "MR-1, MR-2")

	def test_an_order_with_no_links_at_all_is_blank_not_an_error(self):
		row = self._row(po("PO-7"))
		self.assertEqual((row["quotation_count"], row["supplier_quotations"], row["material_requests"]), (0, "", ""))

	def test_import_or_local_comes_from_the_currency(self):
		self.assertEqual(self._row(po("PO-1", currency="USD"))["purchase_type"], "Import")
		self.assertEqual(self._row(po("PO-1"))["purchase_type"], "Local")

	def test_the_amount_is_kept_in_both_currencies(self):
		row = self._row(po("PO-1", total=10, currency="USD", base_grand_total=3.77))
		self.assertEqual((row["grand_total"], row["base_grand_total"], row["currency"]), (10, 3.77, "USD"))
		self.assertEqual(row["company_currency"], CURRENCY)

	def test_an_order_with_no_verbal_field_is_not_verbal(self):
		order = po("PO-1")
		del order["custom_verbal"]
		self.assertEqual(self._row(order)["verbal"], 0)
		self.assertFalse(rpt.is_verbal(order))

	def test_a_null_tick_is_not_verbal(self):
		self.assertFalse(rpt.is_verbal(po("PO-1", verbal=None)))


class TestFigures(unittest.TestCase):
	def test_the_cards_count_and_value_each_kind(self):
		cards = {c["label"]: c["value"] for c in rpt.summary(orders(), CURRENCY)}
		self.assertEqual(cards["With Verbal Quotation"], 2)
		self.assertEqual(cards["Without Verbal Quotation"], 1)
		self.assertEqual(cards["Value With Verbal Quotation"], 350)
		self.assertEqual(cards["Value Without Verbal Quotation"], 1200)
		self.assertEqual(cards["Orders on a Verbal Quotation"], "66.7%")

	def test_no_orders_has_no_share_to_report(self):
		cards = {c["label"]: c["value"] for c in rpt.summary([], CURRENCY)}
		self.assertEqual(cards["Orders on a Verbal Quotation"], "-")
		self.assertEqual(cards["With Verbal Quotation"], 0)

	def test_value_cards_are_in_the_company_currency(self):
		for card in rpt.summary(orders(), CURRENCY):
			if card["datatype"] == "Currency":
				self.assertEqual(card["currency"], CURRENCY)

	def test_the_chart_splits_the_orders_by_kind(self):
		chart = rpt.chart(orders())
		self.assertEqual(chart["data"]["datasets"][0]["values"], [2, 1])
		self.assertEqual(len(chart["data"]["labels"]), 2)
		self.assertIsNone(rpt.chart([]))


class TestColumns(unittest.TestCase):
	def test_the_fieldnames_are_unique_and_every_row_key_is_a_column_or_a_currency_source(self):
		columns = rpt.columns()
		fieldnames = [c["fieldname"] for c in columns]
		self.assertEqual(len(fieldnames), len(set(fieldnames)))
		row = rpt.order_row(po("PO-1", verbal=1), LINKS, CURRENCY)
		extras = {k for k in row if k not in fieldnames}
		# the keys that are not columns: what the formatter and the currency columns read
		self.assertEqual(extras, {"purchase_order", "company_currency", "is_group"} - set(fieldnames))

	def test_the_currency_columns_read_their_currency_from_the_row(self):
		by_name = {c["fieldname"]: c for c in rpt.columns()}
		self.assertEqual(by_name["grand_total"]["options"], "currency")
		self.assertEqual(by_name["base_grand_total"]["options"], "company_currency")

	def test_the_report_is_registered_for_purchasing_roles(self):
		path = os.path.join(os.path.dirname(rpt.__file__), "purchase_orders_by_verbal_quotation.json")
		meta = json.load(open(path))
		self.assertEqual(meta["report_type"], "Script Report")
		self.assertEqual(meta["ref_doctype"], "Purchase Order")
		self.assertEqual(meta["name"], "Purchase Orders by Verbal Quotation")
		self.assertEqual({r["role"] for r in meta["roles"]}, {"Purchase Manager", "Purchase User", "Accounts Manager", "System Manager"})


class TestFilters(unittest.TestCase):
	def _meta(self, **has):
		meta = MagicMock()
		meta.has_field.side_effect = lambda name: has.get(name, True)
		return meta

	def _filters(self, meta=None, **filters):
		return rpt.order_filters(frappe._dict(company="C", **filters), CURRENCY, meta or self._meta())

	def test_draft_and_submitted_are_listed_by_default_never_cancelled(self):
		self.assertIn(["docstatus", "in", [0, 1]], self._filters())

	def test_a_document_status_is_exact(self):
		for label, value in (("Draft", 0), ("Submitted", 1), ("Cancelled", 2)):
			self.assertIn(["docstatus", "=", value], self._filters(docstatus=label))

	def test_dates_supplier_branch_and_user(self):
		found = self._filters(from_date="2026-10-01", to_date="2026-10-31", supplier="Alpha Steel", branch="SFSB", user="a@b.c")
		for item in (
			["transaction_date", ">=", "2026-10-01"],
			["transaction_date", "<=", "2026-10-31"],
			["supplier", "=", "Alpha Steel"],
			["branch", "=", "SFSB"],
			["owner", "=", "a@b.c"],
		):
			self.assertIn(item, found)

	def test_branch_is_ignored_on_a_site_without_the_field(self):
		found = self._filters(self._meta(branch=False), branch="SFSB")
		self.assertNotIn(["branch", "=", "SFSB"], found)

	def test_purchase_type_is_decided_by_currency(self):
		self.assertIn(["currency", "!=", CURRENCY], self._filters(purchase_type="Import"))
		self.assertIn(["currency", "=", CURRENCY], self._filters(purchase_type="Local"))

	def test_the_quotation_type_filters_on_the_verbal_tick(self):
		self.assertIn(["custom_verbal", "=", 1], self._filters(quotation_type="With Verbal Quotation"))
		self.assertIn(["custom_verbal", "!=", 1], self._filters(quotation_type="Without Verbal Quotation"))
		found = self._filters(quotation_type="All")
		self.assertFalse([f for f in found if f[0] == "custom_verbal"])

	def test_a_site_without_the_verbal_field_filters_nothing_on_it(self):
		found = self._filters(self._meta(custom_verbal=False), quotation_type="With Verbal Quotation")
		self.assertFalse([f for f in found if f[0] == "custom_verbal"])


class TestRunning(unittest.TestCase):
	def test_a_company_is_required(self):
		with self.assertRaises(frappe.ValidationError):
			rpt.execute({})

	def test_an_unknown_quotation_type_is_refused(self):
		with self.assertRaises(frappe.ValidationError):
			rpt.execute({"company": "C", "quotation_type": "Verbally"})

	def test_the_whole_report_comes_back_in_the_five_parts_a_script_report_returns(self):
		with (
			patch.object(frappe, "get_cached_value", return_value=CURRENCY),
			patch.object(rpt, "fetch_orders", return_value=orders()),
			patch.object(rpt, "fetch_links", return_value=LINKS),
		):
			columns, rows, message, chart, summary = rpt.execute({"company": "C", "quotation_type": "All"})
		self.assertEqual(len(columns), 15)
		self.assertEqual(len(rows), 6)
		self.assertIsNone(message)
		self.assertEqual(chart["type"], "donut")
		self.assertEqual(len(summary), 5)

	def test_a_blank_quotation_type_means_all(self):
		with (
			patch.object(frappe, "get_cached_value", return_value=CURRENCY),
			patch.object(rpt, "fetch_orders", return_value=orders()),
			patch.object(rpt, "fetch_links", return_value=LINKS),
		):
			_c, rows, *_rest = rpt.execute({"company": "C", "quotation_type": ""})
		self.assertEqual(len(rows), 6)

	def test_the_links_are_fetched_for_exactly_the_orders_listed(self):
		fetched = MagicMock(return_value={})
		with (
			patch.object(frappe, "get_cached_value", return_value=CURRENCY),
			patch.object(rpt, "fetch_orders", return_value=orders()),
			patch.object(rpt, "fetch_links", fetched),
		):
			rpt.execute({"company": "C"})
		fetched.assert_called_once_with(["PO-1", "PO-2", "PO-3"])

	def test_no_orders_asks_for_no_links(self):
		with patch.object(frappe, "qb", MagicMock()) as qb:
			self.assertEqual(rpt.fetch_links([]), {})
		qb.DocType.assert_not_called()
