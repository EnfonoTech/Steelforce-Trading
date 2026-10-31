# sf_trading/tests/test_supplier_quotation_comparison_extended.py
"""Supplier Quotation Comparison Extended: the footing quotations are compared on, who is recommended, the supplier
summary, the gaps, the procurement-rule check and the Purchase Orders raised from the recommendation.

Everything the report reads from the database is patched, so no data is needed and nothing is written (never run a test
module against a real site -- ERPNext's before_tests wipes Item Price):

    bench --site <scratch-site> run-tests --module sf_trading.tests.test_supplier_quotation_comparison_extended
"""

from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase

from sf_trading.sf_trading.report.supplier_quotation_comparison_extended import (
	supplier_quotation_comparison_extended as report,
)

TODAY = "2026-10-31"
COMPANY_CURRENCY = "BHD"
RULE = frappe._dict(enforce=True, above=500.0, local_min=3, import_min=2, verbal_skips=True)
RULE_OFF = frappe._dict(enforce=False, above=500.0, local_min=3, import_min=2, verbal_skips=True)


def quote(
	supplier,
	item,
	qty,
	rate,
	currency="BHD",
	conversion_rate=1,
	quotation=None,
	rfq="RFQ-1",
	status="Submitted",
	docstatus=1,
	valid_till="2026-12-31",
	lead=0,
	date="2026-10-20",
	uom="Nos",
	factor=1,
	total=100.0,
	rfq_item=None,
):
	"""A quotation line as the report builds it, from the header and line a read would return."""
	quotation = quotation or f"SQ-{supplier}"
	header = frappe._dict(
		name=quotation,
		supplier=supplier,
		supplier_name=f"{supplier} Trading",
		currency=currency,
		conversion_rate=conversion_rate,
		transaction_date=date,
		valid_till=valid_till,
		status=status,
		docstatus=docstatus,
		base_grand_total=total,
	)
	line = frappe._dict(
		name=f"{quotation}-{item}",
		parent=quotation,
		item_code=item,
		item_name=item.title(),
		qty=qty,
		uom=uom,
		stock_uom="Nos",
		conversion_factor=factor,
		stock_qty=qty * factor,
		rate=rate,
		net_rate=rate,
		amount=qty * rate,
		net_amount=qty * rate,
		base_net_amount=qty * rate * conversion_rate,
		base_amount=qty * rate * conversion_rate,
		lead_time_days=lead,
		request_for_quotation=rfq,
		request_for_quotation_item=rfq_item or "",
	)
	return report.build_quote(header, line, COMPANY_CURRENCY)


def asked(**items):
	"""{rfq: {item: requested stock qty}} in the shape get_request_scope returns."""
	return {
		"RFQ-1": {
			code: frappe._dict(item_name=code.title(), qty=qty, stock_uom="Nos") for code, qty in items.items()
		}
	}


def invited(*suppliers):
	return {
		"RFQ-1": [frappe._dict(supplier=s, supplier_name=f"{s} Trading", quote_status="Pending") for s in suppliers]
	}


def run(quotes, requested=None, invites=None, rule=None, last_rates=None, abroad=(), defaults=None, **filters):
	"""compare() with the database reads replaced."""
	filters = frappe._dict(company="SFB", from_date="2026-10-01", to_date="2026-10-31", **filters)
	rfqs = sorted({q.rfq for q in quotes if q.rfq} | set(requested or {}) | set(invites or {}))
	with (
		patch.object(report, "company_profile", return_value=(COMPANY_CURRENCY, "Bahrain")),
		patch.object(report, "suppliers_abroad", return_value=set(abroad)),
		patch.object(report, "get_quotes", return_value=quotes),
		patch.object(report, "get_request_names", return_value=rfqs),
		patch.object(report, "get_request_scope", return_value=(requested or {}, invites or {})),
		patch.object(report, "get_last_rates", return_value=last_rates or {}),
		patch.object(report, "procurement_rules", return_value=rule or RULE_OFF),
		patch.object(report, "default_charges", return_value=defaults or []),
		patch.object(report, "nowdate", return_value=TODAY),
	):
		return report.compare(filters)


def winner(comparison, item, rfq="RFQ-1"):
	return next((q for q in comparison.groups[(rfq, item)] if q.recommended), None)


class TestTheFooting(FrappeTestCase):
	def test_price_is_per_stock_unit_so_a_carton_compares_with_a_piece(self):
		# 10 cartons of 12 at 24.0 a carton -> 2.0 a piece
		carton = quote("S1", "bolt", qty=10, rate=24.0, uom="Carton", factor=12)
		piece = quote("S2", "bolt", qty=120, rate=2.5)
		self.assertAlmostEqual(carton.unit_cost_base, 2.0)
		self.assertAlmostEqual(piece.unit_cost_base, 2.5)

	def test_net_rate_after_discount_is_what_is_compared(self):
		header = frappe._dict(
			name="SQ-1", supplier="S1", supplier_name="S1", currency="BHD", conversion_rate=1,
			transaction_date="2026-10-20", valid_till=None, status="Submitted", docstatus=1, base_grand_total=0,
		)
		line = frappe._dict(
			name="L1", item_code="bolt", item_name="Bolt", qty=10, uom="Nos", stock_uom="Nos", conversion_factor=1,
			stock_qty=10, rate=10.0, amount=100.0, net_rate=9.0, net_amount=90.0, base_net_amount=90.0,
			base_amount=100.0,
		)
		self.assertAlmostEqual(report.build_quote(header, line, "BHD").unit_cost_base, 9.0)

	def test_converted_at_the_rate_on_the_quotation(self):
		usd = quote("S2", "bolt", qty=100, rate=2.6, currency="USD", conversion_rate=0.376)
		self.assertAlmostEqual(usd.unit_cost_txn, 2.6)
		self.assertAlmostEqual(usd.unit_cost_base, 0.9776)

	def test_a_line_with_no_price_is_not_priced(self):
		self.assertFalse(quote("S1", "bolt", qty=10, rate=0).priced)
		self.assertTrue(quote("S1", "bolt", qty=10, rate=0.5).priced)

	def test_lead_time_zero_means_not_stated(self):
		self.assertIsNone(quote("S1", "bolt", 1, 1, lead=0).lead_time_days)
		self.assertEqual(quote("S1", "bolt", 1, 1, lead=7).lead_time_days, 7)


class TestOriginAndImportCharges(FrappeTestCase):
	def test_another_currency_is_an_import_the_company_currency_is_local(self):
		usd = quote("S2", "bolt", 1, 1, currency="USD", conversion_rate=0.376)
		local = quote("S1", "bolt", 1, 1)
		self.assertEqual(report.quote_origin(usd, "BHD", set()), report.IMPORT)
		self.assertEqual(report.quote_origin(local, "BHD", set()), report.LOCAL)

	def test_a_supplier_abroad_quoting_in_company_currency_is_still_an_import(self):
		local_priced = quote("S4", "bolt", 1, 1)
		self.assertEqual(report.quote_origin(local_priced, "BHD", {"S4"}), report.IMPORT)

	def test_charges_go_on_imports_only(self):
		usd = quote("S2", "bolt", 100, 2.6, currency="USD", conversion_rate=0.376)
		usd.origin = report.IMPORT
		local = quote("S1", "bolt", 100, 1.0)
		local.origin = report.LOCAL
		plan = report.typed_plan(10)
		self.assertAlmostEqual(report.estimated_charges(usd, plan)[0], 0.09776)
		self.assertEqual(report.estimated_charges(local, plan), (0.0, ""))
		self.assertEqual(report.estimated_charges(usd, report.typed_plan(0)), (0.0, ""))

	def test_the_estimate_can_turn_a_cheap_import_into_the_dearer_quotation(self):
		quotes = [
			quote("S1", "bolt", 100, 1.0, quotation="SQ-S1"),
			quote("S2", "bolt", 100, 2.6, currency="USD", conversion_rate=0.376, quotation="SQ-S2"),
		]
		self.assertEqual(winner(run(quotes, import_charges_pct=0), "bolt").supplier, "S2")
		quotes = [
			quote("S1", "bolt", 100, 1.0, quotation="SQ-S1"),
			quote("S2", "bolt", 100, 2.6, currency="USD", conversion_rate=0.376, quotation="SQ-S2"),
		]
		self.assertEqual(winner(run(quotes, import_charges_pct=10), "bolt").supplier, "S1")

	def test_an_origin_filter_keeps_one_half(self):
		def fresh():
			return [
				quote("S1", "bolt", 100, 1.0, quotation="SQ-S1"),
				quote("S2", "bolt", 100, 2.6, currency="USD", conversion_rate=0.376, quotation="SQ-S2"),
			]

		only_import = run(fresh(), purchase_origin=report.ORIGIN_IMPORT)
		self.assertEqual({q.supplier for q in only_import.quotes}, {"S2"})
		only_local = run(fresh(), purchase_origin=report.ORIGIN_LOCAL)
		self.assertEqual({q.supplier for q in only_local.quotes}, {"S1"})
		both = run(fresh())
		self.assertEqual({q.supplier for q in both.quotes}, {"S1", "S2"})


def charge(name, value, basis="% of Price", applies_to="Import"):
	return frappe._dict(charge=name, basis=basis, value=value, applies_to=applies_to)


class TestDefaultLandedCosts(FrappeTestCase):
	"""The charges added to a quotation come from the company's table, a typed percent, or nowhere."""

	def test_defaults_add_up_per_the_basis_and_the_half_they_apply_to(self):
		plan = [
			charge("Freight", 3),
			charge("Customs Duty", 5),
			charge("Clearance", 0.02, basis="Per Stock Unit"),
			charge("Local delivery", 1, applies_to="Local"),
			charge("Handling", 2, applies_to="Import and Local"),
		]
		usd = quote("S2", "bolt", 100, 2.0, currency="USD", conversion_rate=0.5)
		usd.origin = report.IMPORT
		local = quote("S1", "bolt", 100, 1.0)
		local.origin = report.LOCAL
		# import price 1.0: 3% + 5% + 0.02 per unit + 2% = 0.03 + 0.05 + 0.02 + 0.02
		total, detail = report.estimated_charges(usd, plan)
		self.assertAlmostEqual(total, 0.12)
		self.assertEqual(detail, "Freight 3.0%, Customs Duty 5.0%, Clearance 0.02/unit, Handling 2.0%")
		# local price 1.0: 1% delivery + 2% handling
		total, detail = report.estimated_charges(local, plan)
		self.assertAlmostEqual(total, 0.03)
		self.assertEqual(detail, "Local delivery 1.0%, Handling 2.0%")

	def test_company_defaults_come_from_the_table_and_reach_the_ranking(self):
		def fresh():
			return [
				quote("S1", "bolt", 100, 1.0, quotation="SQ-S1"),
				quote("S2", "bolt", 100, 2.6, currency="USD", conversion_rate=0.376, quotation="SQ-S2"),
			]

		table = [charge("Freight", 4), charge("Customs Duty", 6)]
		with_table = run(fresh(), defaults=table)
		self.assertEqual(winner(with_table, "bolt").supplier, "S1")
		usd = next(q for q in with_table.groups[("RFQ-1", "bolt")] if q.supplier == "S2")
		self.assertAlmostEqual(usd.charge_unit, 0.9776 * 0.10)
		self.assertAlmostEqual(usd.landed_unit_cost, 0.9776 * 1.10)
		self.assertEqual(usd.charge_detail, "Freight 4.0%, Customs Duty 6.0%")
		# no table: the price alone
		self.assertEqual(winner(run(fresh()), "bolt").supplier, "S2")

	def test_the_filter_chooses_defaults_typed_or_none(self):
		table = [charge("Freight", 10)]
		with patch.object(report, "default_charges", return_value=table):
			self.assertEqual(len(report.charge_plan(frappe._dict(company="SFB"))), 1)
			self.assertEqual(len(report.charge_plan(frappe._dict(company="SFB", import_charges="No Charges"))), 0)
			typed = report.charge_plan(frappe._dict(company="SFB", import_charges="Typed Percent", import_charges_pct=7))
			self.assertEqual([(r.basis, r.value, r.applies_to) for r in typed], [("% of Price", 7.0, "Import")])
			# a caller that sends only a percent means it
			bare = report.charge_plan(frappe._dict(company="SFB", import_charges_pct=7))
			self.assertEqual(bare[0].value, 7.0)
			# Typed Percent with 0 adds nothing; it does not fall back to the table
			self.assertEqual(report.charge_plan(frappe._dict(company="SFB", import_charges="Typed Percent")), [])

	def test_the_message_says_what_was_added(self):
		plan = [charge("Freight", 3), charge("Clearance", 0.5, basis="Per Stock Unit", applies_to="Import and Local")]
		self.assertEqual(report.describe_plan(plan), "Freight 3.0% (Import); Clearance 0.5/unit (Import and Local)")
		self.assertEqual(report.describe_plan([]), "none")

	def test_a_site_without_the_table_reads_as_no_charges(self):
		with patch.object(report.frappe.db, "table_exists", return_value=False, create=True):
			self.assertEqual(report.default_charges("SFB"), [])


class TestWhoIsRecommended(FrappeTestCase):
	def test_the_lowest_landed_cost_wins_and_is_ranked_first(self):
		c = run([quote("S1", "bolt", 100, 1.2), quote("S2", "bolt", 100, 1.0), quote("S3", "bolt", 100, 1.5)])
		ranked = sorted(c.groups[("RFQ-1", "bolt")], key=lambda q: q.rank)
		self.assertEqual([q.supplier for q in ranked], ["S2", "S1", "S3"])
		self.assertEqual([q.recommended for q in ranked], [1, 0, 0])
		self.assertAlmostEqual(ranked[1].pct_over_best, 20.0)
		self.assertAlmostEqual(ranked[2].pct_over_best, 50.0)
		self.assertAlmostEqual(ranked[0].pct_over_best, 0.0)

	def test_the_winner_shows_its_saving_over_the_runner_up_for_the_requested_quantity(self):
		c = run([quote("S1", "bolt", 100, 1.2), quote("S2", "bolt", 100, 1.0)], requested=asked(bolt=250))
		best = winner(c, "bolt")
		self.assertEqual(best.supplier, "S2")
		self.assertAlmostEqual(best.saving_vs_next, 0.2 * 250)
		self.assertEqual(best.remarks, "Lowest landed cost")

	def test_a_tie_on_cost_goes_to_the_stated_shorter_lead_time(self):
		c = run([quote("S1", "bolt", 100, 1.0, lead=10), quote("S2", "bolt", 100, 1.0, lead=4)])
		self.assertEqual(winner(c, "bolt").supplier, "S2")
		self.assertEqual(winner(c, "bolt").remarks, "Same cost, shorter lead time")

	def test_an_unstated_lead_time_loses_a_tie_to_a_stated_one(self):
		c = run([quote("S1", "bolt", 100, 1.0, lead=0), quote("S2", "bolt", 100, 1.0, lead=30)])
		self.assertEqual(winner(c, "bolt").supplier, "S2")

	def test_a_full_tie_goes_to_the_earlier_quotation(self):
		c = run(
			[
				quote("S1", "bolt", 100, 1.0, lead=5, date="2026-10-25"),
				quote("S2", "bolt", 100, 1.0, lead=5, date="2026-10-20"),
			]
		)
		self.assertEqual(winner(c, "bolt").supplier, "S2")
		self.assertEqual(winner(c, "bolt").remarks, "Same cost and lead time, earlier quotation")

	def test_prices_a_hair_apart_are_not_a_tie(self):
		c = run([quote("S1", "bolt", 100, 1.0002, lead=1), quote("S2", "bolt", 100, 1.0, lead=9)])
		# S1's shorter lead time does not rescue a price that is higher at the fourth decimal
		self.assertEqual(winner(c, "bolt").supplier, "S2")
		self.assertEqual(winner(c, "bolt").remarks, "Lowest landed cost")

	def test_an_expired_stopped_or_unpriced_quotation_is_never_recommended(self):
		c = run(
			[
				quote("S1", "bolt", 100, 0.5, status="Expired"),
				quote("S2", "bolt", 100, 0.6, status="Stopped"),
				quote("S3", "bolt", 100, 0, quotation="SQ-S3"),
				quote("S4", "bolt", 100, 0.7, valid_till="2026-10-30", quotation="SQ-S4"),
				quote("S5", "bolt", 100, 2.0),
			]
		)
		self.assertEqual(winner(c, "bolt").supplier, "S5")
		reasons = {q.supplier: q.remarks for q in c.groups[("RFQ-1", "bolt")]}
		self.assertEqual(reasons["S1"], "Expired")
		self.assertEqual(reasons["S2"], "Stopped")
		self.assertEqual(reasons["S3"], "No price")
		self.assertEqual(reasons["S4"], "Expired")

	def test_valid_till_today_is_still_valid(self):
		c = run([quote("S1", "bolt", 100, 1.0, valid_till=TODAY)])
		self.assertEqual(winner(c, "bolt").supplier, "S1")

	def test_a_draft_quotation_can_be_recommended(self):
		c = run([quote("S1", "bolt", 100, 1.0, status="Draft", docstatus=0)], status=report.STATUS_BOTH)
		self.assertEqual(winner(c, "bolt").supplier, "S1")

	def test_nothing_eligible_means_no_winner(self):
		c = run([quote("S1", "bolt", 100, 1.0, status="Expired")])
		self.assertIsNone(winner(c, "bolt"))

	def test_a_supplier_competes_once_with_its_lower_quotation(self):
		c = run(
			[
				quote("S1", "bolt", 100, 1.5, quotation="SQ-S1-A"),
				quote("S1", "bolt", 100, 1.1, quotation="SQ-S1-B"),
				quote("S2", "bolt", 100, 1.3, quotation="SQ-S2"),
			]
		)
		group = c.groups[("RFQ-1", "bolt")]
		self.assertEqual(sorted(q.rank for q in group if q.rank), [1, 2])
		superseded = next(q for q in group if q.quotation == "SQ-S1-A")
		self.assertIsNone(superseded.rank)
		self.assertIn("Superseded", superseded.remarks)
		self.assertEqual(winner(c, "bolt").quotation, "SQ-S1-B")

	def test_a_lone_quotation_wins_and_says_so(self):
		c = run([quote("S1", "bolt", 100, 1.0)])
		self.assertEqual(winner(c, "bolt").remarks, "Only quotation in the running")
		self.assertIsNone(winner(c, "bolt").saving_vs_next)

	def test_each_item_and_each_request_is_ranked_apart(self):
		c = run(
			[
				quote("S1", "bolt", 100, 1.0, quotation="SQ-1", rfq="RFQ-1"),
				quote("S2", "bolt", 100, 2.0, quotation="SQ-2", rfq="RFQ-2"),
				quote("S1", "nut", 100, 9.0, quotation="SQ-1", rfq="RFQ-1"),
				quote("S2", "nut", 100, 3.0, quotation="SQ-2", rfq="RFQ-1"),
			]
		)
		self.assertEqual(winner(c, "bolt", "RFQ-1").supplier, "S1")
		self.assertEqual(winner(c, "bolt", "RFQ-2").supplier, "S2")
		self.assertEqual(winner(c, "nut", "RFQ-1").supplier, "S2")

	def test_a_short_offer_is_flagged_not_penalised(self):
		c = run([quote("S1", "bolt", 40, 1.0), quote("S2", "bolt", 100, 1.2)], requested=asked(bolt=100))
		short = next(q for q in c.groups[("RFQ-1", "bolt")] if q.supplier == "S1")
		self.assertAlmostEqual(short.qty_offered_pct, 40.0)
		self.assertEqual(winner(c, "bolt").supplier, "S1")
		# both are totalled for the 100 the request asked for
		self.assertAlmostEqual(short.landed_total, 100.0)

	def test_last_purchase_rate_is_compared_before_the_import_estimate(self):
		c = run(
			[quote("S2", "bolt", 100, 2.6, currency="USD", conversion_rate=0.376)],
			last_rates={"bolt": 0.8},
			import_charges_pct=20,
		)
		q = c.groups[("RFQ-1", "bolt")][0]
		self.assertAlmostEqual(q.vs_last_purchase_pct, (0.9776 / 0.8 - 1) * 100)
		self.assertAlmostEqual(q.landed_unit_cost, 0.9776 * 1.2)

	def test_no_last_purchase_rate_leaves_the_comparison_blank(self):
		c = run([quote("S1", "bolt", 100, 1.0)], last_rates={"bolt": 0})
		q = c.groups[("RFQ-1", "bolt")][0]
		self.assertIsNone(q.last_purchase_rate)
		self.assertIsNone(q.vs_last_purchase_pct)


class TestProcurementRuleCheck(FrappeTestCase):
	def label(self, quotes, **kwargs):
		c = run(quotes, rule=RULE, **kwargs)
		return c.groups[("RFQ-1", "bolt")][0].rule_label

	def test_blank_when_the_company_has_no_rule_on(self):
		c = run([quote("S1", "bolt", 100, 1.0, total=9000)], rule=RULE_OFF)
		self.assertEqual(c.groups[("RFQ-1", "bolt")][0].rule_label, "")

	def test_a_local_quotation_above_the_amount_needs_the_local_minimum(self):
		quotes = [quote(s, "bolt", 100, 1.0 + i / 10, total=900) for i, s in enumerate(["S1", "S2"])]
		self.assertEqual(self.label(quotes), "Short (2 of 3)")
		quotes = [quote(s, "bolt", 100, 1.0 + i / 10, total=900) for i, s in enumerate(["S1", "S2", "S3"])]
		self.assertEqual(self.label(quotes), "Met (3 of 3)")

	def test_a_local_quotation_at_or_under_the_amount_needs_none(self):
		self.assertEqual(self.label([quote("S1", "bolt", 100, 1.0, total=500.0)]), "Not required")

	def test_an_import_needs_the_import_minimum_whatever_the_amount(self):
		def usd(supplier, rate):
			return quote(supplier, "bolt", 100, rate, currency="USD", conversion_rate=0.376, total=10)

		self.assertEqual(self.label([usd("S1", 2.0)]), "Short (1 of 2)")
		self.assertEqual(self.label([usd("S1", 2.0), usd("S2", 2.1)]), "Met (2 of 2)")

	def test_only_submitted_quotations_count_expired_ones_included(self):
		quotes = [
			quote("S1", "bolt", 100, 1.0, total=10, currency="USD", conversion_rate=0.376),
			quote("S2", "bolt", 100, 1.1, total=10, currency="USD", conversion_rate=0.376, status="Expired"),
			quote("S3", "bolt", 100, 1.2, total=10, currency="USD", conversion_rate=0.376, status="Draft", docstatus=0),
		]
		self.assertEqual(self.label(quotes, status=report.STATUS_BOTH), "Met (2 of 2)")

	def test_one_supplier_with_two_quotations_counts_once(self):
		quotes = [
			quote("S1", "bolt", 100, 1.0, quotation="SQ-A", total=10, currency="USD", conversion_rate=0.376),
			quote("S1", "bolt", 100, 1.1, quotation="SQ-B", total=10, currency="USD", conversion_rate=0.376),
		]
		self.assertEqual(self.label(quotes), "Short (1 of 2)")

	def test_a_quotation_with_no_rfq_says_so(self):
		self.assertEqual(
			run([quote("S1", "bolt", 100, 1.0, rfq="")], rule=RULE).groups[("", "bolt")][0].rule_label,
			"Not raised from an RFQ",
		)


class TestSupplierSummary(FrappeTestCase):
	"""RFQ-1 asks 100 bolts and 10 nuts of S1 (local), S2 (USD import) and S3 (who never answers).

	With 10% import charges S1 wins the bolts and S2 the nuts, and S1 is the cheapest single award."""

	def setUp(self):
		self.quotes = [
			quote("S1", "bolt", 100, 1.0, quotation="SQ-S1", total=150),
			quote("S1", "nut", 10, 5.0, quotation="SQ-S1", total=150),
			quote("S2", "bolt", 100, 2.6, currency="USD", conversion_rate=0.376, quotation="SQ-S2", total=160),
			quote("S2", "nut", 10, 12.0, currency="USD", conversion_rate=0.376, quotation="SQ-S2", total=160),
		]
		self.comparison = run(
			self.quotes,
			requested=asked(bolt=100, nut=10),
			invites=invited("S1", "S2", "S3"),
			import_charges_pct=10,
		)
		self.rows = {row["supplier"]: row for row in report.supplier_rows(self.comparison)}

	def test_wins_per_item(self):
		self.assertEqual(winner(self.comparison, "bolt").supplier, "S1")
		self.assertEqual(winner(self.comparison, "nut").supplier, "S2")
		self.assertEqual(self.rows["S1"]["items_won"], 1)
		self.assertEqual(self.rows["S2"]["items_won"], 1)

	def test_basket_totals_use_the_requested_quantities_and_the_landed_cost(self):
		self.assertAlmostEqual(self.rows["S1"]["basket_total"], 100 * 1.0 + 10 * 5.0)
		self.assertAlmostEqual(self.rows["S2"]["basket_total"], 100 * 0.9776 * 1.1 + 10 * 4.512 * 1.1)

	def test_the_cheapest_full_basket_is_the_single_award(self):
		self.assertEqual(self.rows["S1"]["basket_rank"], 1)
		self.assertEqual(self.rows["S2"]["basket_rank"], 2)
		self.assertAlmostEqual(self.rows["S1"]["pct_over_best_basket"], 0.0)
		self.assertGreater(self.rows["S2"]["pct_over_best_basket"], 0)

	def test_premium_is_what_a_single_award_costs_over_buying_each_item_cheapest(self):
		split = 100 * 1.0 + 10 * 4.512 * 1.1
		self.assertAlmostEqual(self.rows["S1"]["premium_vs_split"], 150.0 - split)
		self.assertGreater(self.rows["S1"]["premium_vs_split"], 0)

	def test_coverage(self):
		self.assertEqual(self.rows["S1"]["items_requested"], 2)
		self.assertEqual(self.rows["S1"]["items_quoted"], 2)
		self.assertAlmostEqual(self.rows["S1"]["coverage_pct"], 100.0)

	def test_a_supplier_that_never_answered_is_listed_as_such(self):
		self.assertEqual(self.rows["S3"]["response"], "No response")
		self.assertEqual(self.rows["S3"]["items_quoted"], 0)
		self.assertIsNone(self.rows["S3"]["basket_total"])
		self.assertEqual(self.rows["S3"]["recommendation"], "")

	def test_recommendation_text(self):
		self.assertEqual(self.rows["S1"]["recommendation"], "Recommended for 1 of 2 items; Cheapest single award")
		self.assertEqual(self.rows["S2"]["recommendation"], "Recommended for 1 of 2 items")

	def test_origin_and_currency_of_the_supplier(self):
		self.assertEqual(self.rows["S1"]["origin"], "Local")
		self.assertEqual(self.rows["S2"]["origin"], "Import")
		self.assertEqual(self.rows["S2"]["currency"], "USD")

	def test_a_supplier_that_covers_every_item_and_wins_all_says_so(self):
		c = run(
			[quote("S1", "bolt", 100, 1.0), quote("S1", "nut", 10, 1.0), quote("S2", "bolt", 100, 2.0)],
			requested=asked(bolt=100, nut=10),
		)
		rows = {row["supplier"]: row for row in report.supplier_rows(c)}
		self.assertEqual(rows["S1"]["recommendation"], "Recommended: lowest on every item")
		self.assertEqual(rows["S2"]["recommendation"], "Not recommended")

	def test_a_partial_supplier_is_not_in_the_basket_ranking(self):
		c = run(
			[quote("S1", "bolt", 100, 0.1), quote("S2", "bolt", 100, 1.0), quote("S2", "nut", 10, 1.0)],
			requested=asked(bolt=100, nut=10),
		)
		rows = {row["supplier"]: row for row in report.supplier_rows(c)}
		self.assertIsNone(rows["S1"]["basket_rank"])
		self.assertEqual(rows["S2"]["basket_rank"], 1)

	def test_a_supplier_quoting_without_being_invited_is_shown_as_such(self):
		c = run([quote("S9", "bolt", 100, 1.0)], requested=asked(bolt=100), invites=invited("S1"))
		rows = {row["supplier"]: row for row in report.supplier_rows(c)}
		self.assertEqual(rows["S9"]["response"], "Quoted (not invited)")
		self.assertEqual(rows["S1"]["response"], "No response")

	def test_best_supplier_is_listed_first(self):
		order = [row["supplier"] for row in report.supplier_rows(self.comparison)]
		self.assertEqual(order[-1], "S3")


class TestItemRows(FrappeTestCase):
	def test_rows_are_ranked_within_each_item(self):
		c = run([quote("S1", "bolt", 100, 1.5), quote("S2", "bolt", 100, 1.0), quote("S3", "bolt", 100, 0, quotation="SQ-3")])
		rows = report.item_rows(c)
		self.assertEqual([r["supplier"] for r in rows], ["S2", "S1", "S3"])
		self.assertEqual([r["rank"] for r in rows], [1, 2, None])

	def test_an_item_nobody_quoted_still_has_a_line(self):
		c = run([quote("S1", "bolt", 100, 1.0)], requested=asked(bolt=100, nut=10))
		rows = report.item_rows(c)
		missing = next(r for r in rows if r["item_code"] == "nut")
		self.assertEqual(missing["remarks"], "No quotation received")
		self.assertEqual(missing["requested_qty"], 10)

	def test_expired_quotations_are_hidden_unless_asked_for(self):
		def fresh():
			return [quote("S1", "bolt", 100, 1.0), quote("S2", "bolt", 100, 0.5, status="Expired")]

		hidden = report.item_rows(run(fresh()))
		self.assertEqual([r["supplier"] for r in hidden], ["S1"])
		shown = report.item_rows(run(fresh(), include_expired=1))
		self.assertEqual({r["supplier"] for r in shown}, {"S1", "S2"})
		# hidden or not, the expired one never won
		self.assertEqual(next(r for r in shown if r["supplier"] == "S2")["recommended"], 0)

	def test_a_hidden_expired_quotation_still_counts_for_the_rule(self):
		quotes = [
			quote("S1", "bolt", 100, 1.0, currency="USD", conversion_rate=0.376, total=10),
			quote("S2", "bolt", 100, 0.5, currency="USD", conversion_rate=0.376, total=10, status="Expired"),
		]
		rows = report.item_rows(run(quotes, rule=RULE))
		self.assertEqual([r["supplier"] for r in rows], ["S1"])
		self.assertEqual(rows[0]["rule_label"], "Met (2 of 2)")

	def test_columns_exist_for_every_key_a_row_carries(self):
		c = run([quote("S1", "bolt", 100, 1.0)], requested=asked(bolt=100, nut=10))
		fieldnames = {col["fieldname"] for col in report.get_item_columns()}
		for row in report.item_rows(c):
			self.assertLessEqual(set(row) - {"company_currency"}, fieldnames | {"company_currency"})
		self.assertLessEqual({"company_currency", "currency"} - fieldnames, {"company_currency"})


class TestGaps(FrappeTestCase):
	def setUp(self):
		self.comparison = run(
			[quote("S1", "bolt", 100, 1.0), quote("S1", "nut", 10, 1.0), quote("S2", "bolt", 100, 1.1)],
			requested=asked(bolt=100, nut=10, washer=5),
			invites=invited("S1", "S2", "S3"),
			rule=RULE,
		)
		self.rows = report.gap_rows(self.comparison)

	def find(self, gap, **match):
		return [r for r in self.rows if r["gap"] == gap and all(r.get(k) == v for k, v in match.items())]

	def test_a_supplier_that_never_answered(self):
		self.assertEqual(len(self.find("No response", supplier="S3")), 1)
		self.assertIsNone(self.find("No response", supplier="S3")[0]["item_code"])

	def test_an_item_a_supplier_left_out(self):
		rows = self.find("Item not quoted", supplier="S2", item_code="nut")
		self.assertEqual(len(rows), 1)
		self.assertEqual(rows[0]["detail"], "Quoted 1 of 3 items")

	def test_an_item_nobody_quoted(self):
		rows = self.find("No quotation received", item_code="washer")
		self.assertEqual(len(rows), 1)
		self.assertIsNone(rows[0]["supplier"])

	def test_an_item_short_of_the_minimum(self):
		# total=100 is a local order at or under 500 here, so only the quotation count rule on imports bites: no row
		self.assertEqual(self.find("Below minimum quotations"), [])

	def test_below_minimum_is_reported_for_an_import_with_too_few_quotations(self):
		c = run(
			[quote("S1", "bolt", 100, 2.6, currency="USD", conversion_rate=0.376)],
			requested=asked(bolt=100),
			invites=invited("S1"),
			rule=RULE,
		)
		rows = report.gap_rows(c)
		short = [r for r in rows if r["gap"] == "Below minimum quotations"]
		self.assertEqual(len(short), 1)
		self.assertEqual(short[0]["detail"], "1 of 2 different suppliers have quoted")
		self.assertEqual(short[0]["origin"], "Import")

	def test_a_supplier_that_quoted_everything_has_no_gap(self):
		self.assertEqual(self.find("Item not quoted", supplier="S1", item_code="bolt"), [])
		self.assertEqual([r for r in self.rows if r["supplier"] == "S1" and r["gap"] == "Item not quoted"], [
			r for r in self.rows if r["supplier"] == "S1" and r["gap"] == "Item not quoted" and r["item_code"] == "washer"
		])

	def test_a_line_with_no_price_does_not_count_as_quoted(self):
		c = run(
			[quote("S1", "bolt", 100, 0)], requested=asked(bolt=100), invites=invited("S1")
		)
		rows = report.gap_rows(c)
		self.assertEqual([r["gap"] for r in rows if r["supplier"] == "S1"], ["No response"])


class TestOrdersFromTheRecommendation(FrappeTestCase):
	def comparison_for(self):
		return run(
			[
				quote("S1", "bolt", 100, 1.0, quotation="SQ-S1"),
				quote("S1", "nut", 10, 9.0, quotation="SQ-S1"),
				quote("S2", "bolt", 100, 2.0, quotation="SQ-S2"),
				quote("S2", "nut", 10, 3.0, quotation="SQ-S2"),
				quote("S3", "washer", 5, 1.0, quotation="SQ-S3", status="Draft", docstatus=0),
			],
			requested=asked(bolt=100, nut=10, washer=5),
			status=report.STATUS_BOTH,
		)

	def offered(self, already_ordered=()):
		comparison = self.comparison_for()
		filters = frappe._dict(company="SFB", from_date="2026-10-01", to_date="2026-10-31")
		with (
			patch.object(report, "compare", return_value=comparison),
			patch.object(report, "fetch_in", return_value=list(already_ordered)),
		):
			return {order.quotation: order for order in report.recommended_orders(filters)}

	def test_each_quotation_offers_only_the_lines_it_won(self):
		orders = self.offered()
		self.assertEqual(sorted(orders), ["SQ-S1", "SQ-S2", "SQ-S3"])
		self.assertEqual(orders["SQ-S1"]["items"], ["SQ-S1-bolt"])
		self.assertEqual(orders["SQ-S2"]["items"], ["SQ-S2-nut"])

	def test_the_total_is_the_landed_cost_of_the_requested_quantity(self):
		orders = self.offered()
		self.assertAlmostEqual(orders["SQ-S1"]["total"], 100.0)
		self.assertAlmostEqual(orders["SQ-S2"]["total"], 30.0)

	def test_a_draft_quotation_is_offered_but_cannot_be_ordered(self):
		orders = self.offered()
		self.assertFalse(orders["SQ-S3"]["submitted"])
		self.assertTrue(orders["SQ-S1"]["submitted"])

	def test_a_line_already_on_a_purchase_order_is_left_out(self):
		orders = self.offered(already_ordered=["SQ-S1-bolt"])
		self.assertNotIn("SQ-S1", orders)
		self.assertIn("SQ-S2", orders)

	def test_a_quotation_outside_the_recommendation_is_refused(self):
		offer = {"SQ-S1": frappe._dict(quotation="SQ-S1", submitted=True, items=["SQ-S1-bolt"])}
		with (
			patch.object(report.frappe, "has_permission", return_value=True),
			patch.object(report, "recommended_orders", return_value=list(offer.values())),
			patch.object(report, "parse_filters", return_value=frappe._dict()),
		):
			result = report.make_purchase_orders("{}", '["SQ-NOPE"]')
		self.assertEqual(result, [{"quotation": "SQ-NOPE", "purchase_order": None, "error": "Not in the recommendation"}])

	def test_a_draft_quotation_is_refused_before_anything_is_built(self):
		offer = [frappe._dict(quotation="SQ-S3", submitted=False, items=["SQ-S3-washer"])]
		with (
			patch.object(report.frappe, "has_permission", return_value=True),
			patch.object(report, "recommended_orders", return_value=offer),
			patch.object(report, "parse_filters", return_value=frappe._dict()),
		):
			result = report.make_purchase_orders("{}", '["SQ-S3"]')
		self.assertEqual(result[0]["error"], "Submit the quotation first")
		self.assertIsNone(result[0]["purchase_order"])

	def test_a_required_by_date_already_past_is_moved_to_the_order_date(self):
		po = frappe._dict(
			transaction_date="2026-10-31",
			schedule_date="2026-07-18",
			items=[
				frappe._dict(schedule_date="2026-07-18"),
				frappe._dict(schedule_date="2026-11-15"),
				frappe._dict(schedule_date=None),
			],
		)
		self.assertEqual(report.bring_required_by_forward(po), 1)
		rows = po["items"]  # a dict has its own .items
		self.assertEqual(str(report.getdate(rows[0].schedule_date)), "2026-10-31")
		self.assertEqual(rows[1].schedule_date, "2026-11-15")
		self.assertIsNone(rows[2].schedule_date)
		self.assertEqual(str(report.getdate(po.schedule_date)), "2026-10-31")

	def test_a_draft_is_made_from_the_recommended_lines_and_says_when_a_date_moved(self):
		po = frappe._dict(
			name="PUR-ORD-1",
			transaction_date="2026-10-31",
			schedule_date="2026-07-18",
			items=[frappe._dict(schedule_date="2026-07-18")],
		)
		inserted = []
		po.insert = lambda: inserted.append(po.name)
		offer = [frappe._dict(quotation="SQ-S1", submitted=True, items=["SQ-S1-bolt"])]
		with (
			patch("erpnext.buying.doctype.supplier_quotation.supplier_quotation.make_purchase_order", return_value=po) as mapper,
			patch.object(report.frappe, "has_permission", return_value=True),
			patch.object(report, "recommended_orders", return_value=offer),
			patch.object(report, "parse_filters", return_value=frappe._dict()),
		):
			result = report.make_purchase_orders("{}", '["SQ-S1"]')
		mapper.assert_called_once_with("SQ-S1", args={"filtered_children": ["SQ-S1-bolt"]})
		self.assertEqual(inserted, ["PUR-ORD-1"])
		self.assertEqual(result[0]["purchase_order"], "PUR-ORD-1")
		self.assertIsNone(result[0]["error"])
		self.assertIn("Required By moved to 2026-10-31 on 1 row(s)", result[0]["note"])

	def test_choosing_nothing_is_refused(self):
		with patch.object(report.frappe, "has_permission", return_value=True):
			with self.assertRaises(frappe.ValidationError):
				report.make_purchase_orders("{}", "[]")


class TestFilters(FrappeTestCase):
	def test_a_multi_select_list_arrives_as_a_list_json_or_comma_text(self):
		self.assertEqual(report.as_list(["A", "B"]), ["A", "B"])
		self.assertEqual(report.as_list('["A", "B"]'), ["A", "B"])
		self.assertEqual(report.as_list("A, B"), ["A", "B"])
		self.assertEqual(report.as_list(None), [])
		self.assertEqual(report.as_list(""), [])

	def test_status_choices_map_to_docstatus(self):
		self.assertEqual(report.docstatus_clause(frappe._dict(status="Submitted")), 1)
		self.assertEqual(report.docstatus_clause(frappe._dict(status="Draft")), 0)
		self.assertEqual(report.docstatus_clause(frappe._dict(status="Draft and Submitted")), ["<", 2])
		self.assertEqual(report.docstatus_clause(frappe._dict()), 1)

	def test_a_reversed_period_a_missing_company_and_negative_charges_are_refused(self):
		base = dict(company="SFB", from_date="2026-10-02", to_date="2026-10-01")
		with self.assertRaises(frappe.ValidationError):
			report.validate_filters(frappe._dict(base))
		with self.assertRaises(frappe.ValidationError):
			report.validate_filters(frappe._dict(from_date="2026-10-01", to_date="2026-10-02"))
		with self.assertRaises(frappe.ValidationError):
			report.validate_filters(frappe._dict(company="SFB", from_date="2026-10-01", to_date="2026-10-02", import_charges_pct=-1))

	def test_an_unknown_view_or_origin_falls_back_to_the_default(self):
		self.assertEqual(report.resolve_view(frappe._dict(view="nonsense")), report.VIEW_ITEM)
		self.assertEqual(report.resolve_origin(frappe._dict(purchase_origin="nonsense")), report.ORIGIN_BOTH)

	def test_the_three_views_build_their_own_columns(self):
		c = run([quote("S1", "bolt", 100, 1.0)], requested=asked(bolt=100), invites=invited("S1"))
		for view, columns in (
			(report.VIEW_ITEM, report.get_item_columns),
			(report.VIEW_SUPPLIER, report.get_supplier_columns),
			(report.VIEW_GAPS, report.get_gap_columns),
		):
			with (
				patch.object(report, "compare", return_value=c),
				patch.object(report, "validate_filters"),
			):
				cols, rows, message = report.execute({"company": "SFB", "view": view})
			self.assertEqual([col["fieldname"] for col in cols], [col["fieldname"] for col in columns()])
			self.assertIn("estimated charges", message)
