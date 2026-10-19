# sf_trading/tests/test_mis_profit_loss_detail.py
"""MIS Profit and Loss Detail (sf_trading/mis_profit_loss_detail.py): the vouchers behind a figure.

The database is patched at the module's own seams, so nothing is created or read:

    bench --site <scratch-site> run-tests --module sf_trading.tests.test_mis_profit_loss_detail

The property under test: for EVERY section and subtotal of the profit and loss, in every origin, on random books
posted by two companies and charged between them, the vouchers this report lists add up to exactly the figure
the profit and loss shows for it.
"""

import os
import random
from datetime import date
from unittest.mock import MagicMock, patch

import frappe
from frappe.tests.utils import FrappeTestCase

from sf_trading import mis_profit_loss as mis
from sf_trading import mis_profit_loss_detail as detail
from sf_trading.tests.test_mis_profit_loss import ACCOUNTS, ALTS, BHD_CO, OMR_CO, RATE, FixedRates, month_periods, render, run_model

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TYPES = ("Sales Invoice", "Purchase Invoice", "Journal Entry", "Expense Claim")


def random_ledger(seed, n=300):
	"""GL rows (one per voucher and account) of both companies, as the detail query returns them."""
	rng = random.Random(seed)
	names = list(ACCOUNTS)
	rows = []
	for i in range(n):
		company = rng.choice((OMR_CO, BHD_CO))
		other = BHD_CO if company == OMR_CO else OMR_CO
		alt = rng.choice(["", "", ""] + [a for a, (c, _cc) in ALTS.items() if c == other] + [a for a, (c, _cc) in ALTS.items() if c == company][:0])
		net = round(rng.uniform(1, 900), 3) * rng.choice((1, -1))
		rows.append(dict(company=company, voucher_type=rng.choice(TYPES), voucher_no="V-%04d" % i, account=rng.choice(names),
			party_type=None, party=None, branch="SFSB", cost_center=rng.choice(("SFSB - SFB", "Main - SFTO")), alt=alt,
			posting_date=date(2026, rng.randint(1, 4), rng.randint(1, 28)), remarks="r%d" % i,
			debit=max(-net, 0.0), credit=max(net, 0.0)))
	return rows


def own_rows_of(ledger, company):
	by = {}
	for r in ledger:
		if r["company"] == company:
			key = (r["account"], r["posting_date"], r["cost_center"], r["alt"])
			by[key] = by.get(key, 0.0) + r["credit"] - r["debit"]
	return [(a, d, cc, alt, net) for (a, d, cc, alt), net in by.items()]


def in_rows_of(ledger, company):
	by = {}
	for r in ledger:
		target = ALTS.get(r["alt"])
		if r["company"] != company and target and target[0] == company:
			key = (r["company"], r["account"], r["posting_date"], r["alt"])
			by[key] = by.get(key, 0.0) + r["credit"] - r["debit"]
	return [(c, a, d, alt, net) for (c, a, d, alt), net in by.items()]


def filters(**extra):
	base = dict(company=OMR_CO, from_date=date(2026, 1, 1), to_date=date(2026, 4, 30), range_from=date(2026, 1, 1), range_to=date(2026, 4, 30))
	base.update(extra)
	return frappe._dict(base)


class Seams:
	def __init__(self, ledger, company=OMR_CO):
		self.ledger, self.company, self.patches = ledger, company, []

	def __enter__(self):
		entries = [dict(r) for r in self.ledger]
		for target, attr, value in [
			(detail, "fetch_entries", MagicMock(side_effect=lambda f, alts, sql, params, want_in, want_own: [dict(r) for r in entries])),
			(mis, "_own_rows", MagicMock(return_value=own_rows_of(self.ledger, self.company))),
			(mis, "fetch_in", MagicMock(return_value=in_rows_of(self.ledger, self.company))),
			(mis, "alternatives", MagicMock(return_value=ALTS)),
			(mis, "pl_accounts", MagicMock(return_value=ACCOUNTS)),
			(mis, "has_dimension", MagicMock(return_value=True)),
			(mis, "Rates", FixedRates),
			(mis.money, "scope_params", MagicMock(return_value=("", {}))),
			(mis.frappe, "has_permission", MagicMock(return_value=True)),
			(mis.frappe, "get_cached_value", MagicMock(side_effect=lambda dt, name, field: ("BHD" if name == BHD_CO else "OMR") if field == "default_currency" else name[:4])),
		]:
			p = patch.object(target, attr, value, create=True)
			p.start()
			self.patches.append(p)
		return self

	def __exit__(self, *exc):
		for p in reversed(self.patches):
			p.stop()


def statement_figures(ledger, company=OMR_CO):
	columns = month_periods(kind="Total")
	own, inn = own_rows_of(ledger, company), in_rows_of(ledger, company)
	model, _r = run_model(own, inn, columns, company=company)
	out, totals, bridge = render(model)
	figures = {r["drill_title"]: r["p0"] for r in out.data if r.get("drill_title") and (r.get("is_section") or r.get("is_total")) and r["drill_title"] in {s["label"] for s in mis.LAYOUT}}
	return figures, model, bridge


class TestTheVouchersAddUpToTheFigure(FrappeTestCase):
	def test_every_section_and_subtotal_ties_to_its_vouchers_on_random_books_of_two_companies(self):
		for seed in range(12):
			ledger = random_ledger(seed)
			figures, _model, _bridge = statement_figures(ledger)
			self.assertGreaterEqual(len(figures), 10)
			with Seams(ledger):
				for title, shown in figures.items():
					result = detail.build(filters(line=title))
					self.assertAlmostEqual(result["total"], shown, 5, "seed %s: %s" % (seed, title))

	def test_the_whole_profit_is_the_management_net_profit_and_the_origins_split_it(self):
		for seed in range(8):
			ledger = random_ledger(seed)
			_f, _m, bridge = statement_figures(ledger)
			with Seams(ledger):
				whole = detail.build(filters())["total"]
				own = detail.build(filters(origin="Own books"))["total"]
				charged_in = detail.build(filters(origin="Charged in"))["total"]
				charged_out = detail.build(filters(origin="Charged out"))["total"]
			self.assertAlmostEqual(whole, bridge["mis"][0], 5, seed)
			self.assertAlmostEqual(own + charged_in, whole, 5, seed)
			self.assertAlmostEqual(charged_in, bridge["in"][0], 5, seed)
			self.assertAlmostEqual(charged_out, -bridge["out"][0], 5, "charged out is shown as the amount added back")
			self.assertAlmostEqual(own, bridge["books"][0] - bridge["out"][0], 5, seed)

	def test_a_charged_in_entry_is_worth_its_amount_in_this_companys_currency(self):
		ledger = [dict(company=BHD_CO, voucher_type="Journal Entry", voucher_no="JV-1", account="52010700001 - Rent Expense - SFB", party_type=None, party=None,
			branch="", cost_center="SFSB - SFB", alt="Main - SFTO", posting_date=date(2026, 2, 1), remarks="", debit=100.0, credit=0.0)]
		with Seams(ledger):
			result = detail.build(filters(origin="Charged in", line="Operating expenses"))
		entry = result["entries"][0]
		self.assertAlmostEqual(entry["amount"], 100.0 * RATE, 6, "a cost, shown as a positive cost")
		self.assertAlmostEqual(entry["debit"], 100.0 * RATE, 6)
		self.assertEqual((entry["origin"], entry["company"], entry["rate"]), ("in", BHD_CO, RATE))

	def test_an_entry_charged_to_a_third_company_is_in_nobodys_figure_here(self):
		ledger = [dict(company=BHD_CO, voucher_type="Journal Entry", voucher_no="JV-1", account="52010700001 - Rent Expense - SFB", party_type=None, party=None,
			branch="", cost_center="", alt="Main - SFB", posting_date=date(2026, 2, 1), remarks="", debit=100.0, credit=0.0)]
		with Seams(ledger):
			self.assertEqual(detail.build(filters())["entries"], [])


class TestTheSelection(FrappeTestCase):
	def ledger(self):
		return random_ledger(5)

	def test_an_account_filter_keeps_only_that_account(self):
		ledger = self.ledger()
		pick = next(r["account"] for r in ledger if r["company"] == OMR_CO and not r["alt"])
		with Seams(ledger):
			result = detail.build(filters(account=[pick]))
		self.assertTrue(result["entries"])
		self.assertEqual({e["account_name"] for e in result["entries"]}, {pick})

	def test_a_source_company_filter_keeps_the_charged_in_rows_of_that_company_and_the_charged_out_rows_to_it(self):
		with Seams(self.ledger()):
			charged_in = detail.build(filters(origin="Charged in", source_company=BHD_CO))
			charged_out = detail.build(filters(origin="Charged out", source_company=BHD_CO))
		self.assertTrue(charged_in["entries"] and all(e["company"] == BHD_CO for e in charged_in["entries"]))
		self.assertTrue(all(e["origin"] == "out" for e in charged_out["entries"]))

	def test_an_unknown_section_origin_group_or_a_bad_date_is_refused(self):
		with Seams(self.ledger()):
			for bad in (dict(line="Nonsense"), dict(origin="Elsewhere"), dict(group_by="Weekly"), dict(from_date=date(2026, 9, 1))):
				with self.assertRaises(frappe.ValidationError, msg=str(bad)):
					detail.build(filters(**bad))

	def test_a_missing_rate_is_said_and_those_amounts_are_left_out(self):
		class NoRates(mis.Rates):
			@staticmethod
			def lookup(*_a):
				return 0.0

		with Seams(self.ledger()), patch.object(mis, "Rates", NoRates):
			result = detail.build(filters(origin="Charged in"))
		self.assertEqual(result["total"], 0.0)
		self.assertEqual(result["rates"].missing, {"BHD"})


class TestTheRows(FrappeTestCase):
	def test_every_way_of_grouping_adds_up_to_the_same_total(self):
		ledger = random_ledger(2)
		with Seams(ledger):
			result = detail.build(filters())
		for mode in detail.GROUPS:
			report = detail.build_rows(result["entries"], mode, "OMR")
			top = [r for r in report.data if r["indent"] == 0]
			self.assertAlmostEqual(sum(r["amount"] for r in top), result["total"], 5, mode)

	def test_groups_by_company_and_origin_are_named_by_them(self):
		with Seams(random_ledger(2)):
			result = detail.build(filters())
		companies = {r["label"] for r in detail.build_rows(result["entries"], "Company", "OMR").data if r.get("is_group")}
		self.assertEqual(companies, {OMR_CO, BHD_CO})
		origins = {r["label"] for r in detail.build_rows(result["entries"], "Origin", "OMR").data if r.get("is_group")}
		self.assertEqual(origins, {"Own books", "Charged in"}, "charged out is not in the management view")

	def test_execute_returns_the_tie_message_a_total_row_and_cards(self):
		ledger = random_ledger(4)
		with Seams(ledger):
			columns, data, message, chart, summary = detail.execute(frappe._dict(company=OMR_CO, from_date="2026-01-01", to_date="2026-04-30", line="Operating expenses"))
		self.assertIn("the figure on the MIS Profit and Loss", message)
		self.assertNotIn("tell the accounts team", message)
		self.assertTrue(data[-1]["is_total"])
		self.assertEqual([c["label"] for c in summary], ["Amount", "Vouchers", "Ledger Entries", "Companies"])
		self.assertTrue(any(c["label"].startswith("Amount") for c in columns))

	def test_execute_says_so_when_the_two_roads_disagree(self):
		ledger = random_ledger(4)
		with Seams(ledger), patch.object(detail, "figure_total", return_value=12345.0):
			_c, _d, message, *_rest = detail.execute(frappe._dict(company=OMR_CO, from_date="2026-01-01", to_date="2026-04-30", line="Operating expenses"))
		self.assertIn("tell the accounts team", message)

	def test_charged_out_without_a_section_says_it_is_the_amount_added_back(self):
		with Seams(random_ledger(4)):
			_c, _d, message, *_rest = detail.execute(frappe._dict(company=OMR_CO, from_date="2026-01-01", to_date="2026-04-30", origin="Charged out"))
		self.assertIn("amount added back", message)


class TestTheQueries(FrappeTestCase):
	def test_the_detail_queries_bind_every_value_and_read_the_alternative_dimension(self):
		with patch.object(detail.frappe.db, "sql", MagicMock(return_value=[]), create=True) as sql:
			detail.fetch_entries(filters(), ["Main - SFTO"], " and gle.branch in %(branches)s", {"branches": ("SFSB",)}, True, True)
		own, inn = sql.call_args_list[0].args, sql.call_args_list[1].args
		self.assertIn("gle.company = %(company)s", own[0])
		self.assertIn("gle.branch in %(branches)s", own[0])
		self.assertIn("gle.company != %(company)s", inn[0])
		self.assertIn("gle.alternative_cost_center in %(alts)s", inn[0])
		self.assertEqual(inn[1]["alts"], ("Main - SFTO",))
		self.assertNotIn("branches", inn[1], "branch is the company's own")
		for text in (detail.DETAIL_OWN, detail.DETAIL_IN, detail.DETAIL_GROUP):
			self.assertNotIn("{", text)

	def test_nothing_to_charge_in_means_no_second_query(self):
		with patch.object(detail.frappe.db, "sql", MagicMock(return_value=[]), create=True) as sql:
			detail.fetch_entries(filters(), [], "", {}, True, True)
		self.assertEqual(sql.call_count, 1)

	def test_only_the_origin_asked_for_is_read(self):
		with patch.object(detail.frappe.db, "sql", MagicMock(return_value=[]), create=True) as sql:
			detail.fetch_entries(filters(), ["A"], "", {}, want_in=False, want_own=True)
			self.assertEqual(sql.call_count, 1)
			detail.fetch_entries(filters(), ["A"], "", {}, want_in=True, want_own=False)
			self.assertEqual(sql.call_count, 2)
