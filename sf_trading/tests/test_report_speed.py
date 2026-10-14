"""Faster Balance Sheet, Profit and Loss and Cash Flow (sf_trading/report_speed.py).

    bench --site <site> run-tests --module sf_trading.tests.test_report_speed

Most tests need no database (the SQL is captured, the ledger is a list). The last class runs the real reports twice,
with the faster reads off and on, and demands the same answer; it needs a site on the erpnext release the copies were
written against and is skipped anywhere else.
"""

import datetime
import json
import random
import unittest
from decimal import Decimal
from unittest.mock import MagicMock, patch

import frappe
from frappe.tests.utils import FrappeTestCase

from sf_trading import report_speed

try:
	from erpnext.accounts.report import financial_statements
	from erpnext.accounts.report.cash_flow import cash_flow
except ImportError:  # pragma: no cover - a bench without erpnext
	financial_statements = cash_flow = None

D = datetime.date


def reset_local():
	for name in ("sf_fast_statements", "sf_cashflow_days"):
		if hasattr(frappe.local, name):
			delattr(frappe.local, name)


class TestFingerprint(FrappeTestCase):
	def test_indentation_and_trailing_spaces_do_not_change_it(self):
		def flat(a, b):
			total = a + b
			return total

		class Holder:
			def flat(a, b):
				total = a + b
				return total

		self.assertEqual(report_speed.fingerprint(flat), report_speed.fingerprint(Holder.flat))

	def test_other_code_is_another_fingerprint(self):
		def one():
			return 1

		def two():
			return 2

		self.assertNotEqual(report_speed.fingerprint(one), report_speed.fingerprint(two))

	def test_unreadable_source_is_empty(self):
		self.assertEqual(report_speed.fingerprint(len), "")

	def test_the_known_releases_are_sha1_digests(self):
		for name, digests in report_speed.STOCK_FINGERPRINTS.items():
			for digest in digests:
				self.assertRegex(digest, r"^[0-9a-f]{40}$", name)


class TestIsEnabled(FrappeTestCase):
	def setUp(self):
		reset_local()
		self.addCleanup(reset_local)

	def test_needs_the_app_on_this_site_and_the_switch(self):
		for installed, switch, expected in (
			(["frappe", "sf_trading"], True, True),
			(["frappe", "sf_trading"], False, False),
			(["frappe", "erpnext"], True, False),
		):
			reset_local()
			with patch.object(report_speed.frappe, "get_installed_apps", return_value=installed), patch.object(
				report_speed.switches, "is_on", return_value=switch
			):
				self.assertEqual(report_speed.is_enabled(), expected, (installed, switch))

	def test_a_site_without_the_app_does_not_even_read_the_switch(self):
		with patch.object(report_speed.frappe, "get_installed_apps", return_value=["frappe"]), patch.object(
			report_speed.switches, "is_on"
		) as is_on:
			self.assertFalse(report_speed.is_enabled())
		is_on.assert_not_called()

	def test_asked_once_per_request(self):
		with patch.object(report_speed.frappe, "get_installed_apps", return_value=["sf_trading"]) as apps, patch.object(
			report_speed.switches, "is_on", return_value=True
		) as is_on:
			for _ in range(5):
				report_speed.is_enabled()
		self.assertEqual((apps.call_count, is_on.call_count), (1, 1))

	def test_the_switch_is_the_settings_field_that_exists(self):
		with open(frappe.get_app_path("sf_trading", "sf_trading", "doctype", "sf_trading_settings", "sf_trading_settings.json")) as f:
			meta = json.load(f)
		field = next(x for x in meta["fields"] if x["fieldname"] == report_speed.SWITCH)
		self.assertEqual((field["fieldtype"], field.get("default")), ("Check", "1"))
		self.assertIn(report_speed.SWITCH, meta["field_order"])


class TestAccountsUnder(FrappeTestCase):
	def asked(self, *args):
		with patch.object(report_speed.frappe.db, "sql_list", return_value=["A - X"]) as sql_list:
			self.assertEqual(report_speed.accounts_under(*args), ["A - X"])
		return sql_list.call_args.args

	def test_leaf_accounts_inside_the_root_of_the_type(self):
		sql, args = self.asked(10, 20, "Asset")
		self.assertIn("is_group = 0", sql)
		self.assertIn("lft >= %s and rgt <= %s", sql)
		self.assertIn("root_type = %s", sql)
		self.assertEqual(args, [10, 20, "Asset"])

	def test_each_condition_only_when_given(self):
		sql, args = self.asked(None, None, "Income")
		self.assertNotIn("lft", sql)
		self.assertEqual(args, ["Income"])
		sql, args = self.asked(10, 20, None)
		self.assertNotIn("root_type", sql)
		self.assertEqual(args, [10, 20])
		sql, args = self.asked(10, None, None)  # a range needs both ends, as in ERPNext's own test
		self.assertNotIn("lft", sql)
		self.assertEqual(args, [])

	def test_the_query_is_parameterised(self):
		sql, args = self.asked(10, 20, "Asset'; drop table x; --")
		self.assertNotIn("drop", sql)
		self.assertEqual(args[-1], "Asset'; drop table x; --")


@unittest.skipIf(financial_statements is None, "erpnext is not installed")
class TestFastAccountingEntries(FrappeTestCase):
	"""The statement it builds: the stock one with the hint and the EXISTS test gone, everything else kept."""

	def build(self, doctype="GL Entry", accounts=("Cash - T", "Bank - T"), **kwargs):
		filters = frappe._dict(company="Test Co")
		captured = {}

		def sql(query, params=None, **kw):
			captured.update(query=query, params=params, kw=kw)
			return ["row"]

		with patch.object(report_speed.frappe.db, "sql", sql), patch.object(
			report_speed.frappe.db, "get_single_value", return_value=0
		), patch.object(report_speed, "accounts_under", return_value=list(accounts)), patch.object(
			financial_statements, "apply_additional_conditions", lambda doctype, query, *a, **k: query
		), patch("frappe.desk.reportview.build_match_conditions", return_value=""):
			result = report_speed.fast_get_accounting_entries(doctype, "2026-01-01", "2026-12-31", filters, **kwargs)
		self.assertEqual(result, ["row"])
		return captured

	def test_no_forced_index_and_no_exists_test(self):
		got = self.build(root_lft=1, root_rgt=2, root_type="Asset")
		sql = got["query"].upper()
		self.assertNotIn("FORCE INDEX", sql)
		self.assertNotIn("EXISTS", sql)
		self.assertNotIn("TABACCOUNT", sql)

	def test_the_roots_accounts_are_named(self):
		got = self.build(root_lft=1, root_rgt=2, root_type="Asset")
		self.assertIn("`account` IN (", got["query"])
		self.assertTrue({"Cash - T", "Bank - T"} <= set(got["params"].values()))
		self.assertTrue(got["kw"].get("as_dict"))

	def test_what_the_stock_query_keeps_it_keeps(self):
		got = self.build(root_type="Asset")
		sql = got["query"]
		self.assertIn("`is_cancelled`=0", sql)
		self.assertIn("`posting_date`<=", sql)
		self.assertIn("`company`=", sql)
		for column in ("debit_in_account_currency", "credit_in_account_currency", "account_currency", "is_opening", "fiscal_year"):
			self.assertIn(column, sql)

	def test_no_root_no_account_condition(self):
		self.assertNotIn("`account` IN (", self.build()["query"])

	def test_a_root_without_accounts_matches_nothing(self):
		got = self.build(root_type="Equity", accounts=())
		self.assertIn("`account` IN (", got["query"])
		self.assertIn("", got["params"].values())  # an account that no ledger row has, never an empty IN ()

	def test_closing_balances_are_read_the_same_way(self):
		got = self.build("Account Closing Balance", root_type="Asset", period_closing_voucher="PCV-1")
		sql = got["query"]
		self.assertIn("`closing_date` `posting_date`", sql)
		self.assertIn("`period_closing_voucher`=", sql)
		self.assertNotIn("FORCE INDEX", sql.upper())

	def test_grouped_by_account(self):
		got = self.build(root_type="Asset", group_by_account=True)
		self.assertTrue(got["query"].rstrip().endswith("GROUP BY `account`"))
		self.assertIn("SUM(`debit`)", got["query"])


@unittest.skipIf(financial_statements is None, "erpnext is not installed")
class TestTheDailyRead(FrappeTestCase):
	"""Cash Flow's per-period sums from one daily read."""

	LEDGER = [(D(2026, 1, 5), "100.500000000"), (D(2026, 1, 20), "-30.250000000"), (D(2026, 2, 3), "10.000000000")]

	def setUp(self):
		reset_local()
		self.addCleanup(reset_local)
		self.stock = MagicMock(name="stock", return_value=-1)
		stocked = patch.dict(report_speed.STOCK, {"get_account_type_based_gl_data": self.stock})
		stocked.start()
		self.addCleanup(stocked.stop)

	def window(self, start, end, rows=None, **filters):
		base = {"start_date": start, "end_date": end, "account_type": "Bank", "include_default_book_entries": 1, "finance_book": None}
		base.update(filters)
		with patch.object(report_speed.frappe.db, "sql", return_value=self.LEDGER if rows is None else rows) as sql, patch.object(
			report_speed.frappe, "get_cached_value", return_value=None
		):
			value = report_speed.fast_get_account_type_based_gl_data("Test Co", base)
		return value, sql

	def test_a_period_is_the_sum_of_its_days(self):
		self.assertEqual(self.window(D(2026, 1, 1), D(2026, 1, 31))[0], 70.25)
		self.assertEqual(self.window(D(2026, 2, 1), D(2026, 2, 28))[0], 10.0)
		self.assertEqual(self.window(D(2026, 1, 1), D(2026, 2, 28))[0], 80.25)

	def test_both_ends_of_the_window_are_inside_it(self):
		self.assertEqual(self.window(D(2026, 1, 5), D(2026, 1, 5))[0], 100.5)
		self.assertEqual(self.window(D(2026, 1, 6), D(2026, 1, 19))[0], 0)

	def test_no_rows_and_a_sum_of_zero_are_the_int_zero_the_stock_function_returns(self):
		for rows in ([], [(D(2026, 1, 5), "10.000000000"), (D(2026, 1, 6), "-10.000000000")]):
			reset_local()
			value, _sql = self.window(D(2026, 1, 1), D(2026, 1, 31), rows=rows)
			self.assertEqual(value, 0)
			self.assertIsInstance(value, int)

	def test_strings_and_dates_are_both_windows(self):
		self.assertEqual(self.window("2026-01-01", "2026-01-31")[0], 70.25)

	def test_one_read_answers_every_period_of_an_account_type(self):
		reads = 0
		with patch.object(report_speed.frappe.db, "sql", return_value=self.LEDGER) as sql, patch.object(
			report_speed.frappe, "get_cached_value", return_value=None
		):
			for month in range(1, 13):
				report_speed.fast_get_account_type_based_gl_data(
					"Test Co",
					{"start_date": D(2026, month, 1), "end_date": D(2026, month, 28), "account_type": "Bank", "include_default_book_entries": 1},
				)
			reads = sql.call_count
		self.assertEqual(reads, 1)

	def test_a_different_account_type_company_book_or_cost_centre_is_a_different_read(self):
		with patch.object(report_speed.frappe.db, "sql", return_value=self.LEDGER) as sql, patch.object(
			report_speed.frappe, "get_cached_value", return_value="Statutory"
		), patch.object(financial_statements, "get_cost_centers_with_children", return_value=["CC-1", "CC-2"]):
			base = {"start_date": D(2026, 1, 1), "end_date": D(2026, 1, 31), "account_type": "Bank", "include_default_book_entries": 1}
			report_speed.fast_get_account_type_based_gl_data("Test Co", dict(base))
			report_speed.fast_get_account_type_based_gl_data("Test Co", dict(base))
			self.assertEqual(sql.call_count, 1)
			report_speed.fast_get_account_type_based_gl_data("Test Co", dict(base, account_type="Cash"))
			report_speed.fast_get_account_type_based_gl_data("Other Co", dict(base))
			report_speed.fast_get_account_type_based_gl_data("Test Co", dict(base, finance_book="Tax"))
			report_speed.fast_get_account_type_based_gl_data("Test Co", dict(base, cost_center=["CC"]))
			self.assertEqual(sql.call_count, 5)
			query, params = sql.call_args.args
		self.assertIn("cost_center in %(cost_center)s", query)
		self.assertEqual(sorted(params["cost_center"]), ["CC-1", "CC-2"])

	def test_the_finance_book_condition_of_the_stock_query(self):
		_value, sql = self.window(D(2026, 1, 1), D(2026, 1, 31), finance_book="Tax")
		query, params = sql.call_args.args
		self.assertIn("finance_book in (%(book)s, %(company_book)s, '') or finance_book is null", query)
		self.assertEqual((params["book"], params["company_book"]), ("Tax", ""))
		reset_local()
		_value, sql = self.window(D(2026, 1, 1), D(2026, 1, 31), include_default_book_entries=0)
		query, params = sql.call_args.args
		self.assertIn("finance_book in (%(book)s, '') or finance_book is null", query)
		self.assertNotIn("company_book", params)

	def test_the_read_is_the_whole_ledger_of_the_type_grouped_by_day(self):
		_value, sql = self.window(D(2026, 1, 1), D(2026, 1, 31))
		query, params = sql.call_args.args
		for part in ("tabGL Entry", "group by posting_date", "voucher_type != 'Period Closing Voucher'", "account_type = %(account_type)s", "company = %(company)s"):
			self.assertIn(part, query)
		self.assertNotIn("start_date", query)  # the dates are applied to the days, not to the read
		self.assertEqual((params["company"], params["account_type"]), ("Test Co", "Bank"))

	def test_no_dates_goes_to_the_stock_function(self):
		for start, end in ((None, D(2026, 1, 31)), (D(2026, 1, 1), None), ("", "")):
			self.stock.reset_mock()
			self.assertEqual(report_speed.fast_get_account_type_based_gl_data("Test Co", {"start_date": start, "end_date": end}), -1)
			self.stock.assert_called_once()

	def test_the_sums_are_exactly_what_a_database_sum_would_give(self):
		# the day-by-day addition is in decimals: the window total is the one number the database would return
		rng = random.Random(7)
		days = [D(2026, 1, 1) + datetime.timedelta(days=i) for i in range(0, 300, 1) if rng.random() < 0.6]
		ledger = [(day, "%.9f" % (rng.randint(-10**9, 10**9) / 1000.0)) for day in days]
		for _ in range(60):
			a, b = sorted(rng.sample(range(0, 300), 2))
			start, end = D(2026, 1, 1) + datetime.timedelta(days=a), D(2026, 1, 1) + datetime.timedelta(days=b)
			expected = sum((Decimal(v) for d, v in ledger if start <= d <= end), Decimal(0))
			reset_local()
			value, _sql = self.window(start, end, rows=ledger)
			self.assertEqual(value, float(expected) if expected else 0, (start, end))


class TestDispatch(FrappeTestCase):
	def setUp(self):
		reset_local()
		self.addCleanup(reset_local)
		self.stock = {name: MagicMock(name=name, return_value="stock") for name in report_speed.STOCK_FINGERPRINTS}
		stocked = patch.dict(report_speed.STOCK, self.stock)
		stocked.start()
		self.addCleanup(stocked.stop)

	def test_on_it_is_the_faster_read_and_off_it_is_erpnexts_own(self):
		calls = (
			("get_accounting_entries", "fast_get_accounting_entries", (("GL Entry", "2026-01-01"), {"root_type": "Asset"})),
			("get_account_type_based_gl_data", "fast_get_account_type_based_gl_data", (("Test Co", {"account_type": "Bank"}), {})),
		)
		for enabled in (True, False):
			for name, fast, (args, kwargs) in calls:
				self.stock[name].reset_mock()
				with patch.object(report_speed, "is_enabled", return_value=enabled), patch.object(report_speed, fast, return_value="fast") as faster:
					got = getattr(report_speed, name)(*args, **kwargs)
				self.assertEqual(got, "fast" if enabled else "stock", (name, enabled))
				self.assertEqual(faster.called, enabled)
				self.assertEqual(self.stock[name].called, not enabled)
				if not enabled:
					self.stock[name].assert_called_once_with(*args, **kwargs)
				else:
					faster.assert_called_once_with(*args, **kwargs)


@unittest.skipIf(financial_statements is None, "erpnext is not installed")
class TestInstall(FrappeTestCase):
	def setUp(self):
		self.original = {
			(financial_statements, "get_accounting_entries"): financial_statements.get_accounting_entries,
			(cash_flow, "get_account_type_based_gl_data"): cash_flow.get_account_type_based_gl_data,
		}
		report_speed._STATE["tried"] = False

		def undo():
			for (module, name), function in self.original.items():
				setattr(module, name, function)
			report_speed._STATE["tried"] = False
			report_speed.STOCK.clear()

		self.addCleanup(undo)

	def known(self):
		return {
			"get_accounting_entries": {report_speed.fingerprint(self.original[(financial_statements, "get_accounting_entries")])},
			"get_account_type_based_gl_data": {report_speed.fingerprint(self.original[(cash_flow, "get_account_type_based_gl_data")])},
		}

	def test_a_release_it_knows_is_replaced_and_kept_for_the_switch(self):
		with patch.object(report_speed, "STOCK_FINGERPRINTS", self.known()):
			report_speed.install()
		self.assertIs(financial_statements.get_accounting_entries, report_speed.get_accounting_entries)
		self.assertIs(cash_flow.get_account_type_based_gl_data, report_speed.get_account_type_based_gl_data)
		self.assertIs(report_speed.STOCK["get_accounting_entries"], self.original[(financial_statements, "get_accounting_entries")])

	def test_another_release_is_left_alone_and_named_in_the_log(self):
		with patch.object(report_speed, "STOCK_FINGERPRINTS", {k: {"0" * 40} for k in report_speed.STOCK_FINGERPRINTS}), patch.object(
			report_speed.frappe, "logger"
		) as logger:
			report_speed.install()
		self.assertIs(financial_statements.get_accounting_entries, self.original[(financial_statements, "get_accounting_entries")])
		self.assertIs(cash_flow.get_account_type_based_gl_data, self.original[(cash_flow, "get_account_type_based_gl_data")])
		self.assertEqual(logger.return_value.warning.call_count, 2)
		self.assertEqual(report_speed.STOCK, {})

	def test_only_the_function_that_matches_is_replaced(self):
		known = self.known()
		known["get_account_type_based_gl_data"] = {"0" * 40}
		with patch.object(report_speed, "STOCK_FINGERPRINTS", known), patch.object(report_speed.frappe, "logger"):
			report_speed.install()
		self.assertIs(financial_statements.get_accounting_entries, report_speed.get_accounting_entries)
		self.assertIs(cash_flow.get_account_type_based_gl_data, self.original[(cash_flow, "get_account_type_based_gl_data")])

	def test_once_per_process(self):
		with patch.object(report_speed, "STOCK_FINGERPRINTS", self.known()):
			report_speed.install()
			with patch.object(report_speed, "fingerprint") as fingerprint:
				report_speed.install()
				report_speed.install()
		fingerprint.assert_not_called()

	def test_it_is_on_both_request_and_job_hooks(self):
		from sf_trading import hooks

		self.assertIn("sf_trading.report_speed.install", hooks.before_request)
		self.assertIn("sf_trading.report_speed.install", hooks.before_job)


@unittest.skipIf(financial_statements is None, "erpnext is not installed")
class TestSameFiguresOnRealData(FrappeTestCase):
	"""The reports themselves, faster reads off and on: the answer must not move by a digit."""

	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		stock = {
			"get_accounting_entries": financial_statements.get_accounting_entries,
			"get_account_type_based_gl_data": cash_flow.get_account_type_based_gl_data,
		}
		known = all(
			report_speed.fingerprint(function) in report_speed.STOCK_FINGERPRINTS[name] for name, function in stock.items()
		)
		cls.runnable = known and not isinstance(frappe.local.db, MagicMock) and bool(frappe.db.count("Fiscal Year"))
		cls.stock = stock

	@classmethod
	def tearDownClass(cls):
		# leave erpnext's modules as this class found them
		financial_statements.get_accounting_entries = cls.stock["get_accounting_entries"]
		cash_flow.get_account_type_based_gl_data = cls.stock["get_account_type_based_gl_data"]
		report_speed._STATE["tried"] = False
		report_speed.STOCK.clear()
		super().tearDownClass()

	def setUp(self):
		if not self.runnable:
			self.skipTest("needs a site on the erpnext release this was written for, with ledger data")
		reset_local()
		self.addCleanup(reset_local)

	def answer(self, report, filters, enabled):
		from frappe.desk.query_report import run

		reset_local()
		frappe.local.sf_fast_statements = enabled
		report_speed.install()
		result = run(report, filters=dict(filters), ignore_prepared_report=True)
		return json.dumps([result.get("result"), result.get("columns"), result.get("chart"), result.get("report_summary")], sort_keys=True, default=str)

	def test_statements_are_identical_with_the_switch_on_and_off(self):
		company = frappe.db.get_value("GL Entry", {"is_cancelled": 0}, "company")
		year = frappe.db.get_value("Fiscal Year", {}, "name", order_by="year_start_date desc")
		if not (company and year):
			self.skipTest("no ledger data")
		base = {"company": company, "filter_based_on": "Fiscal Year", "from_fiscal_year": year, "to_fiscal_year": year, "accumulated_values": 0, "include_default_book_entries": 1}
		for report in ("Balance Sheet", "Profit and Loss Statement", "Cash Flow"):
			for periodicity in ("Yearly", "Monthly"):
				filters = dict(base, periodicity=periodicity)
				with self.subTest(report=report, periodicity=periodicity):
					self.assertEqual(self.answer(report, filters, True), self.answer(report, filters, False))
