# sf_trading/tests/test_entries_by_user.py
"""The Entries by User report: how many vouchers each user made, by voucher type
(sf_trading/report/entries_by_user).

    bench --site <site> run-tests --module sf_trading.tests.test_entries_by_user

The builders take plain counts and the data seams are patched, so nothing is created or read.
"""

import json
import os
from unittest.mock import MagicMock, patch

import frappe
from frappe.tests.utils import FrappeTestCase

from sf_trading.sf_trading.report.entries_by_user import entries_by_user as report

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
NAMES = {"a@example.com": "Aaa", "b@example.com": "Bbb"}


def read(*parts):
	with open(os.path.join(ROOT, *parts), encoding="utf-8") as f:
		return f.read()


COUNTS = {
	"Sales Invoice": [("a@example.com", 1, 10), ("a@example.com", 0, 2), ("b@example.com", 1, 4)],
	"Payment Entry": [("a@example.com", 1, 5), ("b@example.com", 1, 9), ("b@example.com", 2, 1)],
	"Journal Entry": [("b@example.com", 1, 3)],
}


class TestTheCounts(FrappeTestCase):
	def test_counts_are_grouped_by_user_and_voucher_type_with_the_three_docstatuses_apart(self):
		agg = report.aggregate(COUNTS)
		self.assertEqual(agg[("a@example.com", "Sales Invoice")], [2, 10, 0])
		self.assertEqual(agg[("b@example.com", "Payment Entry")], [0, 9, 1])
		self.assertEqual(report.user_totals(agg), {"a@example.com": 17, "b@example.com": 17})

	def test_an_unknown_docstatus_is_ignored(self):
		self.assertEqual(report.aggregate({"Sales Invoice": [("a@example.com", 7, 3)]}), {})

	def test_the_same_user_and_type_from_two_rows_add_up(self):
		agg = report.aggregate({"Sales Invoice": [("a@example.com", 1, 3), ("a@example.com", 1, 4)]})
		self.assertEqual(agg[("a@example.com", "Sales Invoice")], [0, 7, 0])


class TestTheDetailView(FrappeTestCase):
	def rows(self, counts=COUNTS):
		return report.detail_rows(report.aggregate(counts), NAMES)

	def test_the_busiest_user_leads_and_each_users_biggest_type_comes_first(self):
		counts = dict(COUNTS)
		counts["Journal Entry"] = [("b@example.com", 1, 8)]  # b: 4 + 9 + 1 + 8 = 22 against a's 17
		rows = self.rows(counts)
		self.assertEqual([(r["rank"], r["user"], r["voucher_type"], r["total"]) for r in rows],
			[("1", "b@example.com", "Payment Entry", 10), ("1", "b@example.com", "Journal Entry", 8), ("1", "b@example.com", "Sales Invoice", 4),
			 ("2", "a@example.com", "Sales Invoice", 12), ("2", "a@example.com", "Payment Entry", 5)])

	def test_ties_are_broken_by_user_id_and_the_columns_add_up(self):
		rows = self.rows()
		self.assertEqual([r["user"] for r in rows if r["voucher_type"] == "Sales Invoice"], ["a@example.com", "b@example.com"])
		for r in rows:
			self.assertEqual(r["total"], r["draft"] + r["submitted"] + r["cancelled"])

	def test_the_share_is_of_that_users_own_entries(self):
		rows = {(r["user"], r["voucher_type"]): r for r in self.rows()}
		self.assertEqual(rows[("a@example.com", "Sales Invoice")]["share"], "70.6%")  # 12 of 17
		self.assertEqual(rows[("a@example.com", "Payment Entry")]["share"], "29.4%")
		self.assertEqual(rows[("a@example.com", "Sales Invoice")]["full_name"], "Aaa")

	def test_a_user_with_no_name_on_file_shows_their_id(self):
		(row,) = report.detail_rows(report.aggregate({"Sales Invoice": [("zed@example.com", 1, 1)]}), {})
		self.assertEqual(row["full_name"], "zed@example.com")

	def test_no_entries_no_rows(self):
		self.assertEqual(report.detail_rows({}, {}), [])


class TestThePivot(FrappeTestCase):
	def test_one_row_per_user_one_column_per_type_somebody_made(self):
		agg = report.aggregate(COUNTS)
		rows = report.pivot_rows(agg, NAMES)
		self.assertEqual(len(rows), 2)
		a = next(r for r in rows if r["user"] == "a@example.com")
		self.assertEqual((a["sales_invoice"], a["payment_entry"], a["journal_entry"], a["total"]), (12, 5, 0, 17))
		fields = [c["fieldname"] for c in report._pivot_columns(agg)]
		self.assertEqual(fields, ["rank", "user", "full_name", "sales_invoice", "payment_entry", "journal_entry", "total"])
		self.assertNotIn("stock_entry", fields)  # nobody made one: no empty column

	def test_the_pivot_columns_follow_the_voucher_type_order_not_the_data_order(self):
		agg = report.aggregate({"Journal Entry": [("a@example.com", 1, 1)], "Sales Invoice": [("a@example.com", 1, 1)]})
		self.assertEqual([c["fieldname"] for c in report._pivot_columns(agg)][3:5], ["sales_invoice", "journal_entry"])


class TestChartAndSummary(FrappeTestCase):
	def test_the_chart_is_the_top_ten_users(self):
		counts = {"Sales Invoice": [("u%02d@example.com" % i, 1, 30 - i) for i in range(12)]}
		agg = report.aggregate(counts)
		chart = report._chart(agg, {})
		self.assertEqual(len(chart["data"]["labels"]), 10)
		self.assertEqual(chart["data"]["datasets"][0]["values"][:3], [30, 29, 28])
		self.assertIsNone(report._chart({}, {}))

	def test_the_summary_cards(self):
		cards = {c["label"]: c["value"] for c in report._summary(report.aggregate(COUNTS), NAMES)}
		self.assertEqual((cards["Users"], cards["Entries"], cards["Still Draft"], cards["Cancelled"]), (2, 34, 2, 1))
		self.assertEqual(cards["Busiest User"], "Aaa")
		self.assertEqual(cards["Busiest Voucher Type"], "Sales Invoice")  # 16 entries against payment entry's 15

	def test_the_summary_of_nothing_says_dashes(self):
		cards = {c["label"]: c["value"] for c in report._summary({}, {})}
		self.assertEqual((cards["Users"], cards["Entries"], cards["Busiest User"], cards["Busiest Voucher Type"]), (0, 0, "-", "-"))


class TestTheQuery(FrappeTestCase):
	def sql(self, **filters):
		f = frappe._dict(company="Steel Force Trading WLL", from_date="2026-10-01", to_date="2026-10-20", **filters)
		return report.count_query("Sales Invoice", f, "posting_date", True).get_sql()

	def test_it_counts_by_creator_and_docstatus_inside_the_company_and_dates(self):
		sql = self.sql()
		for needle in ("`owner`", "`docstatus`", "COUNT(*)", "GROUP BY `owner`,`docstatus`", "`company`='Steel Force Trading WLL'",
				"`posting_date`>='2026-10-01'", "`posting_date`<='2026-10-20'"):
			self.assertIn(needle, sql)

	def test_the_user_filter_narrows_to_one_creator(self):
		self.assertIn("`owner`='a@example.com'", self.sql(user="a@example.com"))

	def test_a_doctype_with_no_company_is_not_filtered_by_one(self):
		f = frappe._dict(company="X", from_date="2026-10-01", to_date="2026-10-20")
		self.assertNotIn("`company`", report.count_query("Payment Advice", f, "transaction_date", False).get_sql())

	def test_a_creation_datetime_takes_the_whole_last_day(self):
		f = frappe._dict(company="X", from_date="2026-10-01", to_date="2026-10-20")
		sql = report.count_query("Foo", f, "creation", True).get_sql()
		self.assertIn("`creation`<'2026-10-21'", sql)

	def test_the_date_field_is_the_documents_own_date(self):
		def meta(*fields):
			m = MagicMock()
			m.has_field.side_effect = lambda f: f in fields
			return m

		self.assertEqual(report.date_field(meta("posting_date", "transaction_date")), "posting_date")
		self.assertEqual(report.date_field(meta("transaction_date")), "transaction_date")
		self.assertEqual(report.date_field(meta()), "creation")


class TestWhichTypesAreCounted(FrappeTestCase):
	def types(self, **filters):
		with patch.object(report.frappe.db, "exists", side_effect=lambda dt, name=None: name != "Payment Advice", create=True), \
				patch.object(report.frappe.db, "table_exists", return_value=True, create=True):
			return report._types(frappe._dict(filters))

	def test_every_type_the_site_has_and_no_other(self):
		types = self.types()
		self.assertIn("Sales Invoice", types)
		self.assertNotIn("Payment Advice", types)

	def test_one_picked_type_is_the_only_one(self):
		self.assertEqual(self.types(voucher_type="Journal Entry"), ["Journal Entry"])

	def test_a_type_that_is_not_counted_is_refused(self):
		with self.assertRaises(frappe.ValidationError):
			self.types(voucher_type="Account")


class TestWhoMayOpenIt(FrappeTestCase):
	def run_it(self, user, roles, **filters):
		with patch.object(report.frappe, "session", frappe._dict(user=user), create=True), \
				patch.object(report.frappe, "get_roles", return_value=roles, create=True), \
				patch.object(report, "_types", return_value=["Sales Invoice"]), \
				patch.object(report, "_counts", return_value=COUNTS["Sales Invoice"]), \
				patch.object(report, "_full_names", return_value=NAMES):
			return report.execute(dict({"company": "Steel Force Trading WLL"}, **filters))

	def test_a_user_with_no_accounts_role_is_refused(self):
		with self.assertRaises(frappe.PermissionError):
			self.run_it("buyer@example.com", ["Stock User"])

	def test_an_accounts_manager_a_system_manager_and_administrator_may(self):
		for user, roles in (("a@example.com", ["Accounts Manager"]), ("b@example.com", ["System Manager"]), ("Administrator", [])):
			columns, rows, message, chart, summary = self.run_it(user, roles)
			self.assertEqual((len(rows), message), (2, None), user)  # one row per user and voucher type
			self.assertTrue(chart and summary)

	def test_the_pivot_view_returns_the_pivot_columns(self):
		columns, rows, *_ = self.run_it("Administrator", [], view=report.PIVOT)
		self.assertIn("sales_invoice", [c["fieldname"] for c in columns])
		self.assertEqual(len(rows), 2)

	def test_a_company_is_required(self):
		with patch.object(report.frappe, "session", frappe._dict(user="Administrator"), create=True):
			with self.assertRaises(frappe.ValidationError):
				report.execute({})


class TestWhatTheSystemShips(FrappeTestCase):
	def test_the_report_is_for_accounts_managers_and_system_managers(self):
		meta = json.loads(read("sf_trading", "report", "entries_by_user", "entries_by_user.json"))
		self.assertEqual(({r["role"] for r in meta["roles"]}, meta["report_type"], meta["add_total_row"], meta["name"]),
			({"Accounts Manager", "System Manager"}, "Script Report", 1, "Entries by User"))
		for ext in ("py", "js"):
			self.assertTrue(os.path.exists(os.path.join(ROOT, "sf_trading", "report", "entries_by_user", "entries_by_user." + ext)))

	def test_the_filters_offer_every_voucher_type_that_is_counted(self):
		js = read("sf_trading", "report", "entries_by_user", "entries_by_user.js")
		for voucher_type in report.VOUCHER_TYPES:
			self.assertIn('"%s"' % voucher_type, js)
		for fieldname in ("company", "from_date", "to_date", "user", "voucher_type", "view"):
			self.assertIn('fieldname: "%s"' % fieldname, js)
