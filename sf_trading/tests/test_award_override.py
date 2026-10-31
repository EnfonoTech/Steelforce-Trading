# sf_trading/tests/test_award_override.py
"""A Purchase Order that departs from the quotation comparison's recommendation needs a reason.

The comparison and the quotation lines are patched, so no data is needed and nothing is written (never run a test module
against a real site -- ERPNext's before_tests wipes Item Price):

    bench --site <scratch-site> run-tests --module sf_trading.tests.test_award_override
"""

from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase

from sf_trading import award_override
from sf_trading.tests.test_supplier_quotation_comparison_extended import asked, quote, run


class FakePO(frappe._dict):
	"""The few things of a Purchase Order the check touches."""

	def set(self, key, value):
		self[key] = value


def purchase_order(*rows, **extra):
	po = FakePO(
		company="SFB",
		docstatus=0,
		meta=frappe._dict(has_field=lambda name: True),
		flags=frappe._dict(),
		items=list(rows),
		custom_verbal=0,
		is_internal_supplier=0,
	)
	po.update(extra)
	return po


def row(idx, quotation_item, item="bolt"):
	return frappe._dict(idx=idx, item_code=item, supplier_quotation_item=quotation_item)


def lines(*names, rfq="RFQ-1", item="bolt"):
	return [frappe._dict(name=name, request_for_quotation=rfq, item_code=item) for name in names]


def comparison():
	"""S1 is the cheapest quotation of 100 bolts, S2 dearer, S3 expired."""
	return run(
		[
			quote("S1", "bolt", 100, 1.0, quotation="SQ-S1"),
			quote("S2", "bolt", 100, 1.3, quotation="SQ-S2"),
			quote("S3", "bolt", 100, 0.5, quotation="SQ-S3", status="Expired"),
		],
		requested=asked(bolt=100),
	)


def check(po, found_lines, compared=None):
	with (
		patch.object(award_override, "fetch_in", return_value=found_lines),
		patch.object(award_override, "comparison_for", return_value=compared or comparison()) as compare,
	):
		award_override.validate(po)
	return compare


class TestDeparturesNeedAReason(FrappeTestCase):
	def test_an_order_that_follows_the_recommendation_carries_nothing(self):
		po = purchase_order(row(1, "SQ-S1-bolt"))
		check(po, lines("SQ-S1-bolt"))
		self.assertEqual(po[award_override.ITEMS_FIELD], "")

	def test_a_dearer_quotation_is_refused_without_a_reason(self):
		po = purchase_order(row(1, "SQ-S2-bolt"))
		with self.assertRaises(frappe.ValidationError):
			check(po, lines("SQ-S2-bolt"))
		self.assertIn("Row 1, bolt: bought from S2 Trading at 1.3 a unit; recommended: S1 Trading at 1.0 a unit", po[award_override.ITEMS_FIELD])

	def test_with_a_reason_the_order_saves_and_the_departure_is_written_on_it(self):
		po = purchase_order(row(1, "SQ-S2-bolt"), **{award_override.REASON_FIELD: "Only S2 can deliver this week"})
		check(po, lines("SQ-S2-bolt"))
		self.assertIn("S2 Trading", po[award_override.ITEMS_FIELD])

	def test_a_reason_of_only_spaces_is_no_reason(self):
		po = purchase_order(row(1, "SQ-S2-bolt"), **{award_override.REASON_FIELD: "   "})
		with self.assertRaises(frappe.ValidationError):
			check(po, lines("SQ-S2-bolt"))

	def test_an_expired_quotation_is_a_departure(self):
		po = purchase_order(row(1, "SQ-S3-bolt"))
		with self.assertRaises(frappe.ValidationError):
			check(po, lines("SQ-S3-bolt"))

	def test_a_quotation_that_is_not_in_the_comparison_is_a_departure(self):
		po = purchase_order(row(1, "SQ-GONE-bolt"))
		with self.assertRaises(frappe.ValidationError):
			check(po, lines("SQ-GONE-bolt"))
		self.assertIn("its quotation is not in the comparison", po[award_override.ITEMS_FIELD])

	def test_each_departing_row_is_listed_and_a_following_row_is_not(self):
		po = purchase_order(row(1, "SQ-S1-bolt"), row(2, "SQ-S2-bolt"), row(3, "SQ-S3-bolt"))
		with self.assertRaises(frappe.ValidationError):
			check(po, lines("SQ-S1-bolt", "SQ-S2-bolt", "SQ-S3-bolt"))
		listed = po[award_override.ITEMS_FIELD].split("\n")
		self.assertEqual(len(listed), 2)
		self.assertTrue(listed[0].startswith("Row 2,"))
		self.assertTrue(listed[1].startswith("Row 3,"))

	def test_a_supplier_that_covers_the_remainder_of_a_split_award_follows_the_recommendation(self):
		split = run(
			[quote("S1", "bolt", 40, 1.0, quotation="SQ-S1"), quote("S2", "bolt", 100, 1.3, quotation="SQ-S2")],
			requested=asked(bolt=100),
		)
		po = purchase_order(row(1, "SQ-S1-bolt"), row(2, "SQ-S2-bolt"))
		check(po, lines("SQ-S1-bolt", "SQ-S2-bolt"), compared=split)
		self.assertEqual(po[award_override.ITEMS_FIELD], "")

	def test_the_comparison_is_asked_once_per_request(self):
		po = purchase_order(row(1, "SQ-S1-bolt"), row(2, "SQ-S1-bolt"))
		compare = check(po, lines("SQ-S1-bolt"))
		compare.assert_called_once_with("SFB", "RFQ-1")


class TestWhatIsNotChecked(FrappeTestCase):
	def test_a_verbal_order_an_internal_supplier_and_a_data_fix_are_not_checked(self):
		for extra in ({"custom_verbal": 1}, {"is_internal_supplier": 1}):
			po = purchase_order(row(1, "SQ-S2-bolt"), **extra)
			check(po, lines("SQ-S2-bolt"))
			self.assertEqual(po[award_override.ITEMS_FIELD], "")
		po = purchase_order(row(1, "SQ-S2-bolt"))
		po.flags.ignore_award_override = True
		check(po, lines("SQ-S2-bolt"))
		self.assertEqual(po[award_override.ITEMS_FIELD], "")

	def test_a_cancelled_order_is_not_checked(self):
		po = purchase_order(row(1, "SQ-S2-bolt"), docstatus=2)
		check(po, lines("SQ-S2-bolt"))
		self.assertEqual(po[award_override.ITEMS_FIELD], "")

	def test_a_line_with_no_quotation_and_a_quotation_with_no_request_are_not_checked(self):
		po = purchase_order(row(1, None), row(2, "SQ-S2-bolt"))
		check(po, lines("SQ-S2-bolt", rfq=""))
		self.assertEqual(po[award_override.ITEMS_FIELD], "")

	def test_an_empty_comparison_is_not_a_departure(self):
		empty = run([], requested=asked(bolt=100))
		po = purchase_order(row(1, "SQ-S2-bolt"))
		check(po, lines("SQ-S2-bolt"), compared=empty)
		self.assertEqual(po[award_override.ITEMS_FIELD], "")

	def test_a_site_that_has_not_migrated_the_fields_is_left_alone(self):
		po = purchase_order(row(1, "SQ-S2-bolt"))
		po.meta = frappe._dict(has_field=lambda name: False)
		with patch.object(award_override, "fetch_in") as read:
			award_override.validate(po)
		read.assert_not_called()


class TestTheFields(FrappeTestCase):
	def test_both_fields_are_made_on_the_purchase_order_with_the_reason_always_visible(self):
		with patch.object(award_override, "create_custom_fields") as make:
			award_override.ensure_custom_fields()
		fields = make.call_args.args[0]["Purchase Order"]
		by = {f["fieldname"]: f for f in fields}
		self.assertEqual(set(by), {award_override.ITEMS_FIELD, award_override.REASON_FIELD})
		self.assertNotIn("depends_on", by[award_override.REASON_FIELD])
		self.assertEqual(by[award_override.ITEMS_FIELD]["read_only"], 1)
