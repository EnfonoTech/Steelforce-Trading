# sf_trading/tests/test_cash_flow_statement.py
"""The Cash Flow Statement (sf_trading/cash_flow_statement.py).

The database is patched at the module's own seams, so nothing is created or read:

    bench --site <scratch-site> run-tests --module sf_trading.tests.test_cash_flow_statement

The heart of it is one property, tested on random books: whatever accounts there are and however they
are classified, the direct and the indirect statement both add up to the real movement of cash and bank.
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

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def read(*parts):
	with open(os.path.join(ROOT, *parts), encoding="utf-8") as f:
		return f.read()


def acc(name, root, account_type=""):
	return frappe._dict(name=name, root_type=root, account_type=account_type)


# a chart shaped like Steel Force's: what each is, and the line it must land on
CHART = [
	(acc("13010200001 - Petty Cash - SFB", "Asset", "Cash"), cfs.CASH),
	(acc("13020100024 - NBB - SFB", "Asset", "Bank"), cfs.CASH),
	(acc("13020100027 - PDC Account - SFB", "Asset", "Bank"), cfs.CASH),
	(acc("130302E0001 - Easy Touch - SFB", "Asset", "Receivable"), "AR"),
	(acc("Stock Delivered But Not Billed - SFB", "Asset", "Stock Delivered But Not Billed"), "AR"),
	(acc("220104S0001 - Supplier - SFB", "Liability", "Payable"), "AP"),
	(acc("22010100001 - Stock Received But Not Billed - SFB", "Liability", "Stock Received But Not Billed"), "AP"),
	(acc("13090100001 - Trading Inventory - SFB", "Asset", "Stock"), "INVENTORY"),
	(acc("Stock Billed But Not Delivered - SFB", "Liability", "Stock Billed But Not Delivered"), "DEFERRED_REV"),
	(acc("22040100005 - Advance Received From Customer - SFB", "Liability", "Current Liability"), "DEFERRED_REV"),
	(acc("22040100004 - Accrued Expenses - SFB", "Liability", "Current Liability"), "ACCRUED"),
	(acc("22030100004 - Salary Payable - SFB", "Liability", "Current Liability"), "ACCRUED"),
	(acc("201 - Leave Salary Payable - SFB", "Liability", ""), "ACCRUED"),
	(acc("22020100002 - Provision For Gratuity (EOSB) - SFB", "Liability", "Current Liability"), "PROVISIONS"),
	(acc("202 - Provision For Leave Salary - SFB", "Liability", ""), "PROVISIONS"),
	(acc("23010000001 - KFH Vehicle Loan - SFB", "Liability", "Current Liability"), "LOANS"),
	(acc("2301 - Long Term Bank Loan - SFB", "Liability", ""), "LOANS"),
	(acc("22040200002 - VAT Output A/C (Local) - SFB", "Liability", "Tax"), "TAX_BS"),
	(acc("13120100001 - Input VAT A/C (Local) - SFB", "Asset", "Tax"), "TAX_BS"),
	(acc("13110100001 - Prepaid Motor Insurance - SFB", "Asset", "Current Asset"), "PREPAID"),
	(acc("13190100001 - Temporary Opening - SFB", "Asset", "Temporary"), "PREPAID"),
	(acc("11010100002 - Fixed Assets - Furniture - SFB", "Asset", "Fixed Asset"), "FIXED_ASSETS"),
	(acc("Capital Work in Progress - SFB", "Asset", "Capital Work in Progress"), "FIXED_ASSETS"),
	(acc("Accumulated Depreciation - SFB", "Asset", "Accumulated Depreciation"), "ACC_DEP"),
	(acc("12010 - Long Term Investment - SFB", "Asset", ""), "INVESTMENTS"),
	(acc("31010100002 - Owner Current Account - SFB", "Equity", ""), "EQUITY"),
	(acc("Share Based Payment Reserve - SFB", "Equity", ""), "SHARE_RESERVE"),
	(acc("41010100001 - Sales Revenue - SFB", "Income", "Income Account"), "SALES"),
	(acc("41010101 - Discount Received On Purchase - SFB", "Income", ""), "OTHER_INCOME"),
	(acc("52010302 - Other Income - SFB", "Expense", ""), "OTHER_INCOME"),
	(acc("51010100001 - Cost Of Goods Sold - SFB", "Expense", "Cost of Goods Sold"), "COGS"),
	(acc("51010300013 - Customs Duty - SFB", "Expense", "Expenses Included In Valuation"), "VALUATION"),
	(acc("13090100005 - Stock Adjustment Account - SFB", "Expense", "Stock Adjustment"), "STOCK_ADJ"),
	(acc("52010700001 - Rent Expense - SFB", "Expense", "Expense Account"), "OPEX"),
	(acc("52010300018 - Round Off Account - SFB", "Expense", "Round Off"), "OPEX"),
	(acc("Maintenance Expenses-Vehicles - SFB", "Expense", "Indirect Expense"), "OPEX"),
	(acc("52010310 - Other Expenses - SFB", "Expense", ""), "OPEX"),
	(acc("52010305 - Interest On Bank Loan - SFB", "Expense", ""), "INTEREST_EXP"),
	(acc("Income Tax Expense - SFB", "Expense", ""), "TAX_EXP"),
	(acc("Depreciation - SFB", "Expense", "Depreciation"), "DEPRECIATION"),
	(acc("Amortisation Expense - SFB", "Expense", ""), "AMORTIZATION"),
	(acc("Loss On Disposal Of Asset - SFB", "Expense", ""), "DISPOSAL"),
	(acc("Share Based Compensation Expense - SFB", "Expense", ""), "SHARE_COMP"),
]
LINE_OF = {a.name: key for a, key in CHART}
CASH_ACCOUNTS = [a.name for a, key in CHART if key == cfs.CASH]


class TestWhichLineIsAnAccount(FrappeTestCase):
	def test_each_account_of_a_chart_like_steel_forces_lands_on_its_line(self):
		for account, expected in CHART:
			self.assertEqual(cfs.classify(account), expected, account.name)

	def test_an_override_beats_every_rule(self):
		pdc = "13020100027 - PDC Account - SFB"
		self.assertEqual(cfs.classify(acc(pdc, "Asset", "Bank"), {pdc: "PREPAID"}), "PREPAID")
		self.assertEqual(cfs.classify(acc("Odd - SFB", "Asset", ""), {"Odd - SFB": cfs.CASH}), cfs.CASH)

	def test_the_companys_own_disposal_and_depreciation_accounts_win_over_the_name(self):
		self.assertEqual(cfs.classify(acc("Gains - SFB", "Expense", ""), None, {"Gains - SFB": "DISPOSAL"}), "DISPOSAL")

	def test_an_account_no_rule_places_still_lands_somewhere_by_its_root(self):
		for root, line in (("Asset", "PREPAID"), ("Liability", "ACCRUED"), ("Equity", "EQUITY"), ("Expense", "OPEX"), ("Income", "OTHER_INCOME")):
			self.assertEqual(cfs.classify(acc("zzz", root, "")), line, root)
		self.assertEqual(cfs.classify(acc("zzz", "", "")), "ACCRUED")

	def test_the_number_in_front_of_a_name_is_not_read_as_part_of_it(self):
		self.assertEqual(cfs.classify(acc("2301 - Long Term Bank Loan - SFB", "Liability", "")), "LOANS")
		self.assertEqual(cfs.classify(acc("1234 Interest 5 - X", "Expense", "")), "INTEREST_EXP")

	def test_interest_income_is_income_and_interest_paid_is_expense(self):
		self.assertEqual(cfs.classify(acc("Interest Income - SFB", "Expense", "")), "OTHER_INCOME")
		self.assertEqual(cfs.classify(acc("Interest Income - SFB", "Income", "")), "OTHER_INCOME")
		self.assertEqual(cfs.classify(acc("Bank Interest Paid - SFB", "Expense", "")), "INTEREST_EXP")

	def test_a_foreign_exchange_gain_or_loss_account_is_an_expense_line_not_income(self):
		# it holds both: a net figure that is mostly a loss must not be shown as other income
		self.assertEqual(cfs.classify(acc("52010600002 - Foreign Exchange Gain/Loss - SFB", "Expense", "Expense Account")), "OPEX")

	def test_a_tax_account_is_a_tax_line_by_its_side(self):
		self.assertEqual(cfs.classify(acc("x", "Liability", "Tax")), "TAX_BS")
		self.assertEqual(cfs.classify(acc("x", "Asset", "Tax")), "TAX_BS")
		self.assertEqual(cfs.classify(acc("x", "Expense", "Tax")), "TAX_EXP")

	def test_every_line_the_rules_can_return_has_a_label(self):
		keys = set(cfs.ACCOUNT_TYPE_LINE.values()) | set(cfs.ROOT_DEFAULT.values())
		for table in cfs.KEYWORDS.values():
			keys |= {key for key, _pattern in table}
		self.assertTrue(keys <= set(cfs.LINES), keys - set(cfs.LINES))


class TestPeriods(FrappeTestCase):
	def test_total_is_one_period_of_the_whole_range(self):
		periods = cfs.period_list("2026-02-10", "2026-10-19", "Total")
		self.assertEqual([(p["from"], p["to"]) for p in periods], [(date(2026, 2, 10), date(2026, 10, 19))])

	def test_months_are_cut_on_month_ends_and_clipped_to_the_range(self):
		periods = cfs.period_list("2026-01-15", "2026-03-10", "Monthly")
		self.assertEqual([(p["from"], p["to"], p["label"]) for p in periods],
			[(date(2026, 1, 15), date(2026, 1, 31), "Jan 2026"), (date(2026, 2, 1), date(2026, 2, 28), "Feb 2026"),
				(date(2026, 3, 1), date(2026, 3, 10), "Mar 2026")])

	def test_quarters_and_years(self):
		q = cfs.period_list("2026-02-01", "2026-10-19", "Quarterly")
		self.assertEqual([(p["from"], p["to"], p["label"]) for p in q],
			[(date(2026, 2, 1), date(2026, 3, 31), "Q1 2026"), (date(2026, 4, 1), date(2026, 6, 30), "Q2 2026"),
				(date(2026, 7, 1), date(2026, 9, 30), "Q3 2026"), (date(2026, 10, 1), date(2026, 10, 19), "Q4 2026")])
		y = cfs.period_list("2025-11-01", "2026-10-19", "Yearly")
		self.assertEqual([(p["from"], p["to"], p["label"]) for p in y],
			[(date(2025, 11, 1), date(2025, 12, 31), "2025"), (date(2026, 1, 1), date(2026, 10, 19), "2026")])

	def test_periods_leave_no_gap_and_no_overlap(self):
		for periodicity in ("Monthly", "Quarterly", "Yearly"):
			periods = cfs.period_list("2024-03-17", "2026-10-19", periodicity)
			self.assertEqual(periods[0]["from"], date(2024, 3, 17))
			self.assertEqual(periods[-1]["to"], date(2026, 10, 19))
			for before, after in zip(periods, periods[1:]):
				self.assertEqual((after["from"] - before["to"]).days, 1, periodicity)

	def test_a_month_bucket_belongs_to_the_one_period_holding_it(self):
		periods = cfs.period_list("2026-01-15", "2026-06-30", "Quarterly")
		self.assertEqual([cfs.period_index(periods, m) for m in ("2026-01", "2026-03", "2026-04", "2026-06")], [0, 0, 1, 1])


def random_book(seed, vouchers=200, months=("2026-01", "2026-02", "2026-03", "2026-04")):
	"""Balanced vouchers over the chart; returns the flows as the ledger query gives them."""
	rng = random.Random(seed)
	names = list(LINE_OF)
	flows = {}
	for _ in range(vouchers):
		rows = rng.sample(names, rng.randint(2, 6))
		amounts = [round(rng.uniform(1, 5000), 3) * rng.choice((1, -1)) for _r in rows[:-1]]
		amounts.append(-sum(amounts))
		month = rng.choice(months)
		for name, net in zip(rows, amounts):
			flows[(name, month)] = flows.get((name, month), 0.0) + net
	return [(name, month, net) for (name, month), net in flows.items()]


class TestTheStatementTiesToTheLedger(FrappeTestCase):
	"""The property the report is built on."""

	def cash_movement(self, flows, month_keys):
		return [-sum(net for a, m, net in flows if a in CASH_ACCOUNTS and m in keys) for keys in month_keys]

	def test_both_methods_add_up_to_the_movement_of_cash_and_bank(self):
		months = ("2026-01", "2026-02", "2026-03", "2026-04")
		periods = cfs.period_list("2026-01-01", "2026-04-30", "Monthly")
		for seed in range(40):
			flows = random_book(seed, months=months)
			model = cfs.aggregate(flows, LINE_OF, periods)
			actual = self.cash_movement(flows, [(m,) for m in months])
			self.assertEqual([round(x, 6) for x in model["cash"]], [round(x, 6) for x in actual], seed)
			for method in (cfs.D, cfs.I):
				for i in range(len(periods)):
					self.assertAlmostEqual(cfs.statement_total(model, method, i), actual[i], 6, "%s seed %s period %s" % (method, seed, i))

	def test_the_two_methods_agree_section_by_section(self):
		periods = cfs.period_list("2026-01-01", "2026-04-30", "Total")
		for seed in range(20):
			model = cfs.aggregate(random_book(seed), LINE_OF, periods)
			totals = {}
			for method in (cfs.D, cfs.I):
				rows = cfs.Rows(periods, "BHD", "None", 1)
				totals[method] = cfs.statement_rows(model, method, rows, 100.0)["totals"]
			for key in ("INVESTING", "FINANCING", "OPERATING"):
				self.assertAlmostEqual(totals[cfs.D][key][0], totals[cfs.I][key][0], 6, (seed, key))

	def test_every_line_counts_once(self):
		# a P&L line added back in the indirect statement is also taken out of the line it was added back against
		for method in (cfs.D, cfs.I):
			weight = {key: 0 for key in cfs.LINES}
			for line in cfs.display_lines(method):
				for key, mult in line["members"]:
					weight[key] += mult
			self.assertEqual(weight, {key: 1 for key in cfs.LINES}, method)

	def test_hiding_zero_lines_changes_no_total(self):
		periods = cfs.period_list("2026-01-01", "2026-04-30", "Total")
		model = cfs.aggregate(random_book(3), LINE_OF, periods)
		shown, hidden = cfs.Rows(periods, "BHD", "None", 1), cfs.Rows(periods, "BHD", "None", 0)
		a = cfs.statement_rows(model, cfs.D, shown, 0.0)
		b = cfs.statement_rows(model, cfs.D, hidden, 0.0)
		self.assertEqual(a["grand"], b["grand"])
		self.assertLessEqual(len(hidden.data), len(shown.data))

	def test_a_cash_movement_the_lines_do_not_explain_shows_in_the_difference(self):
		periods = cfs.period_list("2026-01-01", "2026-04-30", "Total")
		model = cfs.aggregate(random_book(5), LINE_OF, periods)
		rows = cfs.Rows(periods, "BHD", "None", 1)
		result = cfs.statement_rows(model, cfs.D, rows, 50.0)
		self.assertAlmostEqual(result["difference"][0], 0.0, 6)
		model["cash"][0] += 12.5
		rows = cfs.Rows(periods, "BHD", "None", 1)
		result = cfs.statement_rows(model, cfs.D, rows, 50.0)
		self.assertAlmostEqual(result["difference"][0], -12.5, 6)

	def test_an_account_the_chart_no_longer_holds_is_not_dropped(self):
		periods = cfs.period_list("2026-01-01", "2026-04-30", "Total")
		flows = [("Deleted Account - SFB", "2026-01", 40.0), (CASH_ACCOUNTS[0], "2026-01", -40.0)]
		model = cfs.aggregate(flows, LINE_OF, periods)
		self.assertAlmostEqual(cfs.statement_total(model, cfs.D, 0), 40.0)
		self.assertAlmostEqual(model["cash"][0], 40.0)


class TestWorkedExample(FrappeTestCase):
	"""A small book with figures a person can check by hand."""

	FLOWS = [
		("41010100001 - Sales Revenue - SFB", "2026-01", 1000.0),
		("130302E0001 - Easy Touch - SFB", "2026-01", -300.0),
		("51010100001 - Cost Of Goods Sold - SFB", "2026-01", -600.0),
		("13090100001 - Trading Inventory - SFB", "2026-01", 100.0),
		("220104S0001 - Supplier - SFB", "2026-01", 50.0),
		("52010700001 - Rent Expense - SFB", "2026-01", -80.0),
		("Depreciation - SFB", "2026-01", -20.0),
		("Accumulated Depreciation - SFB", "2026-01", 20.0),
		("11010100002 - Fixed Assets - Furniture - SFB", "2026-01", -200.0),
		("23010000001 - KFH Vehicle Loan - SFB", "2026-01", 300.0),
		("13020100024 - NBB - SFB", "2026-01", -270.0),
	]

	def build(self, method):
		periods = cfs.period_list("2026-01-01", "2026-01-31", "Total")
		model = cfs.aggregate(self.FLOWS, LINE_OF, periods)
		rows = cfs.Rows(periods, "BHD", "None", 1)
		result = cfs.statement_rows(model, method, rows, 40.0)
		return {r["label"]: r.get("p0") for r in rows.data}, result

	def test_the_direct_lines_are_what_a_person_would_work_out(self):
		by_label, result = self.build(cfs.D)
		self.assertEqual(by_label["Sales revenue"], 1000.0)
		self.assertEqual(by_label["Change in accounts receivable"], -300.0)
		self.assertEqual(by_label["Cash from customers"], 700.0)
		self.assertEqual(by_label["Cost of goods sold"], -600.0)
		self.assertEqual(by_label["Change in inventory"], 100.0)
		self.assertEqual(by_label["Change in accounts payable"], 50.0)
		self.assertEqual(by_label["Cash to suppliers"], -450.0)
		self.assertEqual(by_label["Operating expenses"], -80.0)
		self.assertEqual(by_label["Depreciation, amortisation and other non-cash items (net)"], 0.0)
		self.assertEqual(by_label["Net cash from operating activities"], 170.0)
		self.assertEqual(by_label["Net cash from investing activities"], -200.0)
		self.assertEqual(by_label["Net cash from financing activities"], 300.0)
		self.assertEqual(by_label["Net increase / (decrease) in cash and bank"], 270.0)
		self.assertEqual(by_label["Cash and bank at the end of the period"], 310.0)

	def test_the_indirect_statement_reaches_the_same_operating_cash_by_another_road(self):
		by_label, result = self.build(cfs.I)
		self.assertEqual(by_label["Net income"], 300.0)
		self.assertEqual(by_label["Depreciation"], 20.0)
		self.assertEqual(by_label["Accounts receivable"], -300.0)
		self.assertEqual(by_label["Inventory (before write-downs)"], 100.0)
		self.assertEqual(by_label["Accounts payable"], 50.0)
		self.assertEqual(by_label["Net cash from operating activities"], 170.0)
		self.assertEqual(by_label["Net increase / (decrease) in cash and bank"], 270.0)

	def test_the_ledger_agrees(self):
		_by, result = self.build(cfs.D)
		self.assertAlmostEqual(result["difference"][0], 0.0)


class TestTheRowsAndTheirDrillDown(FrappeTestCase):
	def rows(self, show="Top 15 and others", zero=0, periodicity="Total"):
		periods = cfs.period_list("2026-01-01", "2026-04-30", periodicity)
		model = cfs.aggregate(random_book(11), LINE_OF, periods)
		rows = cfs.Rows(periods, "BHD", show, zero)
		cfs.statement_rows(model, cfs.D, rows, 0.0)
		return rows

	def test_every_row_has_a_number_an_indent_and_a_currency(self):
		rows = self.rows()
		self.assertEqual([r["rid"] for r in rows.data], list(range(1, len(rows.data) + 1)))
		for r in rows.data:
			self.assertIn("indent", r)
			self.assertEqual(r["currency"], "BHD")

	def test_a_line_names_its_accounts_and_a_total_names_its_lines(self):
		rows = self.rows()
		by_rid = {r["rid"]: r for r in rows.data}
		line = next(r for r in rows.data if r.get("is_line") and r["accounts"])
		self.assertTrue(all(isinstance(a, str) for a in line["accounts"]))
		total = next(r for r in rows.data if r.get("is_total") and r["label"].startswith("Net cash from operating"))
		self.assertTrue(total["lines"] and all(by_rid[rid].get("is_line") for rid in total["lines"]))

	def test_accounts_under_a_line_are_biggest_first_and_the_rest_are_pooled(self):
		rows = self.rows(show="Top 15 and others")
		for line in [r for r in rows.data if r.get("is_line")]:
			children = []
			for r in rows.data[line["rid"]:]:
				if r["indent"] <= line["indent"]:
					break
				children.append(r)
			named = [c for c in children if not c["label"].startswith("Other accounts")]
			sizes = [abs(c["p0"]) for c in named]
			self.assertEqual(sizes, sorted(sizes, reverse=True))
			self.assertLessEqual(len(named), cfs.TOP_ACCOUNTS)
			self.assertAlmostEqual(sum(c["p0"] for c in children), line["p0"], 6)

	def test_a_line_with_many_accounts_keeps_the_biggest_and_pools_the_rest_into_a_row_that_drills_into_them(self):
		customers = ["Customer %02d - SFB" % n for n in range(20)]
		line_of = dict(LINE_OF, **{c: "AR" for c in customers})
		flows = [(c, "2026-01", -(n + 1) * 10.0) for n, c in enumerate(customers)] + [(CASH_ACCOUNTS[0], "2026-01", sum((n + 1) * 10.0 for n in range(20)))]
		periods = cfs.period_list("2026-01-01", "2026-01-31", "Total")
		rows = cfs.Rows(periods, "BHD", "Top 15 and others", 0)
		cfs.statement_rows(cfs.aggregate(flows, line_of, periods), cfs.D, rows, 0.0)
		line = next(r for r in rows.data if r["label"] == "Change in accounts receivable")
		self.assertEqual(len(line["accounts"]), 20)
		children = [r for r in rows.data[line["rid"]:line["rid"] + 17] if r["indent"] > line["indent"]]
		self.assertEqual(len(children), 16)
		pooled = children[-1]
		self.assertEqual(pooled["label"], "Other accounts (5)")
		self.assertEqual(pooled["accounts"], sorted(customers[:5]))
		self.assertAlmostEqual(pooled["p0"], -(10 + 20 + 30 + 40 + 50.0), 6)
		self.assertAlmostEqual(sum(c["p0"] for c in children), line["p0"], 6)

	def test_none_shows_no_account_rows_and_all_shows_every_one(self):
		self.assertFalse([r for r in self.rows(show="None").data if r.get("is_account")])
		many = [r for r in self.rows(show="All").data if r.get("is_account")]
		self.assertTrue(many)
		self.assertFalse([r for r in many if r["label"].startswith("Other accounts")])

	def test_with_periods_each_row_has_a_column_per_period_and_a_total(self):
		rows = self.rows(periodicity="Monthly")
		first = rows.data[0]
		self.assertEqual(sorted(first["periods"]), ["p0", "p1", "p2", "p3", "total"])
		self.assertEqual(first["periods"]["total"], {"from_date": "2026-01-01", "to_date": "2026-04-30"})
		for r in rows.data:
			if "total" in r:
				self.assertAlmostEqual(sum(r["p%d" % i] for i in range(4)), r["total"], 5)

	def test_columns_follow_the_periods(self):
		single = cfs.columns(cfs.period_list("2026-01-01", "2026-04-30", "Total"), "BHD")
		self.assertEqual([c["fieldname"] for c in single], ["label", "p0", "currency"])
		many = cfs.columns(cfs.period_list("2026-01-01", "2026-04-30", "Monthly"), "BHD")
		self.assertEqual([c["fieldname"] for c in many], ["label", "p0", "p1", "p2", "p3", "total", "currency"])


class TestTheLedgerQueries(FrappeTestCase):
	def filters(self, **extra):
		return frappe._dict(company="Steel Force Trading WLL", from_date=date(2026, 1, 1), to_date=date(2026, 10, 19), **extra)

	def test_flows_leave_out_opening_entries_and_period_closing_vouchers_and_bind_every_value(self):
		with patch.object(cfs.frappe.db, "sql", MagicMock(return_value=[]), create=True) as sql:
			cfs.fetch_flows(self.filters(), " and gle.branch in %(branches)s", {"branches": ("SFSB",)})
		query, params = sql.call_args.args
		for fragment in ("is_cancelled = 0", "ifnull(gle.is_opening, 'No') != 'Yes'", "voucher_type != 'Period Closing Voucher'", "group by gle.account, month"):
			self.assertIn(fragment, query)
		self.assertIn("gle.branch in %(branches)s", query)
		self.assertEqual(params["company"], "Steel Force Trading WLL")
		self.assertNotIn("Steel Force", query)

	def test_the_opening_cash_takes_what_came_before_and_the_opening_entries_inside(self):
		with patch.object(cfs.frappe.db, "sql", MagicMock(return_value=[[3904.253]]), create=True) as sql:
			self.assertEqual(cfs.fetch_opening_cash(self.filters(), ["Cash"], "", {}), 3904.253)
		query, params = sql.call_args.args
		self.assertIn("gle.posting_date < %(from_date)s", query)
		self.assertIn("ifnull(gle.is_opening, 'No') = 'Yes'", query)
		self.assertEqual(params["cash"], ("Cash",))

	def test_no_cash_account_means_no_query(self):
		with patch.object(cfs.frappe.db, "sql", MagicMock(side_effect=AssertionError("no cash accounts")), create=True):
			self.assertEqual(cfs.fetch_opening_cash(self.filters(), [], "", {}), 0.0)
			self.assertEqual(cfs.fetch_closing_cash(self.filters(), [], "", {}), 0.0)

	def test_the_query_text_holds_placeholders_only(self):
		for query in (cfs.FLOWS, cfs.FLOWS_GROUP, cfs.OPENING, cfs.BALANCE):
			self.assertNotIn("{", query)
		for placeholder in ("%(company)s", "%(from_date)s", "%(to_date)s"):
			self.assertIn(placeholder, cfs.FLOWS)


class TestTheReport(FrappeTestCase):
	BASE = dict(company="Steel Force Trading WLL", from_date="2026-01-01", to_date="2026-04-30")

	def run_report(self, filters, flows=None, opening=0.0, closing=None):
		flows = random_book(2) if flows is None else flows
		actual_closing = opening - sum(n for a, m, n in flows if a in CASH_ACCOUNTS)
		patches = [
			patch.object(cfs, "load_accounts", return_value=(LINE_OF, {})),
			patch.object(cfs, "fetch_flows", return_value=flows),
			patch.object(cfs, "fetch_opening_cash", return_value=opening),
			patch.object(cfs, "fetch_closing_cash", return_value=closing if closing is not None else actual_closing),
			patch.object(cfs.money, "scope_params", return_value=("", {})),
			patch.object(cfs.frappe, "has_permission", MagicMock(return_value=True), create=True),
			patch.object(cfs.frappe, "get_cached_value", MagicMock(return_value="BHD"), create=True),
		]
		for p in patches:
			p.start()
		try:
			return cfs.execute(frappe._dict(filters))
		finally:
			for p in reversed(patches):
				p.stop()

	def test_execute_returns_columns_rows_message_chart_and_summary(self):
		columns, data, message, chart, summary = self.run_report(dict(self.BASE, periodicity="Monthly", method="Direct"))
		self.assertEqual(columns[0]["fieldname"], "label")
		self.assertTrue(data)
		self.assertIsNone(message)
		self.assertEqual(chart["type"], "bar")
		self.assertEqual([c["label"] for c in summary], ["Operating", "Investing", "Financing", "Net change in cash"])
		self.assertAlmostEqual(sum(c["value"] for c in summary[:3]), summary[3]["value"], 2)

	def test_a_single_period_has_no_chart(self):
		self.assertIsNone(self.run_report(dict(self.BASE))[3])

	def test_both_methods_give_the_same_totals(self):
		_c, direct, *_rest = self.run_report(dict(self.BASE, method="Direct"))
		_c, indirect, *_rest = self.run_report(dict(self.BASE, method="Indirect"))

		def pick(rows, label):
			return next(r["p0"] for r in rows if r["label"] == label)

		for label in ("Net cash from operating activities", "Net cash from investing activities", "Net cash from financing activities",
				"Net increase / (decrease) in cash and bank", "Cash and bank at the end of the period"):
			self.assertAlmostEqual(pick(direct, label), pick(indirect, label), 5, label)

	def test_the_message_says_so_when_the_statement_does_not_tie(self):
		flows = [("41010100001 - Sales Revenue - SFB", "2026-01", 100.0), (CASH_ACCOUNTS[0], "2026-01", -90.0)]
		_c, _d, message, *_rest = self.run_report(dict(self.BASE), flows=flows)
		self.assertIn("away from the ledger", message)

	def test_the_message_says_so_when_the_balance_read_on_its_own_differs(self):
		_c, _d, message, *_rest = self.run_report(dict(self.BASE), closing=12345.0)
		self.assertIn("read on its own", message)

	def test_a_bad_method_or_periodicity_or_dates_are_refused(self):
		for bad in (dict(self.BASE, method="Both"), dict(self.BASE, periodicity="Weekly"), dict(self.BASE, show_accounts="Some"),
				dict(self.BASE, from_date="2026-05-01"), dict(self.BASE, company="")):
			with self.assertRaises(frappe.ValidationError, msg=str(bad)):
				self.run_report(bad)


class TestWhatTheSystemShips(FrappeTestCase):
	def test_the_report_files_exist_and_the_report_is_registered_for_accountants(self):
		base = os.path.join(ROOT, "sf_trading", "report", "cash_flow_statement")
		for ext in ("py", "js", "json"):
			self.assertTrue(os.path.exists(os.path.join(base, "cash_flow_statement." + ext)))
		report = json.loads(read("sf_trading", "report", "cash_flow_statement", "cash_flow_statement.json"))
		self.assertEqual((report["name"], report["report_type"], report["ref_doctype"]), ("Cash Flow Statement", "Script Report", "GL Entry"))
		self.assertIn({"role": "Accounts Manager"}, report["roles"])

	def test_the_detail_report_files_exist_and_it_is_for_accountants(self):
		base = os.path.join(ROOT, "sf_trading", "report", "cash_flow_statement_detail")
		for ext in ("py", "js", "json"):
			self.assertTrue(os.path.exists(os.path.join(base, "cash_flow_statement_detail." + ext)))
		report = json.loads(read("sf_trading", "report", "cash_flow_statement_detail", "cash_flow_statement_detail.json"))
		self.assertEqual((report["name"], report["report_type"], report["ref_doctype"]), ("Cash Flow Statement Detail", "Script Report", "GL Entry"))
		self.assertIn({"role": "Accounts Manager"}, report["roles"])
		self.assertIn("Cash Flow Statement Detail", read("public", "js", "cash_flow_statement.js"))

	def test_the_drill_script_is_loaded_everywhere(self):
		self.assertIn("/assets/sf_trading/js/cash_flow_statement.js", read("hooks.py"))
		self.assertTrue(os.path.exists(os.path.join(ROOT, "public", "js", "cash_flow_statement.js")))

	def test_the_line_labels_offered_on_the_override_are_exactly_the_lines(self):
		child = json.loads(read("sf_trading", "doctype", "sf_cash_flow_account_map", "sf_cash_flow_account_map.json"))
		options = {f["fieldname"]: f for f in child["fields"]}["line"]["options"].split("\n")
		self.assertEqual(set(options), set(cfs.LABEL_TO_KEY))
		self.assertEqual(len(options), len(set(options)), "a label twice would make the override ambiguous")
		self.assertEqual(cfs.LABEL_TO_KEY[cfs.CASH_LABEL], cfs.CASH)

	def test_the_override_table_is_on_the_settings(self):
		settings = json.loads(read("sf_trading", "doctype", "sf_trading_settings", "sf_trading_settings.json"))
		fields = {f["fieldname"]: f for f in settings["fields"]}
		self.assertEqual((fields[cfs.MAP_FIELD]["fieldtype"], fields[cfs.MAP_FIELD]["options"]), ("Table", cfs.MAP_TABLE))
		self.assertIn(cfs.MAP_FIELD, settings["field_order"])
		self.assertEqual(json.loads(read("sf_trading", "doctype", "sf_cash_flow_account_map", "sf_cash_flow_account_map.json"))["istable"], 1)

	def test_the_labels_of_the_two_statements_are_all_plain_text(self):
		for method in (cfs.D, cfs.I):
			for line in cfs.display_lines(method):
				self.assertNotIn("<", line["label"])

	def test_the_settings_refuse_an_account_placed_twice(self):
		source = read("sf_trading", "doctype", "sf_trading_settings", "sf_trading_settings.py")
		self.assertIn("validate_cash_flow_account_map", source)


class TestTheOverrides(FrappeTestCase):
	def rows(self, mapping):
		return [frappe._dict(account=a, line=l) for a, l in mapping]

	def test_a_group_row_covers_what_is_under_it_and_a_nearer_row_wins(self):
		accounts = [frappe._dict(name="Leaf A", lft=3, rgt=4), frappe._dict(name="Leaf B", lft=6, rgt=7), frappe._dict(name="Elsewhere", lft=12, rgt=13)]
		bounds = {"Outer": frappe._dict(lft=1, rgt=10, company="Co"), "Inner": frappe._dict(lft=2, rgt=5, company="Co")}
		table = self.rows([("Outer", "Loans and borrowings"), ("Inner", "Provisions")])
		get_all = MagicMock(side_effect=lambda doctype, **k: table if doctype == cfs.MAP_TABLE else accounts)
		with patch.object(cfs.frappe.db, "table_exists", return_value=True, create=True), \
				patch.object(cfs.frappe, "get_all", get_all, create=True), \
				patch.object(cfs.frappe.db, "get_value", side_effect=lambda dt, name, fields, as_dict=False: bounds[name], create=True):
			self.assertEqual(cfs.line_overrides("Co"), {"Leaf A": "PROVISIONS", "Leaf B": "LOANS"})

	def test_no_table_no_overrides(self):
		with patch.object(cfs.frappe.db, "table_exists", return_value=False, create=True):
			self.assertEqual(cfs.line_overrides("Co"), {})

	def test_a_row_for_another_companys_account_is_ignored(self):
		table = self.rows([("Foreign", "Loans and borrowings")])
		with patch.object(cfs.frappe.db, "table_exists", return_value=True, create=True), \
				patch.object(cfs.frappe, "get_all", MagicMock(return_value=table), create=True), \
				patch.object(cfs.frappe.db, "get_value", return_value=frappe._dict(lft=1, rgt=2, company="Other Co"), create=True):
			self.assertEqual(cfs.line_overrides("Co"), {})


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
const cfs = sandbox.sf_trading.cash_flow_statement;
const data = JSON.parse(process.argv[3]);
const filters = JSON.parse(process.argv[4]);
const report = { data, get_filter_values: () => filters };
const out = {};
const open = (rid, col) => { sandbox.routed = null; sandbox.alert = null; sandbox.frappe.route_options = null; cfs.open_detail(report, rid, col);
	return { route: sandbox.routed, options: sandbox.frappe.route_options, alert: sandbox.alert }; };
out.line = open(2, "p1");
out.account = open(3, "p0");
out.total = open(4, "total");
out.header = open(1, "p0");
const passthrough = (v) => String(v);
out.fmt = {
	line: cfs.formatter(5, 0, { fieldname: "p0" }, data[1], passthrough),
	negative: cfs.formatter(-5, 0, { fieldname: "p0" }, { ...data[1], p0: -5 }, passthrough),
	header: cfs.formatter(5, 0, { fieldname: "p0" }, data[0], passthrough),
	difference_ok: cfs.formatter(0, 0, { fieldname: "p0" }, { is_difference: 1, p0: 0, rid: 8 }, passthrough),
	difference_bad: cfs.formatter(3, 0, { fieldname: "p0" }, { is_difference: 1, p0: 3, rid: 8 }, passthrough),
	label: cfs.formatter("Cash", 0, { fieldname: "label" }, { is_total: 1, rid: 4, drill_line: "T" }, passthrough),
};
out.detail = {
	voucher: cfs.detail_formatter("x", 0, { fieldname: "label" }, { is_voucher: 1, voucher_type: "Sales Invoice", voucher_no: "SINV-1" }, passthrough),
	negative: cfs.detail_formatter("-4", 0, { fieldname: "amount" }, { amount: -4 }, passthrough),
	pooled: cfs.detail_formatter("rest", 0, { fieldname: "label" }, { is_pooled: 1 }, passthrough),
	group: cfs.detail_formatter("Sales Invoice", 0, { fieldname: "label" }, { is_group: 1 }, passthrough),
	statement: cfs.statement_route(filters),
	ledger: cfs.ledger_route(filters, ["A", "B"]),
};
console.log(JSON.stringify(out));
"""

JS_DATA = [
	{"rid": 1, "label": "A. Cash flows", "is_header": 1, "periods": {"p0": {"from_date": "2026-01-01", "to_date": "2026-01-31"}, "p1": {"from_date": "2026-02-01", "to_date": "2026-04-30"}, "total": {"from_date": "2026-01-01", "to_date": "2026-04-30"}}},
	{"rid": 2, "label": "Change in accounts receivable", "is_line": 1, "accounts": ["A", "B"], "drill_line": "Change in accounts receivable", "p0": 1},
	{"rid": 3, "label": "Customer One", "is_account": 1, "accounts": ["Customer One - SFB"], "drill_line": "Change in accounts receivable", "p0": 2},
	{"rid": 4, "label": "Net cash from operating activities", "is_total": 1, "drill_line": "Net cash from operating activities"},
]

JS_FILTERS = {"company": "Co", "from_date": "2026-01-01", "to_date": "2026-04-30", "method": "Indirect", "cost_center": "SFSB - SFB", "branch": ["SFSB"]}


class TestTheDrillDown(FrappeTestCase):
	def run_js(self, filters=None):
		node = shutil.which("node")
		if not node:
			self.skipTest("node is not installed")
		done = subprocess.run([node, "-e", JS_RUNNER, "x", os.path.join(ROOT, "public", "js", "cash_flow_statement.js"), json.dumps(JS_DATA),
			json.dumps(filters or JS_FILTERS)], capture_output=True, text=True, encoding="utf-8", timeout=60)
		self.assertEqual(done.returncode, 0, done.stderr)
		return json.loads(done.stdout)

	def test_a_line_opens_the_detail_for_its_column_dates_and_every_filter_of_the_report(self):
		out = self.run_js()["line"]
		self.assertEqual(out["route"], ["query-report", "Cash Flow Statement Detail"])
		options = out["options"]
		self.assertEqual((options["company"], options["method"], options["line"]), ("Co", "Indirect", "Change in accounts receivable"))
		self.assertEqual((options["from_date"], options["to_date"]), ("2026-02-01", "2026-04-30"), "the column clicked, not the whole report")
		self.assertEqual((options["cost_center"], options["branch"]), ("SFSB - SFB", ["SFSB"]))
		self.assertEqual(options["group_by"], "Voucher Type")
		self.assertNotIn("account", options)

	def test_an_account_row_opens_its_vouchers_for_that_account_only(self):
		options = self.run_js()["account"]["options"]
		self.assertEqual(options["account"], ["Customer One - SFB"])
		self.assertEqual(options["group_by"], "Vouchers")
		self.assertEqual(options["line"], "Change in accounts receivable", "the line gives the account its weight")

	def test_a_total_opens_the_whole_figure_for_the_whole_range(self):
		options = self.run_js()["total"]["options"]
		self.assertEqual(options["line"], "Net cash from operating activities")
		self.assertEqual((options["from_date"], options["to_date"]), ("2026-01-01", "2026-04-30"))

	def test_a_figure_with_no_line_behind_it_says_so_instead_of_opening_an_empty_report(self):
		out = self.run_js()["header"]
		self.assertIsNone(out["route"])
		self.assertIn("Nothing to open", out["alert"]["message"])

	def test_filters_that_are_not_set_are_not_sent(self):
		options = self.run_js({"company": "Co", "from_date": "2026-01-01", "to_date": "2026-04-30", "method": "Direct", "branch": []})["line"]["options"]
		self.assertNotIn("cost_center", options)
		self.assertNotIn("branch", options)

	def test_amounts_are_links_and_headers_and_a_clean_difference_are_not(self):
		fmt = self.run_js()["fmt"]
		self.assertIn('class="sf-cfs-drill"', fmt["line"])
		self.assertIn('data-col="p0"', fmt["line"])
		self.assertIn("#c62828", fmt["negative"])
		self.assertEqual(fmt["header"], "")
		self.assertIn("#2e7d32", fmt["difference_ok"])
		self.assertIn("#c62828", fmt["difference_bad"])
		self.assertNotIn("sf-cfs-drill", fmt["difference_bad"])
		self.assertEqual(fmt["label"], "<b>Cash</b>")

	def test_in_the_detail_a_vouchers_number_opens_the_voucher(self):
		detail = self.run_js()["detail"]
		self.assertEqual(detail["voucher"], '<a href="/app/Sales Invoice/SINV-1">SINV-1</a>')
		self.assertIn("#c62828", detail["negative"])
		self.assertEqual(detail["pooled"], "<i>rest</i>")
		self.assertEqual(detail["group"], "<b>Sales Invoice</b>")

	def test_the_detail_leads_back_to_the_statement_and_on_to_the_general_ledger(self):
		detail = self.run_js()["detail"]
		self.assertEqual(detail["statement"], {"company": "Co", "from_date": "2026-01-01", "to_date": "2026-04-30", "method": "Indirect",
			"cost_center": "SFSB - SFB", "branch": ["SFSB"]})
		self.assertEqual(detail["ledger"]["account"], ["A", "B"])
		self.assertEqual(detail["ledger"]["cost_center"], ["SFSB - SFB"])
		self.assertEqual(detail["ledger"]["group_by"], "Group by Voucher (Consolidated)")
