# sf_trading/tests/test_mis_profit_loss.py
"""MIS Profit and Loss (sf_trading/mis_profit_loss.py).

The database is patched at the module's own seams, so nothing is created or read:

    bench --site <scratch-site> run-tests --module sf_trading.tests.test_mis_profit_loss

The property under test: whatever was posted, however it was charged between companies, the management profit
equals the profit per books, less what was charged out, plus what was charged in -- in every column, however
the columns are cut -- and no ledger row is counted twice or dropped.
"""

import json
import os
import random
import shutil
import subprocess
from datetime import date
from unittest.mock import MagicMock, patch

import frappe
from frappe.tests.utils import FrappeTestCase

from sf_trading import cash_flow_statement as cfs
from sf_trading import mis_profit_loss as mis
from sf_trading.tests.test_cash_flow_statement import CHART

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BHD_CO, OMR_CO = "Steel Force Trading WLL", "STEEL FORCE INTERNATIONAL SPC"
ALTS = {
	"Main - SFTO": (OMR_CO, "Main - SFTO"),
	"WH - SFTO": (OMR_CO, "WH - SFTO"),
	"Main - SFB": (BHD_CO, "Main - SFB"),
	"SFSB - SFB": (BHD_CO, "SFSB - SFB"),
}
RATE = 2.6  # BHD into OMR, for the test (a made-up rate: only the arithmetic matters)


def read(*parts):
	with open(os.path.join(ROOT, *parts), encoding="utf-8") as f:
		return f.read()


def pl_account(name, root, account_type):
	line = cfs.classify(frappe._dict(name=name, root_type=root, account_type=account_type))
	parts = name.split(" - ")
	return {"section": mis.section_of(line, root), "label": name, "root": root, "base": parts[1] if len(parts) > 2 else parts[0]}


def pl_chart():
	return {a.name: pl_account(a.name, a.root_type, a.account_type) for a, _k in CHART if a.root_type in ("Income", "Expense")}


ACCOUNTS = pl_chart()
SOURCES = {BHD_CO: ACCOUNTS, OMR_CO: ACCOUNTS}


class FixedRates(mis.Rates):
	"""BHD into OMR at a fixed rate; anything else is unknown."""

	@staticmethod
	def lookup(from_currency, to_currency, on):
		return RATE if (from_currency, to_currency) == ("BHD", "OMR") else 0.0


def month_periods(first="2026-01-01", last="2026-04-30", kind="Monthly"):
	return mis.Columns("Period", cfs.period_list(first, last, kind))


def random_rows(seed, company=OMR_CO, n=400):
	"""Own rows of `company` (some charged out to the other company) and rows of the other company charged in."""
	rng = random.Random(seed)
	names = list(ACCOUNTS)
	other = BHD_CO if company == OMR_CO else OMR_CO
	own_alts = [a for a, (c, _cc) in ALTS.items() if c == other]
	mine = [a for a, (c, _cc) in ALTS.items() if c == company]
	own, inn = [], []
	for _i in range(n):
		on = date(2026, rng.randint(1, 4), rng.randint(1, 28))
		cc = rng.choice(("SFSB - SFB", "SFSS - SFB", "Main - SFTO", ""))
		net = round(rng.uniform(1, 900), 3) * rng.choice((1, -1))
		alt = rng.choice(["", "", ""] + own_alts)
		own.append((rng.choice(names), on, cc, alt, net))
	for _i in range(n // 3):
		on = date(2026, rng.randint(1, 4), rng.randint(1, 28))
		inn.append((other, rng.choice(names), on, rng.choice(mine), round(rng.uniform(1, 900), 3) * rng.choice((1, -1))))
	return own, inn


def run_model(own, inn, columns, company=OMR_CO, rates=None):
	currency = "OMR" if company == OMR_CO else "BHD"
	rates = rates or FixedRates(currency)
	with patch.object(mis.frappe, "get_cached_value", side_effect=lambda dt, name, field: ("BHD" if name == BHD_CO else "OMR") if field == "default_currency" else name[:4], create=True):
		return mis.build_model(own, inn, ACCOUNTS, SOURCES, ALTS, company, columns, rates), rates


def render(model, detail="Sections and Accounts", show_zero=True, show_percent=0):
	out = mis.Out(model.columns, "OMR", show_percent)
	totals = mis.statement_rows(model, out, detail, show_zero)
	bridge = mis.bridge_rows(model, out, OMR_CO, totals["net"] or [0.0] * model.n)
	return out, totals, bridge


class TestTheLayout(FrappeTestCase):
	def test_the_steps_run_in_the_order_a_manager_reads_them(self):
		self.assertEqual(mis.ORDER, ["REVENUE", "COST_OF_SALES", "GROSS", "OTHER_INCOME", "OPEX", "EBITDA", "DA", "EBIT", "INTEREST", "DISPOSAL", "PBT", "TAX", "NET"])

	def test_every_profit_and_loss_line_of_the_cash_flow_statement_has_exactly_one_section(self):
		for key in cfs.PL_KEYS:
			self.assertIn(key, mis.SECTION_OF_LINE, key)
		sections = [s for s in mis.LAYOUT if not s.get("subtotal")]
		seen = [line for s in sections for line in s["lines"]]
		self.assertEqual(len(seen), len(set(seen)), "a line in two sections would be counted twice")

	def test_an_account_the_rules_put_on_a_balance_sheet_line_still_lands_in_a_section(self):
		self.assertEqual(mis.section_of("AR", "Income"), "OTHER_INCOME")
		self.assertEqual(mis.section_of("LOANS", "Expense"), "OPEX")
		self.assertEqual(mis.section_of(None, "Expense"), "OPEX")

	def test_a_sections_sign_makes_costs_positive_and_a_subtotal_adds_what_is_above_it(self):
		self.assertEqual(mis.section_signs("Operating expenses"), {"OPEX": -1})
		self.assertEqual(mis.section_signs("revenue"), {"REVENUE": 1})
		gross = mis.section_signs("Gross profit")
		self.assertEqual(gross, {"REVENUE": 1, "COST_OF_SALES": 1})
		net = mis.section_signs("Net profit")
		self.assertEqual(set(net), {s["key"] for s in mis.LAYOUT if not s.get("subtotal")})
		self.assertEqual(mis.section_signs("Nonsense"), {})

	def test_the_accounts_of_the_chart_land_where_a_manager_expects(self):
		by = {a: v["section"] for a, v in ACCOUNTS.items()}
		self.assertEqual(by["41010100001 - Sales Revenue - SFB"], "REVENUE")
		self.assertEqual(by["51010100001 - Cost Of Goods Sold - SFB"], "COST_OF_SALES")
		self.assertEqual(by["51010300013 - Customs Duty - SFB"], "COST_OF_SALES")
		self.assertEqual(by["52010700001 - Rent Expense - SFB"], "OPEX")
		self.assertEqual(by["Depreciation - SFB"], "DA")
		self.assertEqual(by["52010305 - Interest On Bank Loan - SFB"], "INTEREST")
		self.assertEqual(by["Income Tax Expense - SFB"], "TAX")
		self.assertEqual(by["Loss On Disposal Of Asset - SFB"], "DISPOSAL")
		self.assertEqual(by["52010302 - Other Income - SFB"], "OTHER_INCOME")


class TestTheArithmetic(FrappeTestCase):
	def test_a_worked_example_from_revenue_to_net_profit(self):
		own = [
			("41010100001 - Sales Revenue - SFB", date(2026, 1, 5), "SFSB - SFB", "", 1000.0),
			("51010100001 - Cost Of Goods Sold - SFB", date(2026, 1, 6), "SFSB - SFB", "", -600.0),
			("52010700001 - Rent Expense - SFB", date(2026, 1, 7), "SFSB - SFB", "", -150.0),
			("Depreciation - SFB", date(2026, 1, 31), "SFSB - SFB", "", -50.0),
			("52010305 - Interest On Bank Loan - SFB", date(2026, 1, 31), "SFSB - SFB", "", -20.0),
			("Income Tax Expense - SFB", date(2026, 1, 31), "SFSB - SFB", "", -30.0),
		]
		model, _r = run_model(own, [], month_periods("2026-01-01", "2026-01-31", "Total"))
		out, totals, bridge = render(model)
		by = {r["label"]: r.get("p0") for r in out.data}
		self.assertEqual(by["Revenue"], 1000.0)
		self.assertEqual(by["Cost of sales"], 600.0)
		self.assertEqual(by["Gross profit"], 400.0)
		self.assertEqual(by["Gross profit margin %"], 40.0)
		self.assertEqual(by["Operating expenses"], 150.0)
		self.assertEqual(by["EBITDA"], 250.0)
		self.assertEqual(by["Operating profit (EBIT)"], 200.0)
		self.assertEqual(by["Profit before tax"], 180.0)
		self.assertEqual(by["Net profit"], 150.0)
		self.assertEqual(by["Net profit margin %"], 15.0)
		self.assertEqual(bridge["difference"], [0.0])

	def test_what_is_charged_out_leaves_the_figures_and_is_listed_under_them(self):
		own = [("41010100001 - Sales Revenue - SFB", date(2026, 1, 5), "SFSB - SFB", "", 500.0),
			("52010700001 - Rent Expense - SFB", date(2026, 1, 7), "SFSB - SFB", "Main - SFTO", -120.0)]
		model, _r = run_model(own, [], month_periods("2026-01-01", "2026-01-31", "Total"), company=BHD_CO)
		out = mis.Out(model.columns, "BHD", 0)
		totals = mis.statement_rows(model, out, "Sections and Accounts", True)
		bridge = mis.bridge_rows(model, out, BHD_CO, totals["net"])
		self.assertEqual(sum(totals["net"]), 500.0, "the rent is not this company's cost")
		self.assertEqual(bridge["books"], [380.0])
		self.assertEqual(bridge["out"], [-120.0])
		self.assertEqual(bridge["mis"], [500.0])
		out_rows = [r for r in out.data if r.get("origin") == "out" and r.get("is_line")]
		self.assertEqual([(r["source_company"], round(r["p0"], 3)) for r in out_rows], [(OMR_CO, 120.0)])

	def test_what_another_company_charged_in_is_added_in_this_companys_currency_and_marked(self):
		inn = [(BHD_CO, "52010700001 - Rent Expense - SFB", date(2026, 1, 7), "Main - SFTO", -100.0)]
		model, rates = run_model([], inn, month_periods("2026-01-01", "2026-01-31", "Total"))
		out, totals, bridge = render(model)
		line = next(r for r in out.data if r.get("origin") == "in" and r.get("is_line"))
		self.assertTrue(line["label"].startswith("↳ "))
		self.assertTrue(line["label"].endswith(": Rent Expense"), line["label"])
		self.assertAlmostEqual(line["p0"], 100.0 * RATE, 6)
		self.assertEqual(line["source_company"], BHD_CO)
		self.assertAlmostEqual(sum(totals["net"]), -100.0 * RATE, 6)
		self.assertAlmostEqual(bridge["in"][0], -100.0 * RATE, 6)
		self.assertEqual(bridge["difference"], [0.0])

	def test_a_row_charged_to_a_cost_centre_of_a_third_company_is_none_of_ours(self):
		inn = [(BHD_CO, "52010700001 - Rent Expense - SFB", date(2026, 1, 7), "Main - SFB", -100.0)]
		model, _r = run_model([], inn, month_periods("2026-01-01", "2026-01-31", "Total"))
		self.assertEqual(model.sections, {})

	def test_no_exchange_rate_means_the_amount_is_left_out_and_the_currency_is_listed(self):
		class NoRates(mis.Rates):
			@staticmethod
			def lookup(*_a):
				return 0.0

		inn = [(BHD_CO, "52010700001 - Rent Expense - SFB", date(2026, 1, 7), "Main - SFTO", -100.0)]
		model, rates = run_model([], inn, month_periods("2026-01-01", "2026-01-31", "Total"), rates=NoRates("OMR"))
		out, totals, _bridge = render(model)
		self.assertEqual(sum(totals["net"]), 0.0)
		self.assertEqual(rates.missing, {"BHD"})
		self.assertEqual(model.unconverted, {BHD_CO})

	def test_the_same_currency_needs_no_rate(self):
		rates = mis.Rates("BHD")
		self.assertEqual(rates.rate("BHD", date(2026, 1, 1)), 1.0)
		self.assertEqual(rates.rate(None, date(2026, 1, 1)), 1.0)
		self.assertEqual(rates.missing, set())

	def test_the_exchange_lookup_uses_the_latest_rate_on_or_before_and_the_inverse(self):
		def get_all(doctype, filters=None, **k):
			if filters["from_currency"] == "BHD" and filters["to_currency"] == "OMR":
				return []
			return [frappe._dict(exchange_rate=0.5)]
		with patch.object(mis.frappe, "get_all", side_effect=get_all, create=True):
			self.assertEqual(mis.Rates.lookup("BHD", "OMR", date(2026, 1, 1)), 2.0)


class TestTheBridgeHolds(FrappeTestCase):
	"""The property: profit per books - charged out + charged in == the management profit, always."""

	def test_in_every_column_for_every_way_of_cutting_the_columns(self):
		for seed in range(25):
			own, inn = random_rows(seed)
			for columns in (month_periods(), month_periods(kind="Total"), month_periods(kind="Quarterly")):
				model, _r = run_model(own, inn, columns)
				out, totals, bridge = render(model)
				for i in range(model.n):
					self.assertAlmostEqual(bridge["difference"][i], 0.0, 6, "seed %s column %s" % (seed, i))
					self.assertAlmostEqual(bridge["mis"][i], totals["net"][i], 6)

	def test_by_cost_centre_the_columns_add_up_to_the_whole(self):
		for seed in range(10):
			own, inn = random_rows(seed)
			whole, _r = run_model(own, inn, month_periods(kind="Total"))
			cc_cols = mis.cost_centre_columns(own, inn, ALTS, OMR_CO)
			split, _r = run_model(own, inn, cc_cols)
			a, b = render(whole), render(split)
			self.assertAlmostEqual(sum(a[1]["net"]), sum(b[1]["net"]), 6, seed)
			self.assertAlmostEqual(sum(b[2]["difference"]), 0.0, 6)

	def test_every_ledger_row_of_ours_is_counted_once_and_none_twice(self):
		own, inn = random_rows(3)
		model, _r = run_model(own, inn, month_periods(kind="Total"))
		books = sum(net for _a, _d, _c, _alt, net in own)
		self.assertAlmostEqual(model.books[0], books, 6)
		counted_in = sum(line["vals"][0] for sec in model.sections.values() for line in sec.values() if line["origin"] == "in")
		expected_in = sum(net * RATE for src, _a, _d, alt, net in inn if ALTS[alt][0] == OMR_CO)
		self.assertAlmostEqual(counted_in, expected_in, 6)

	def test_a_company_with_nothing_charged_either_way_has_no_bridge_adjustment(self):
		own = [(a, date(2026, 2, 1), "SFSB - SFB", "", 10.0) for a in list(ACCOUNTS)[:5]]
		model, _r = run_model(own, [], month_periods(kind="Total"))
		_out, totals, bridge = render(model)
		self.assertEqual((bridge["out"], bridge["in"]), ([0.0], [0.0]))
		self.assertAlmostEqual(bridge["books"][0], totals["net"][0], 6)


class TestTheColumnsAndRows(FrappeTestCase):
	def test_a_period_goes_to_the_one_column_that_holds_its_date(self):
		cols = month_periods()
		self.assertEqual([cols.index(date(2026, m, 15), "x") for m in (1, 2, 3, 4)], [0, 1, 2, 3])
		self.assertIsNone(cols.index(date(2026, 5, 1), "x"))

	def test_a_cost_centre_column_holds_its_own_postings_and_an_empty_one_has_its_own_column(self):
		cols = mis.Columns("Cost Center", [{"label": "A", "key": "A"}, {"label": mis.NO_CC, "key": mis.NO_CC}])
		self.assertEqual((cols.index(date(2026, 1, 1), "A"), cols.index(date(2026, 1, 1), ""), cols.index(date(2026, 1, 1), "Z")), (0, 1, None))

	def test_a_charged_in_row_falls_in_the_cost_centre_it_was_charged_to(self):
		inn = [(BHD_CO, "52010700001 - Rent Expense - SFB", date(2026, 1, 7), "WH - SFTO", -10.0)]
		cols = mis.cost_centre_columns([], inn, ALTS, OMR_CO)
		self.assertEqual([i["key"] for i in cols.items], ["WH - SFTO"])

	def test_comparison_sets_the_earlier_range_beside_the_current_and_adds_the_change(self):
		filters = frappe._dict(from_date=date(2026, 4, 1), to_date=date(2026, 6, 30), comparison="Previous Period", periodicity="Total")
		cols = mis.period_columns(filters)
		self.assertTrue(cols.comparison)
		self.assertEqual((cols.items[1]["from"], cols.items[1]["to"]), (date(2026, 1, 1), date(2026, 3, 31)))
		# not a whole number of months: the same number of days before
		odd = frappe._dict(from_date=date(2026, 4, 10), to_date=date(2026, 4, 19), comparison="Previous Period", periodicity="Total")
		self.assertEqual((mis.period_columns(odd).items[1]["from"], mis.period_columns(odd).items[1]["to"]), (date(2026, 3, 31), date(2026, 4, 9)))
		filters.comparison = "Same Period Last Year"
		cols = mis.period_columns(filters)
		self.assertEqual((cols.items[1]["from"], cols.items[1]["to"]), (date(2025, 4, 1), date(2025, 6, 30)))
		own = [("41010100001 - Sales Revenue - SFB", date(2026, 5, 1), "", "", 300.0), ("41010100001 - Sales Revenue - SFB", date(2025, 5, 1), "", "", 200.0)]
		model, _r = run_model(own, [], cols)
		out = mis.Out(cols, "OMR", 0)
		mis.statement_rows(model, out, "Sections Only", True)
		revenue = next(r for r in out.data if r["label"] == "Revenue")
		self.assertEqual((revenue["p0"], revenue["p1"], revenue["change"], revenue["change_pct"]), (300.0, 200.0, 100.0, 50.0))

	def test_a_total_column_is_added_for_several_periods_and_for_cost_centres(self):
		own, inn = random_rows(1)
		model, _r = run_model(own, inn, month_periods())
		out, _t, _b = render(model)
		row = next(r for r in out.data if r["label"] == "Revenue")
		self.assertAlmostEqual(sum(row["p%d" % i] for i in range(4)), row["total"], 5)
		single, _r = run_model(own, inn, month_periods(kind="Total"))
		self.assertNotIn("total", next(r for r in render(single)[0].data if r["label"] == "Revenue"))

	def test_a_margin_row_follows_each_profit_step_and_is_marked_so_it_is_not_a_figure(self):
		own = [("41010100001 - Sales Revenue - SFB", date(2026, 1, 5), "", "", 200.0), ("51010100001 - Cost Of Goods Sold - SFB", date(2026, 1, 5), "", "", -50.0)]
		model, _r = run_model(own, [], month_periods(kind="Total"))
		out, _t, _b = render(model)
		ratios = [r for r in out.data if r.get("is_ratio")]
		self.assertEqual([r["label"] for r in ratios], ["Gross profit margin %", "EBITDA margin %", "Operating profit (EBIT) margin %", "Profit before tax margin %", "Net profit margin %"])
		self.assertEqual(ratios[0]["p0"], 75.0)

	def test_a_company_with_no_revenue_has_blank_margins_not_zero_ones(self):
		own = [("52010700001 - Rent Expense - SFB", date(2026, 1, 5), "", "", -50.0)]
		model, _r = run_model(own, [], month_periods(kind="Total"))
		out, _t, _b = render(model)
		ratios = [r for r in out.data if r.get("is_ratio")]
		self.assertEqual(len(ratios), 5)
		self.assertTrue(all("p0" not in r for r in ratios), "0% would say the margin is nil, when there is nothing to take it of")

	def test_sections_only_drops_the_account_rows_and_zero_rows_are_hidden_unless_asked(self):
		own = [("41010100001 - Sales Revenue - SFB", date(2026, 1, 5), "", "", 200.0)]
		model, _r = run_model(own, [], month_periods(kind="Total"))
		self.assertFalse([r for r in render(model, "Sections Only")[0].data if r.get("is_line") and r.get("origin") == "own"])
		hidden = render(model, show_zero=False)[0].data
		self.assertNotIn("Operating expenses", [r["label"] for r in hidden])
		shown = render(model, show_zero=True)[0].data
		self.assertIn("Operating expenses", [r["label"] for r in shown])

	def test_a_charged_in_line_comes_after_the_own_lines_of_its_section(self):
		own = [("52010700001 - Rent Expense - SFB", date(2026, 1, 5), "", "", -5.0)]
		inn = [(BHD_CO, "52010700001 - Rent Expense - SFB", date(2026, 1, 5), "Main - SFTO", -900.0)]
		model, _r = run_model(own, inn, month_periods(kind="Total"))
		lines = [r for r in render(model)[0].data if r.get("is_line") and r.get("section") == "OPEX"]
		self.assertEqual([r.get("origin") for r in lines], ["own", "in"], "own first even though the charged-in line is bigger")

	def test_percent_of_revenue_is_filled_beside_each_amount(self):
		own = [("41010100001 - Sales Revenue - SFB", date(2026, 1, 5), "", "", 200.0), ("52010700001 - Rent Expense - SFB", date(2026, 1, 5), "", "", -50.0)]
		model, _r = run_model(own, [], month_periods(kind="Total"))
		out, _t, _b = render(model)
		revenue = next(r for r in out.data if r["label"] == "Revenue")
		mis.percent_columns(out.data, model.columns, revenue)
		rent = next(r for r in out.data if r["label"].startswith("52010700001"))
		self.assertEqual((rent["pct0"], revenue["pct0"]), (25.0, 100.0))
		self.assertNotIn("pct0", next(r for r in out.data if r.get("is_ratio")))


class TestTheReport(FrappeTestCase):
	BASE = dict(company=OMR_CO, from_date="2026-01-01", to_date="2026-04-30")

	def run_report(self, extra=None, seed=2, dimension=True):
		own, inn = random_rows(seed)
		patches = [
			patch.object(mis, "alternatives", return_value=ALTS),
			patch.object(mis, "_own_rows", return_value=own),
			patch.object(mis, "fetch_in", return_value=inn),
			patch.object(mis, "pl_accounts", return_value=ACCOUNTS),
			patch.object(mis, "has_dimension", return_value=dimension),
			patch.object(mis, "Rates", FixedRates),
			patch.object(mis.money, "scope_params", return_value=("", {})), patch.object(mis.money, "permitted", return_value=None),
			patch.object(mis.frappe, "has_permission", MagicMock(return_value=True), create=True),
			patch.object(mis.frappe, "get_cached_value", MagicMock(side_effect=lambda dt, name, field: "OMR" if name == OMR_CO or field == "default_currency" and name != BHD_CO else ("BHD" if field == "default_currency" else "SFB")), create=True),
		]
		for p in patches:
			p.start()
		try:
			return mis.execute(frappe._dict(dict(self.BASE, **(extra or {}))))
		finally:
			for p in reversed(patches):
				p.stop()

	def test_it_returns_columns_rows_message_chart_and_cards(self):
		columns, data, message, chart, summary = self.run_report({"periodicity": "Monthly"})
		self.assertEqual(columns[0]["fieldname"], "label")
		self.assertEqual([c["fieldname"] for c in columns][1:6], ["p0", "p1", "p2", "p3", "total"])
		self.assertIsNone(message)
		self.assertEqual(chart["type"], "bar")
		self.assertEqual([c["label"] for c in summary], ["Revenue", "Gross Profit", "Gross Margin", "EBITDA", "Net Profit", "Net Margin", "Charged In", "Charged Out, Added Back"])

	def test_the_first_row_tells_the_drill_down_what_each_column_is(self):
		_c, data, *_rest = self.run_report({"periodicity": "Quarterly"})
		meta = data[0]["columns_meta"]
		self.assertEqual(meta["p0"], {"from_date": "2026-01-01", "to_date": "2026-03-31"})
		self.assertEqual(meta["total"], {"from_date": "2026-01-01", "to_date": "2026-04-30"})
		_c, data, *_rest = self.run_report({"columns_by": "Cost Center"})
		meta = data[0]["columns_meta"]
		columns = [v for k, v in meta.items() if k != "total"]
		self.assertTrue(columns and all("cost_center" in v for v in columns))
		self.assertTrue(all(v["from_date"] == "2026-01-01" and v["to_date"] == "2026-04-30" for v in meta.values()), "a drill must never fall back on the filter's dates")
		self.assertNotIn("cost_center", meta["total"])

	def test_the_bridge_is_checked_and_a_failure_would_be_said(self):
		_c, data, message, *_rest = self.run_report()
		self.assertIsNone(message)
		diff = next(r for r in data if r.get("is_difference"))
		self.assertAlmostEqual(diff["p0"], 0.0, 6)

	def test_without_the_dimension_it_says_so_and_shows_the_books(self):
		_c, _d, message, *_rest = self.run_report(dimension=False)
		self.assertIn("not set up on this site", message)

	def test_a_comparison_needs_a_total_and_period_columns(self):
		for bad in ({"comparison": "Previous Period", "periodicity": "Monthly"}, {"comparison": "Previous Period", "columns_by": "Cost Center"},
				{"periodicity": "Weekly"}, {"detail": "Everything"}, {"company": ""}, {"from_date": "2026-09-01"}):
			with self.assertRaises(frappe.ValidationError, msg=str(bad)):
				self.run_report(bad)

	def test_percent_columns_come_after_each_amount_column(self):
		columns, data, *_rest = self.run_report({"show_percent": 1})
		names = [c["fieldname"] for c in columns]
		self.assertEqual(names[1:3], ["p0", "pct0"])
		self.assertTrue(any("pct0" in r for r in data))

	def test_a_missing_exchange_rate_is_said_in_the_message(self):
		class NoRates(mis.Rates):
			@staticmethod
			def lookup(*_a):
				return 0.0

		with patch.object(mis, "Rates", NoRates):
			own, inn = random_rows(2)
			patches = [patch.object(mis, "alternatives", return_value=ALTS), patch.object(mis, "_own_rows", return_value=own), patch.object(mis, "fetch_in", return_value=inn),
				patch.object(mis, "pl_accounts", return_value=ACCOUNTS), patch.object(mis, "has_dimension", return_value=True),
				patch.object(mis.money, "scope_params", return_value=("", {})), patch.object(mis.money, "permitted", return_value=None), patch.object(mis.frappe, "has_permission", MagicMock(return_value=True), create=True),
				patch.object(mis.frappe, "get_cached_value", MagicMock(side_effect=lambda dt, name, field: "BHD" if name == BHD_CO else "OMR"), create=True)]
			for p in patches:
				p.start()
			try:
				_c, _d, message, *_rest = mis.execute(frappe._dict(self.BASE))
			finally:
				for p in reversed(patches):
					p.stop()
		self.assertIn("No exchange rate", message)


class TestTheQueries(FrappeTestCase):
	def filters(self):
		return frappe._dict(company=OMR_CO, range_from=date(2026, 1, 1), range_to=date(2026, 4, 30))

	def test_own_rows_are_the_companys_profit_and_loss_without_closing_entries_and_bind_every_value(self):
		with patch.object(mis.frappe.db, "sql", MagicMock(return_value=[]), create=True) as sql:
			mis.fetch_own(self.filters(), " and gle.branch in %(branches)s", {"branches": ("SFSB",)})
		query, params = sql.call_args.args
		for fragment in ("acc.root_type in ('Income', 'Expense')", "voucher_type != 'Period Closing Voucher'", "is_cancelled = 0", "gle.company = %(company)s",
				"alternative_cost_center", "group by gle.account, gle.posting_date, gle.cost_center, alt", "gle.branch in %(branches)s"):
			self.assertIn(fragment, query)
		self.assertEqual(params["company"], OMR_CO)
		self.assertNotIn("STEEL FORCE", query)

	def test_charged_in_rows_are_other_companies_rows_charged_to_one_of_ours(self):
		with patch.object(mis.frappe.db, "sql", MagicMock(return_value=[]), create=True) as sql:
			mis.fetch_in(self.filters(), ["Main - SFTO", "WH - SFTO"], None)
		query, params = sql.call_args.args
		for fragment in ("gle.company != %(company)s", "gle.alternative_cost_center in %(alts)s", "group by gle.company, gle.account, gle.posting_date, alt"):
			self.assertIn(fragment, query)
		self.assertEqual(params["alts"], ("Main - SFTO", "WH - SFTO"))

	def test_a_cost_centre_filter_narrows_the_charged_in_rows_to_that_cost_centre(self):
		with patch.object(mis.frappe.db, "sql", MagicMock(return_value=[]), create=True) as sql:
			mis.fetch_in(self.filters(), ["Main - SFTO", "WH - SFTO"], {"WH - SFTO"})
		self.assertEqual(sql.call_args.args[1]["alts"], ("WH - SFTO",))

	def test_no_alternative_cost_centre_of_ours_means_no_query(self):
		with patch.object(mis.frappe.db, "sql", MagicMock(side_effect=AssertionError("nothing can be charged in")), create=True):
			self.assertEqual(mis.fetch_in(self.filters(), [], None), [])
			self.assertEqual(mis.fetch_in(self.filters(), ["A"], {"B"}), [])

	def test_without_the_dimension_column_the_own_query_reads_a_blank_in_its_place(self):
		with patch.object(mis, "has_dimension", return_value=False), patch.object(mis.frappe.db, "sql", MagicMock(return_value=[]), create=True) as sql:
			mis._own_rows(self.filters(), "", {})
		self.assertNotIn("gle.alternative_cost_center", sql.call_args.args[0])
		self.assertIn("'' as alt", sql.call_args.args[0])

	def test_the_query_text_holds_placeholders_only(self):
		for text in (mis.OWN_ROWS, mis.OWN_GROUP, mis.IN_ROWS, mis.IN_GROUP, mis.BASE):
			self.assertNotIn("{", text)


JS_RUNNER = r"""
const fs = require("fs"), vm = require("vm");
const sandbox = { console, $: () => ({}), __: (s) => s, cint: (v) => parseInt(v) || 0, flt: (v) => parseFloat(v) || 0 };
sandbox.frappe = {
	provide(path) { path.split(".").reduce((o, k) => (o[k] = o[k] || {}, o[k]), sandbox); },
	route_options: null, set_route(...r) { sandbox.routed = r; }, show_alert(a) { sandbox.alert = a; },
	utils: { get_form_link: (dt, name, html, text) => '<a href="/app/' + dt + '/' + name + '">' + text + '</a>' },
};
sandbox.sf_trading = {};
vm.createContext(sandbox);
vm.runInContext(fs.readFileSync(process.argv[2], "utf8"), sandbox);
const pl = sandbox.sf_trading.mis_pl;
const data = JSON.parse(process.argv[3]);
const filters = JSON.parse(process.argv[4]);
const report = { data, get_filter_values: () => filters };
const open = (rid, col) => { sandbox.routed = null; sandbox.alert = null; sandbox.frappe.route_options = null; pl.open_detail(report, rid, col);
	return { route: sandbox.routed, options: sandbox.frappe.route_options, alert: sandbox.alert }; };
const passthrough = (v) => String(v);
const out = { section: open(2, "p1"), own: open(3, "p0"), charged_in: open(4, "p0"), subtotal: open(5, "total"), bridge_out: open(6, "p0"), books: open(7, "p0"), header: open(1, "p0") };
out.fmt = {
	amount: pl.formatter(5, 0, { fieldname: "p0" }, data[1], passthrough),
	negative: pl.formatter(-5, 0, { fieldname: "p0" }, { ...data[1], p0: -5 }, passthrough),
	ratio: pl.formatter(40, 0, { fieldname: "p0" }, { is_ratio: 1, p0: 40, label: "x" }, passthrough),
	ratio_label: pl.formatter("GP", 0, { fieldname: "label" }, { is_ratio: 1 }, passthrough),
	diff_ok: pl.formatter(0, 0, { fieldname: "p0" }, { is_difference: 1, p0: 0, rid: 9 }, passthrough),
	diff_bad: pl.formatter(2, 0, { fieldname: "p0" }, { is_difference: 1, p0: 2, rid: 9 }, passthrough),
	change: pl.formatter(7, 0, { fieldname: "change" }, { ...data[1], change: 7 }, passthrough),
	header: pl.formatter(5, 0, { fieldname: "p0" }, data[0], passthrough),
	voucher: pl.detail_formatter("x", 0, { fieldname: "label" }, { is_voucher: 1, voucher_type: "Journal Entry", voucher_no: "JV-1" }, passthrough),
	back: pl.statement_route(filters),
};
console.log(JSON.stringify(out));
"""

JS_DATA = [
	{"rid": 1, "label": "Bridge", "is_header": 1, "columns_meta": {"p0": {"from_date": "2026-01-01", "to_date": "2026-01-31"}, "p1": {"from_date": "2026-02-01", "to_date": "2026-04-30"}, "total": {"from_date": "2026-01-01", "to_date": "2026-04-30"}}},
	{"rid": 2, "label": "Operating expenses", "is_section": 1, "section": "OPEX", "drill_title": "Operating expenses", "p0": 5},
	{"rid": 3, "label": "52010700001 - Rent", "is_line": 1, "section": "OPEX", "origin": "own", "account": "Rent - SFB", "drill_title": "Operating expenses"},
	{"rid": 4, "label": "↳ SFB: Rent", "is_line": 1, "section": "OPEX", "origin": "in", "account": "Rent - SFB", "source_company": "Steel Force Trading WLL", "drill_title": "Operating expenses"},
	{"rid": 5, "label": "Gross profit", "is_total": 1, "section": "GROSS", "drill_title": "Gross profit"},
	{"rid": 6, "label": "Less: charged out", "is_bridge": 1, "origin": "out", "drill_title": "charged out"},
	{"rid": 7, "label": "Profit per books", "is_bridge": 1, "origin": "books"},
]
JS_FILTERS = {"company": "Co", "from_date": "2026-01-01", "to_date": "2026-04-30", "cost_center": "Main - SFTO", "branch": ["SFSB"]}


class TestTheDrillDown(FrappeTestCase):
	def run_js(self, filters=None):
		node = shutil.which("node")
		if not node:
			self.skipTest("node is not installed")
		done = subprocess.run([node, "-e", JS_RUNNER, "x", os.path.join(ROOT, "public", "js", "mis_profit_loss.js"), json.dumps(JS_DATA),
			json.dumps(filters or JS_FILTERS)], capture_output=True, text=True, encoding="utf-8", timeout=60)
		self.assertEqual(done.returncode, 0, done.stderr)
		return json.loads(done.stdout)

	def test_a_section_opens_its_vouchers_for_the_column_clicked(self):
		out = self.run_js()["section"]
		self.assertEqual(out["route"], ["query-report", "MIS Profit and Loss Detail"])
		o = out["options"]
		self.assertEqual((o["line"], o["from_date"], o["to_date"], o["group_by"]), ("Operating expenses", "2026-02-01", "2026-04-30", "Voucher Type"))
		self.assertEqual((o["cost_center"], o["branch"]), ("Main - SFTO", ["SFSB"]))
		self.assertNotIn("origin", o)

	def test_an_own_line_opens_its_account_and_a_charged_in_line_its_account_company_and_origin(self):
		own = self.run_js()["own"]["options"]
		self.assertEqual((own["origin"], own["account"], own["group_by"]), ("Own books", ["Rent - SFB"], "Vouchers"))
		charged = self.run_js()["charged_in"]["options"]
		self.assertEqual((charged["origin"], charged["source_company"], charged["account"]), ("Charged in", "Steel Force Trading WLL", ["Rent - SFB"]))

	def test_a_subtotal_opens_for_the_whole_range_and_carries_no_origin(self):
		o = self.run_js()["subtotal"]["options"]
		self.assertEqual((o["line"], o["from_date"], o["to_date"]), ("Gross profit", "2026-01-01", "2026-04-30"))

	def test_a_bridge_row_opens_by_origin_with_no_section(self):
		o = self.run_js()["bridge_out"]["options"]
		self.assertEqual(o["origin"], "Charged out")
		self.assertNotIn("line", o)

	def test_the_books_row_and_a_header_have_nothing_to_open(self):
		for key in ("books", "header"):
			out = self.run_js()[key]
			self.assertIsNone(out["route"], key)
			self.assertIn("Nothing to open", out["alert"]["message"])

	def test_a_cost_centre_column_carries_its_cost_centre_instead_of_the_filters(self):
		data = json.loads(json.dumps(JS_DATA))
		data[0]["columns_meta"] = {"p0": {"cost_center": "WH - SFTO"}}
		node = shutil.which("node")
		if not node:
			self.skipTest("node is not installed")
		done = subprocess.run([node, "-e", JS_RUNNER, "x", os.path.join(ROOT, "public", "js", "mis_profit_loss.js"), json.dumps(data), json.dumps(JS_FILTERS)],
			capture_output=True, text=True, encoding="utf-8", timeout=60)
		self.assertEqual(json.loads(done.stdout)["own"]["options"]["cost_center"], "WH - SFTO")

	def test_amounts_are_links_ratios_are_plain_percentages_and_a_clean_difference_is_green(self):
		fmt = self.run_js()["fmt"]
		self.assertIn('class="sf-mis-drill"', fmt["amount"])
		self.assertIn("#c62828", fmt["negative"])
		self.assertIn("40.0%", fmt["ratio"])
		self.assertNotIn("sf-mis-drill", fmt["ratio"])
		self.assertIn("<i", fmt["ratio_label"])
		self.assertIn("#2e7d32", fmt["diff_ok"])
		self.assertIn("#c62828", fmt["diff_bad"])
		self.assertNotIn("sf-mis-drill", fmt["change"])
		self.assertEqual(fmt["header"], "")

	def test_a_vouchers_number_opens_the_voucher_and_the_detail_leads_back(self):
		fmt = self.run_js()["fmt"]
		self.assertEqual(fmt["voucher"], '<a href="/app/Journal Entry/JV-1">JV-1</a>')
		self.assertEqual(fmt["back"], {"company": "Co", "filter_based_on": "Date Range", "from_date": "2026-01-01", "to_date": "2026-04-30", "cost_center": "Main - SFTO", "branch": ["SFSB"]})


class TestWhatTheSystemShips(FrappeTestCase):
	def test_the_report_files_exist_and_are_for_accountants(self):
		for name, title in (("mis_profit_and_loss", "MIS Profit and Loss"), ("mis_profit_and_loss_detail", "MIS Profit and Loss Detail")):
			base = os.path.join(ROOT, "sf_trading", "report", name)
			for ext in ("py", "js", "json"):
				self.assertTrue(os.path.exists(os.path.join(base, name + "." + ext)), name + "." + ext)
			report = json.loads(read("sf_trading", "report", name, name + ".json"))
			self.assertEqual((report["name"], report["report_type"], report["ref_doctype"]), (title, "Script Report", "GL Entry"))
			self.assertIn({"role": "Accounts Manager"}, report["roles"])

	def test_the_filters_of_the_profit_and_loss_are_the_ones_the_module_reads(self):
		js = read("sf_trading", "report", "mis_profit_and_loss", "mis_profit_and_loss.js")
		for name in ("company", "periodicity", "comparison", "accumulated_values", "columns_by", "detail", "branch", "cost_center", "show_percent", "show_zero_rows"):
			self.assertIn('fieldname: "%s"' % name, js)
		self.assertIn("sf_trading.mis_pl.period_filters()", js)
		self.assertIn("Half-Yearly", js)
		shared = read("public", "js", "mis_profit_loss.js")
		for name in ("filter_based_on", "quick_range", "from_date", "to_date", "from_fiscal_year", "to_fiscal_year"):
			self.assertIn('fieldname: "%s"' % name, shared)
		for option in mis.COMPARISONS + mis.COLUMNS_BY + mis.DETAIL_LEVELS:
			self.assertIn(option, js)

	def test_the_drill_script_is_loaded_everywhere(self):
		self.assertIn("/assets/sf_trading/js/mis_profit_loss.js", read("hooks.py"))
		js = read("public", "js", "mis_profit_loss.js")
		self.assertIn("MIS Profit and Loss Detail", js)


class TestThePeriodFilters(FrappeTestCase):
	"""Filter Based On, as on the Profit and Loss Statement: a date range, or fiscal years."""

	def fy(self, rows):
		return patch.object(mis.frappe.db, "get_value", MagicMock(side_effect=lambda dt, name, fields: rows.get(name)), create=True)

	def test_a_date_range_is_the_two_dates_and_the_default_filter(self):
		filters = frappe._dict(from_date="2026-02-01", to_date="2026-03-31")
		mis.resolve_period(filters)
		self.assertEqual((filters.filter_based_on, filters.from_date, filters.to_date), ("Date Range", date(2026, 2, 1), date(2026, 3, 31)))

	def test_a_fiscal_year_is_its_first_day_to_its_last(self):
		filters = frappe._dict(filter_based_on="Fiscal Year", from_fiscal_year="2026", to_fiscal_year="2026", from_date="2020-01-01", to_date="2020-01-02")
		with self.fy({"2026": (date(2026, 1, 1), date(2026, 12, 31))}):
			mis.resolve_period(filters)
		self.assertEqual((filters.from_date, filters.to_date), (date(2026, 1, 1), date(2026, 12, 31)), "the stale dates behind a hidden filter are not read")

	def test_several_fiscal_years_run_from_the_first_of_the_start_year_to_the_last_of_the_end_year(self):
		rows = {"2025": (date(2025, 4, 1), date(2026, 3, 31)), "2026": (date(2026, 4, 1), date(2027, 3, 31))}
		filters = frappe._dict(filter_based_on="Fiscal Year", from_fiscal_year="2025", to_fiscal_year="2026")
		with self.fy(rows):
			mis.resolve_period(filters)
		self.assertEqual((filters.from_date, filters.to_date), (date(2025, 4, 1), date(2027, 3, 31)))

	def test_a_missing_end_year_means_the_start_year_alone(self):
		filters = frappe._dict(filter_based_on="Fiscal Year", from_fiscal_year="2026")
		with self.fy({"2026": (date(2026, 1, 1), date(2026, 12, 31))}):
			mis.resolve_period(filters)
		self.assertEqual(filters.to_date, date(2026, 12, 31))

	def test_a_start_year_after_the_end_year_an_unknown_year_and_no_year_are_refused(self):
		rows = {"2025": (date(2025, 1, 1), date(2025, 12, 31)), "2026": (date(2026, 1, 1), date(2026, 12, 31))}
		with self.fy(rows):
			with self.assertRaises(frappe.ValidationError):
				mis.resolve_period(frappe._dict(filter_based_on="Fiscal Year", from_fiscal_year="2026", to_fiscal_year="2025"))
			with self.assertRaises(frappe.ValidationError):
				mis.resolve_period(frappe._dict(filter_based_on="Fiscal Year", from_fiscal_year="1999"))
		with self.assertRaises(frappe.ValidationError):
			mis.resolve_period(frappe._dict(filter_based_on="Fiscal Year"))
		with self.assertRaises(frappe.ValidationError):
			mis.resolve_period(frappe._dict(filter_based_on="Whenever"))

	def test_from_after_to_is_still_refused_for_a_date_range(self):
		with self.assertRaises(frappe.ValidationError):
			mis.resolve_period(frappe._dict(from_date="2026-05-01", to_date="2026-01-01"))

	def test_half_yearly_columns_are_cut_at_june_and_december(self):
		periods = cfs.period_list("2026-01-01", "2026-12-31", "Half-Yearly")
		self.assertEqual([(p["label"], str(p["from"]), str(p["to"])) for p in periods], [("H1 2026", "2026-01-01", "2026-06-30"), ("H2 2026", "2026-07-01", "2026-12-31")])
		part = cfs.period_list("2026-03-15", "2026-08-20", "Half-Yearly")
		self.assertEqual([(str(p["from"]), str(p["to"])) for p in part], [("2026-03-15", "2026-06-30"), ("2026-07-01", "2026-08-20")])
		self.assertIn("Half-Yearly", mis.PERIODICITY)
		self.assertNotIn("Half-Yearly", cfs.PERIODICITY, "the cash flow statement keeps its own list")


class TestRunningTotals(FrappeTestCase):
	def rows(self):
		return [
			("41010100001 - Sales Revenue - SFB", date(2026, 1, 5), "", "", 100.0),
			("41010100001 - Sales Revenue - SFB", date(2026, 2, 5), "", "", 200.0),
			("41010100001 - Sales Revenue - SFB", date(2026, 3, 5), "", "", 300.0),
			("52010700001 - Rent Expense - SFB", date(2026, 2, 7), "", "Main - SFB", -40.0),
			("52010700001 - Rent Expense - SFB", date(2026, 3, 7), "", "", -10.0),
		]

	def test_every_column_is_the_total_up_to_and_including_it(self):
		inn = [(BHD_CO, "52010700001 - Rent Expense - SFB", date(2026, 3, 9), "Main - SFTO", -20.0)]
		model, _r = run_model(self.rows(), inn, month_periods("2026-01-01", "2026-03-31", "Monthly"))
		mis.accumulate(model)
		out = mis.Out(model.columns, "OMR", 0, accumulated=True)
		totals = mis.statement_rows(model, out, "Sections and Accounts", True)
		bridge = mis.bridge_rows(model, out, OMR_CO, totals["net"])
		revenue = next(r for r in out.data if r["label"] == "Revenue")
		self.assertEqual([revenue["p0"], revenue["p1"], revenue["p2"]], [100.0, 300.0, 600.0])
		self.assertNotIn("total", revenue, "a running total ends in its last column")
		self.assertEqual(bridge["difference"], [0.0, 0.0, 0.0])
		self.assertEqual(bridge["books"], [100.0, 260.0, 550.0])
		self.assertEqual(bridge["out"], [0.0, -40.0, -40.0])

	def test_the_margins_follow_the_running_totals(self):
		model, _r = run_model(self.rows(), [], month_periods("2026-01-01", "2026-03-31", "Monthly"))
		mis.accumulate(model)
		out = mis.Out(model.columns, "OMR", 0, accumulated=True)
		mis.statement_rows(model, out, "Sections and Accounts", True)
		net = next(r for r in out.data if r["label"] == "Net profit margin %")
		self.assertEqual([round(net["p%d" % i], 3) for i in range(3)], [100.0, 100.0, round((600.0 - 10.0) / 600.0 * 100, 3)])

	def test_running_totals_apply_to_periods_of_one_range_only(self):
		two = mis.Columns("Period", cfs.period_list("2026-01-01", "2026-02-28", "Monthly"))
		self.assertTrue(mis.accumulating(frappe._dict(accumulated_values=1), two))
		self.assertFalse(mis.accumulating(frappe._dict(accumulated_values=0), two))
		self.assertFalse(mis.accumulating(frappe._dict(accumulated_values=1), mis.Columns("Period", cfs.period_list("2026-01-01", "2026-02-28", "Total"))))
		compared = mis.Columns("Period", two.items, comparison=True)
		self.assertFalse(mis.accumulating(frappe._dict(accumulated_values=1), compared))
		self.assertFalse(mis.accumulating(frappe._dict(accumulated_values=1), mis.Columns("Cost Center", [{"label": "a", "key": "a"}, {"label": "b", "key": "b"}])))

	def test_a_running_total_drills_from_the_first_day(self):
		columns = month_periods("2026-01-01", "2026-03-31", "Monthly")
		meta = mis.columns_meta(columns, date(2026, 1, 1), date(2026, 3, 31), accumulated=True)
		self.assertEqual(meta["p2"], {"from_date": "2026-01-01", "to_date": "2026-03-31"})
		self.assertEqual(meta["p0"], {"from_date": "2026-01-01", "to_date": "2026-01-31"})
		self.assertNotIn("total", meta)
		plain = mis.columns_meta(columns, date(2026, 1, 1), date(2026, 3, 31))
		self.assertEqual(plain["p2"], {"from_date": "2026-03-01", "to_date": "2026-03-31"})


class TestRatiosAndCards(FrappeTestCase):
	def test_a_ratio_rows_total_is_the_ratio_of_the_totals_not_the_sum_of_the_columns(self):
		own = [("41010100001 - Sales Revenue - SFB", date(2026, 1, 5), "", "", 100.0), ("51010100001 - Cost Of Goods Sold - SFB", date(2026, 1, 6), "", "", -50.0),
			("41010100001 - Sales Revenue - SFB", date(2026, 2, 5), "", "", 300.0), ("51010100001 - Cost Of Goods Sold - SFB", date(2026, 2, 6), "", "", -60.0)]
		model, _r = run_model(own, [], month_periods("2026-01-01", "2026-02-28", "Monthly"))
		out, _t, _b = render(model)
		gross = next(r for r in out.data if r["label"] == "Gross profit margin %")
		self.assertEqual((gross["p0"], gross["p1"]), (50.0, 80.0))
		self.assertEqual(gross["total"], 72.5, "(400 - 110) / 400, not 50 + 80")

	def test_a_ratio_row_changes_in_points_and_has_no_percent_change(self):
		own = [("41010100001 - Sales Revenue - SFB", date(2026, 1, 5), "", "", 100.0), ("51010100001 - Cost Of Goods Sold - SFB", date(2026, 1, 6), "", "", -50.0),
			("41010100001 - Sales Revenue - SFB", date(2025, 12, 5), "", "", 100.0), ("51010100001 - Cost Of Goods Sold - SFB", date(2025, 12, 6), "", "", -80.0)]
		columns = mis.Columns("Period", [{"label": "now", "from": date(2026, 1, 1), "to": date(2026, 1, 31)}, {"label": "before", "from": date(2025, 12, 1), "to": date(2025, 12, 31)}], comparison=True)
		model, _r = run_model(own, [], columns)
		out, _t, _b = render(model)
		gross = next(r for r in out.data if r["label"] == "Gross profit margin %")
		self.assertEqual((gross["p0"], gross["p1"], gross["change"]), (50.0, 20.0, 30.0))
		self.assertNotIn("change_pct", gross)
		self.assertNotIn("total", gross)

	def test_a_percent_change_from_nothing_is_blank_not_zero(self):
		own = [("41010100001 - Sales Revenue - SFB", date(2026, 1, 5), "", "", 100.0)]
		columns = mis.Columns("Period", [{"label": "now", "from": date(2026, 1, 1), "to": date(2026, 1, 31)}, {"label": "before", "from": date(2025, 12, 1), "to": date(2025, 12, 31)}], comparison=True)
		model, _r = run_model(own, [], columns)
		out, _t, _b = render(model)
		revenue = next(r for r in out.data if r["label"] == "Revenue")
		self.assertEqual(revenue["change"], 100.0)
		self.assertIsNone(revenue["change_pct"])

	def test_percent_of_revenue_is_blank_in_a_column_with_no_revenue_and_has_a_total(self):
		own = [("41010100001 - Sales Revenue - SFB", date(2026, 1, 5), "", "", 200.0), ("52010700001 - Rent Expense - SFB", date(2026, 1, 5), "", "", -50.0),
			("52010700001 - Rent Expense - SFB", date(2026, 2, 5), "", "", -30.0)]
		model, _r = run_model(own, [], month_periods("2026-01-01", "2026-02-28", "Monthly"))
		out, _t, _b = render(model)
		revenue = next(r for r in out.data if r["label"] == "Revenue")
		mis.percent_columns(out.data, model.columns, revenue)
		rent = next(r for r in out.data if r["label"].startswith("52010700001"))
		self.assertEqual((rent["pct0"], rent["pct_total"]), (25.0, 40.0))
		self.assertIsNone(rent["pct1"], "no revenue in February: no share to show")

	def test_the_cards_of_a_comparison_are_the_current_period_not_both_added(self):
		result = TestTheReport.run_report(TestTheReport(), {"comparison": "Previous Period", "from_date": "2026-03-01", "to_date": "2026-04-30"})
		cards = {c["label"]: c["value"] for c in result[4]}
		revenue = next(r for r in result[1] if r["label"] == "Revenue")
		self.assertEqual(cards["Revenue"], round(revenue["p0"], 3), "the previous period is beside it, not part of it")

	def test_the_cards_of_running_totals_are_the_last_column(self):
		result = TestTheReport.run_report(TestTheReport(), {"periodicity": "Monthly", "accumulated_values": 1})
		cards = {c["label"]: c["value"] for c in result[4]}
		revenue = next(r for r in result[1] if r["label"] == "Revenue")
		self.assertEqual(cards["Revenue"], round(revenue["p3"], 3))
		self.assertNotIn("total", [c["fieldname"] for c in result[0]])
		self.assertIn("running total", result[2])

	def test_the_ebitda_card_is_the_ebitda_row(self):
		result = TestTheReport.run_report(TestTheReport(), {"periodicity": "Total"})
		cards = {c["label"]: c["value"] for c in result[4]}
		ebitda = next(r for r in result[1] if r["label"] == "EBITDA")
		self.assertEqual(cards["EBITDA"], round(ebitda["p0"], 3))


class TestChargedInPrivacy(FrappeTestCase):
	MINE = ["Main - SFTO", "WH - SFTO"]

	def scope(self, cost_center=None, permitted=None):
		filters = frappe._dict(cost_center=cost_center)
		with patch.object(mis.money, "permitted", return_value=permitted), patch.object(mis.money, "cost_centers", side_effect=lambda cc: [cc]):
			return mis.charged_in_scope(filters, self.MINE, ALTS)

	def test_nothing_narrows_it_without_a_filter_or_a_permission(self):
		self.assertIsNone(self.scope())

	def test_the_cost_centre_filter_narrows_it(self):
		self.assertEqual(self.scope(cost_center="WH - SFTO"), {"WH - SFTO"})

	def test_a_users_cost_centre_permissions_narrow_what_is_charged_in_to_them(self):
		self.assertEqual(self.scope(permitted={"WH - SFTO"}), {"WH - SFTO"})
		self.assertEqual(self.scope(cost_center="Main - SFTO", permitted={"WH - SFTO"}), set())

	def test_an_empty_scope_means_no_charged_in_rows_not_all_of_them(self):
		with patch.object(mis.frappe.db, "sql", MagicMock(side_effect=AssertionError("must not query")), create=True):
			filters = frappe._dict(company=OMR_CO, range_from=date(2026, 1, 1), range_to=date(2026, 4, 30))
			self.assertEqual(mis.fetch_in(filters, self.MINE, set()), [])


QUICK_RUNNER = r"""
const fs = require("fs"), vm = require("vm");
const iso = (d) => d.toISOString().slice(0, 10);
const utc = (y, m, d) => new Date(Date.UTC(y, m, d));
const TODAY = utc(2026, 9, 19);
const datetime = {
	get_today: () => iso(TODAY),
	add_days: (s, n) => { const d = new Date(s + "T00:00:00Z"); d.setUTCDate(d.getUTCDate() + n); return iso(d); },
	add_months: (s, n) => { const d = new Date(s + "T00:00:00Z"); const day = d.getUTCDate(); d.setUTCDate(1); d.setUTCMonth(d.getUTCMonth() + n);
		const last = new Date(Date.UTC(d.getUTCFullYear(), d.getUTCMonth() + 1, 0)).getUTCDate(); d.setUTCDate(Math.min(day, last)); return iso(d); },
	month_start: () => iso(utc(2026, 9, 1)), month_end: () => iso(utc(2026, 9, 31)),
	quarter_start: () => iso(utc(2026, 9, 1)), quarter_end: () => iso(utc(2026, 11, 31)),
	year_start: () => iso(utc(2026, 0, 1)), year_end: () => iso(utc(2026, 11, 31)),
};
const sandbox = { console, __: (s) => s, cint: (v) => parseInt(v) || 0, flt: (v) => parseFloat(v) || 0 };
sandbox.frappe = {
	provide(path) { path.split(".").reduce((o, k) => (o[k] = o[k] || {}, o[k]), sandbox); },
	datetime, boot: JSON.parse(process.argv[3]),
};
sandbox.sf_trading = {};
vm.createContext(sandbox);
vm.runInContext(fs.readFileSync(process.argv[2], "utf8"), sandbox);
const pl = sandbox.sf_trading.mis_pl;
const ranges = {};
for (const name of ["", "Today", "This Month", "Last Month", "This Quarter", "Last Quarter", "This Year", "Last Year", "Last 12 Months"]) ranges[name] = pl.quick_range_dates(name);
console.log(JSON.stringify({ ranges, filters: pl.period_filters() }));
"""


class TestTheQuickRangesAndFiscalYearFilters(FrappeTestCase):
	def run_js(self, boot):
		node = shutil.which("node")
		if not node:
			self.skipTest("node is not installed")
		done = subprocess.run([node, "-e", QUICK_RUNNER, "x", os.path.join(ROOT, "public", "js", "mis_profit_loss.js"), json.dumps(boot)],
			capture_output=True, text=True, encoding="utf-8", timeout=60)
		self.assertEqual(done.returncode, 0, done.stderr)
		return json.loads(done.stdout)

	def test_each_quick_range_fills_the_dates_a_person_means_by_it(self):
		ranges = self.run_js({})["ranges"]
		self.assertIsNone(ranges[""], "blank is a custom range: nothing is filled in")
		self.assertEqual(ranges["Today"], ["2026-10-19", "2026-10-19"])
		self.assertEqual(ranges["This Month"], ["2026-10-01", "2026-10-31"])
		self.assertEqual(ranges["Last Month"], ["2026-09-01", "2026-09-30"])
		self.assertEqual(ranges["This Quarter"], ["2026-10-01", "2026-12-31"])
		self.assertEqual(ranges["Last Quarter"], ["2026-07-01", "2026-09-30"])
		self.assertEqual(ranges["This Year"], ["2026-01-01", "2026-12-31"])
		self.assertEqual(ranges["Last Year"], ["2025-01-01", "2025-12-31"])
		self.assertEqual(ranges["Last 12 Months"], ["2025-10-20", "2026-10-19"], "twelve whole months ending today")

	def test_the_filters_mirror_the_profit_and_loss_statement_and_show_one_pair_at_a_time(self):
		filters = {f["fieldname"]: f for f in self.run_js({"current_fiscal_year": ["2026", "2026-01-01", "2026-12-31"]})["filters"]}
		self.assertEqual(list(filters), ["filter_based_on", "quick_range", "from_date", "to_date", "from_fiscal_year", "to_fiscal_year"])
		self.assertEqual(filters["filter_based_on"]["options"].split("\n"), list(mis.FILTER_BASED_ON))
		for name in ("quick_range", "from_date", "to_date"):
			self.assertIn("!= 'Fiscal Year'", filters[name]["depends_on"], name)
		for name in ("from_fiscal_year", "to_fiscal_year"):
			self.assertIn("== 'Fiscal Year'", filters[name]["depends_on"], name)
			self.assertEqual(filters[name]["options"], "Fiscal Year")
		self.assertEqual((filters["from_date"]["default"], filters["to_date"]["default"]), ("2026-01-01", "2026-10-19"))
		self.assertEqual((filters["from_fiscal_year"]["default"], filters["to_fiscal_year"]["default"]), ("2026", "2026"))
		for name in ("from_date", "to_date", "from_fiscal_year", "to_fiscal_year"):
			self.assertNotIn("reqd", filters[name], "a hidden filter must not block the report: it is required only when shown")
			self.assertEqual(filters[name]["mandatory_depends_on"], filters[name]["depends_on"])

	def test_with_no_fiscal_year_booted_the_dates_default_to_this_year(self):
		filters = {f["fieldname"]: f for f in self.run_js({})["filters"]}
		self.assertEqual(filters["from_date"]["default"], "2026-01-01")
		self.assertIsNone(filters["from_fiscal_year"].get("default"))
