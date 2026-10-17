# sf_trading/tests/test_items_without_price_list.py
"""Items Without Price List: which items it keeps (priced, unpriced or both), why, for which company and branch.

Queries are patched and read back, so no data is needed and no Item Price is touched (never run a
test module against a real site -- ERPNext's before_tests wipes Item Price):

    bench --site <scratch-site> run-tests --module sf_trading.tests.test_items_without_price_list
"""

import datetime as dt
from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase

from sf_trading import branch_price_list
from sf_trading.sf_trading.report.items_without_price_list import items_without_price_list as report

ON = dt.date(2026, 10, 17)


class Row(dict):
	__getattr__ = dict.get


def price_row(rate, valid_from=None, valid_upto=None, name="IP-1"):
	return Row(name=name, price_list_rate=rate, valid_from=valid_from, valid_upto=valid_upto)


def item(code, group="Hand Tool", **extra):
	base = dict(
		item_code=code, item_name=code.title(), item_group=group, brand=None, stock_uom="Nos", disabled=0,
		last_purchase_rate=0, item_valuation_rate=0,
	)
	base.update(extra)
	return Row(base)


class TestWhereAnItemStands(FrappeTestCase):
	def test_no_row_at_all(self):
		self.assertEqual(report.status_for([], ON), report.STATUS_NONE)

	def test_rows_that_are_all_zero(self):
		self.assertEqual(report.status_for([price_row(0), price_row(0)], ON), report.STATUS_ZERO)

	def test_a_real_rate_outside_its_dates(self):
		rows = [price_row(0), price_row(2.5, valid_upto=dt.date(2026, 9, 1))]
		self.assertEqual(report.status_for(rows, ON), report.STATUS_EXPIRED)
		rows = [price_row(2.5, valid_from=dt.date(2026, 11, 1))]
		self.assertEqual(report.status_for(rows, ON), report.STATUS_EXPIRED)

	def test_a_valid_price_is_priced(self):
		self.assertEqual(report.status_for([price_row(2.5)], ON), report.STATUS_PRICED)
		rows = [price_row(2.5, valid_from=dt.date(2026, 1, 1), valid_upto=dt.date(2026, 12, 31))]
		self.assertEqual(report.status_for(rows, ON), report.STATUS_PRICED)
		# one valid row among lapsed ones is enough
		self.assertEqual(report.status_for([price_row(1, valid_upto=dt.date(2026, 1, 1)), price_row(3)], ON), report.STATUS_PRICED)

	def test_the_price_in_the_list_is_the_first_valid_row(self):
		rows = [price_row(4.0, valid_upto=dt.date(2026, 1, 1), name="old"), price_row(5.0, name="now"), price_row(6.0, name="later")]
		self.assertEqual(report.chosen_price(rows, ON).name, "now")

	def test_an_expired_item_shows_the_price_that_lapsed_last(self):
		rows = [price_row(1.0, valid_upto=dt.date(2026, 1, 1), name="a"), price_row(2.0, valid_upto=dt.date(2026, 6, 1), name="b"), price_row(0, name="c")]
		self.assertEqual(report.chosen_price(rows, ON).name, "b")

	def test_a_zero_only_or_empty_item_has_no_price_in_the_list(self):
		self.assertIsNone(report.chosen_price([price_row(0)], ON))
		self.assertIsNone(report.chosen_price([], ON))

	def test_each_current_list_is_named_once_in_order(self):
		text = report.current_lists_text([("Wholesale", 7.5), ("Offer Price", 8.8), ("Offer Price", 8.8)])
		self.assertEqual(text, "Offer Price: 8.800; Wholesale: 7.500")
		self.assertEqual(report.current_lists_text([]), "")


class TestWhichListIsChecked(FrappeTestCase):
	def resolve(self, filters, kind="selling", flags=None, branch_default=None, site_default="Standard Selling"):
		flags = {"enabled": 1, "selling": 1, "buying": 0} if flags is None else flags
		with patch.object(branch_price_list, "default_price_list", return_value=branch_default), patch.object(
			frappe.db, "get_single_value", return_value=site_default, create=True
		), patch.object(frappe.db, "get_value", return_value=frappe._dict(flags) if flags else None, create=True):
			return report.resolve_price_list(frappe._dict(filters), kind)

	def test_the_filter_wins(self):
		self.assertEqual(self.resolve({"price_list": "Offer"}, branch_default="Branch List"), "Offer")

	def test_a_branch_uses_its_own_default_list(self):
		self.assertEqual(self.resolve({"branch": "Dock"}, branch_default="Branch List"), "Branch List")

	def test_a_branch_with_none_falls_to_the_site_default(self):
		self.assertEqual(self.resolve({"branch": "Dock"}), "Standard Selling")

	def test_no_list_anywhere_is_refused(self):
		with self.assertRaises(frappe.ValidationError):
			self.resolve({}, site_default=None)

	def test_a_selling_list_is_refused_on_the_buying_side(self):
		with self.assertRaises(frappe.ValidationError):
			self.resolve({"price_list": "Standard Selling"}, kind="buying")

	def test_a_list_that_does_not_exist_is_refused(self):
		with self.assertRaises(frappe.ValidationError):
			self.resolve({"price_list": "Nope"}, flags={})


class TestTheItemQuery(FrappeTestCase):
	def ask(self, kind="selling", warehouses=None, **filters):
		with patch.object(
			frappe.db, "get_value", return_value=frappe._dict(lft=3, rgt=9), create=True
		), patch.object(frappe.db, "sql", return_value=[], create=True) as sql:
			report.fetch_items(frappe._dict(filters), kind, "Standard Selling", ON, warehouses)
		query, values = sql.call_args.args[0], sql.call_args.args[1]
		return query, values

	def test_the_gap_is_asked_for_by_default(self):
		query, values = self.ask()
		self.assertIn("not exists (select 1 from `tabItem Price` p where", query)
		self.assertIn("p.price_list_rate > 0", query)
		self.assertEqual(values["price_list"], "Standard Selling")
		self.assertEqual(values["on_date"], ON)

	def test_customer_and_supplier_prices_do_not_count(self):
		query, _values = self.ask()
		self.assertIn("ifnull(p.customer, '') = ''", query)
		self.assertIn("ifnull(p.supplier, '') = ''", query)

	def test_only_stock_items_unless_told_otherwise(self):
		self.assertIn("it.is_stock_item = 1", self.ask()[0])
		self.assertIn("it.is_stock_item = 0", self.ask(item_type="Services / Non-Stock")[0])
		self.assertNotIn("it.is_stock_item", self.ask(item_type="All Items")[0])

	def test_each_side_asks_about_its_own_items(self):
		self.assertIn("it.is_sales_item = 1", self.ask("selling")[0])
		self.assertIn("it.is_purchase_item = 1", self.ask("buying")[0])

	def test_disabled_items_only_when_asked(self):
		self.assertIn("it.disabled = 0", self.ask()[0])
		self.assertNotIn("it.disabled = 0", self.ask(include_disabled=1)[0])

	def test_an_item_group_covers_everything_beneath_it(self):
		query, values = self.ask(item_group="Hand Tool")
		self.assertIn("ig.lft >= %(lft)s and ig.rgt <= %(rgt)s", query)
		self.assertEqual((values["lft"], values["rgt"]), (3, 9))

	def test_item_and_brand_filters(self):
		query, values = self.ask(item_code="FILE", brand="Vito")
		self.assertIn("it.name = %(item_code)s", query)
		self.assertIn("it.brand = %(brand)s", query)
		self.assertEqual((values["item_code"], values["brand"]), ("FILE", "Vito"))

	def test_show_with_price_asks_for_a_valid_price_instead(self):
		query, _values = self.ask(show="With Price")
		self.assertNotIn("not exists (select 1 from `tabItem Price` p", query)
		self.assertIn("exists (select 1 from `tabItem Price` p where", query)
		self.assertIn("p.price_list_rate > 0", query)

	def test_show_all_items_asks_nothing_about_the_price(self):
		query, _values = self.ask(show="All Items")
		self.assertNotIn("from `tabItem Price` p where p.item_code = it.name and p.price_list = %(price_list)s", query)

	def test_show_without_price_is_the_same_as_leaving_it_blank(self):
		self.assertEqual(self.ask(show="Without Price")[0], self.ask()[0])

	def test_a_status_decides_over_show(self):
		query, _values = self.ask(show="With Price", status=report.STATUS_NONE)
		self.assertEqual(query.count("not exists (select 1 from `tabItem Price` p where"), 2)
		query, _values = self.ask(show="Without Price", status=report.STATUS_PRICED)
		self.assertNotIn("not exists (select 1 from `tabItem Price` p where", query)

	def test_status_no_item_price_wants_no_row_at_all(self):
		query, _values = self.ask(status=report.STATUS_NONE)
		self.assertEqual(query.count("not exists (select 1 from `tabItem Price` p where"), 2)

	def test_status_zero_rate_wants_rows_but_none_above_zero(self):
		query, _values = self.ask(status=report.STATUS_ZERO)
		self.assertIn("exists (select 1 from `tabItem Price` p where", query)
		self.assertIn("and p.price_list_rate > 0)", query)

	def test_status_expired_wants_a_real_rate_somewhere(self):
		query, _values = self.ask(status=report.STATUS_EXPIRED)
		self.assertIn("exists (select 1 from `tabItem Price` p where p.item_code = it.name and p.price_list = %(price_list)s", query)

	def test_a_branch_counts_only_the_prices_it_can_use(self):
		with patch.object(report.item_price_branch, "table_ready", return_value=True):
			query, values = self.ask(branch="Dock")
		self.assertIn("from `tabItem Price Branch` pb where pb.parent = p.name", query)
		self.assertIn("pb.branch = %(branch)s", query)
		self.assertEqual(values["branch"], "Dock")

	def test_without_a_branch_every_price_counts(self):
		with patch.object(report.item_price_branch, "table_ready", return_value=True):
			query, _values = self.ask()
		self.assertNotIn("tabItem Price Branch", query)

	def test_before_the_branch_table_exists_the_branch_is_not_asked(self):
		with patch.object(report.item_price_branch, "table_ready", return_value=False):
			query, _values = self.ask(branch="Dock")
		self.assertNotIn("tabItem Price Branch", query)

	def test_a_company_keeps_its_own_items(self):
		query, values = self.ask(company="Steel Force Trading WLL")
		self.assertIn("`tabItem Default` idf where idf.parent = it.name", query)
		self.assertIn("idf.company = %(company)s", query)
		self.assertEqual(values["company"], "Steel Force Trading WLL")
		self.assertNotIn("tabItem Default", self.ask()[0])

	def test_sold_since_is_that_companys_invoices(self):
		query, _values = self.ask(company="C1", moved_since="2026-09-01")
		self.assertIn("mh.company = %(company)s", query)
		self.assertNotIn("mh.company", self.ask(moved_since="2026-09-01")[0])

	def test_priced_nowhere_checks_every_enabled_list_of_the_side(self):
		query, _values = self.ask("selling", no_price_anywhere=1)
		self.assertIn("pl.enabled = 1 and pl.selling = 1", query)
		query, _values = self.ask("buying", no_price_anywhere=1)
		self.assertIn("pl.enabled = 1 and pl.buying = 1", query)

	def test_in_stock_counts_only_the_branch_warehouses(self):
		query, values = self.ask(only_in_stock=1, warehouses=("SFSS - SFB",))
		self.assertIn("b.actual_qty > 0 and b.warehouse in %(warehouses)s", query)
		self.assertEqual(values["warehouses"], ("SFSS - SFB",))
		query, values = self.ask(only_in_stock=1)
		self.assertNotIn("warehouses", values)

	def test_sold_since_reads_sales_for_selling_and_purchases_for_buying(self):
		query, values = self.ask("selling", moved_since="2026-09-01")
		self.assertIn("`tabSales Invoice Item` mi", query)
		self.assertEqual(values["moved_since"], dt.date(2026, 9, 1))
		self.assertIn("`tabPurchase Invoice Item` mi", self.ask("buying", moved_since="2026-09-01")[0])

	def test_what_is_in_stock_comes_first_so_a_cut_off_loses_the_dead_items(self):
		query, values = self.ask()
		self.assertIn("order by (select ifnull(sum(ob.actual_qty), 0) > 0 from `tabBin` ob where ob.item_code = it.name) desc", query)
		query, values = self.ask(warehouses=("SFSS - SFB",))
		self.assertIn("ob.warehouse in %(warehouses)s) desc", query)
		self.assertEqual(values["warehouses"], ("SFSS - SFB",))

	def test_one_more_than_the_cap_is_asked_for_so_truncation_can_be_told(self):
		self.assertEqual(self.ask()[1]["cap"], report.ROW_CAP + 1)

	def test_no_value_is_joined_into_the_text(self):
		query, _values = self.ask(item_code="X'; drop table tabItem; --", brand="B")
		self.assertNotIn("drop table", query)


class TestCompanyWarehouses(FrappeTestCase):
	def scope(self, company, branch, company_warehouses, branch_warehouses):
		with patch.object(report, "branch_warehouses", return_value=branch_warehouses), patch.object(
			frappe, "get_all", return_value=list(company_warehouses)
		):
			return report.scope_warehouses(company, branch)

	def test_no_company_leaves_the_branchs_warehouses_as_they_are(self):
		self.assertEqual(self.scope(None, "Dock", [], ("D1",)), ("D1",))
		self.assertIsNone(self.scope(None, None, [], None))

	def test_a_company_reads_stock_from_its_own_warehouses(self):
		self.assertEqual(self.scope("C1", None, ["W2", "W1"], None), ("W1", "W2"))

	def test_a_branch_narrows_the_companys_warehouses(self):
		self.assertEqual(self.scope("C1", "Dock", ["W1", "W2", "W3"], ("W2", "W9")), ("W2",))

	def test_a_company_with_no_warehouse_holds_nothing_rather_than_everything(self):
		self.assertEqual(self.scope("C1", None, [], None), report.NO_WAREHOUSE)


class TestBranchWarehouses(FrappeTestCase):
	def test_no_branch_means_no_restriction(self):
		self.assertIsNone(report.branch_warehouses(None))

	def test_group_warehouses_are_opened_up(self):
		def get_all(doctype, **kwargs):
			if doctype == "Branch Configuration Warehouse":
				return ["Dock Group"]
			return ["Dock A", "Dock B"]

		with patch.object(frappe, "get_all", side_effect=get_all), patch.object(
			frappe.db, "get_value", return_value=frappe._dict(lft=1, rgt=4), create=True
		):
			self.assertEqual(report.branch_warehouses("Dock"), ("Dock A", "Dock B"))

	def test_a_branch_with_no_warehouses_is_not_restricted(self):
		with patch.object(frappe, "get_all", return_value=[]):
			self.assertIsNone(report.branch_warehouses("Dock"))


class TestTheRows(FrappeTestCase):
	def build(self, items, kind="selling", own=None, current=None, stock=None, last=None, branch=None, company=None, scopes=None):
		with patch.object(report, "_own_list_rows", return_value=own or {}), patch.object(
			report, "_current_lists", return_value=current or {}
		), patch.object(report, "_stock", return_value=stock or {}), patch.object(
			report, "_last_movement", return_value=last or {}
		), patch.object(report, "_margin_for_group", return_value=8.0), patch.object(
			report.item_price_branch, "branches_by_price", return_value=scopes or {}
		):
			return report.build_rows(items, kind, "Standard Selling", ON, None, branch, company)

	def test_an_item_priced_in_another_list_says_so(self):
		rows = self.build([item("FILE")], current={"FILE": [("Offer Price", 8.8)]})
		self.assertEqual(rows[0]["status"], report.STATUS_NONE)
		self.assertEqual(rows[0]["price_in_list"], 0.0)
		self.assertEqual(rows[0]["current_price_lists"], "Offer Price: 8.800")
		self.assertEqual(rows[0]["price_list_count"], 1)

	def test_an_item_priced_nowhere_has_nothing_in_the_column(self):
		rows = self.build([item("FILE")])
		self.assertEqual((rows[0]["current_price_lists"], rows[0]["price_list_count"]), ("", 0))

	def test_valuation_is_the_average_of_what_is_in_stock(self):
		rows = self.build([item("FILE")], stock={"FILE": Row(qty=4, value=10.0, rate=9.0)})
		self.assertEqual(rows[0]["valuation_rate"], 2.5)

	def test_with_nothing_in_stock_the_last_known_rate_is_shown(self):
		rows = self.build([item("FILE")], stock={"FILE": Row(qty=0, value=0, rate=1.512)})
		self.assertEqual(rows[0]["valuation_rate"], 1.512)
		rows = self.build([item("FILE", item_valuation_rate=0.7)])
		self.assertEqual(rows[0]["valuation_rate"], 0.7)

	def test_the_minimum_price_is_the_higher_cost_plus_the_group_margin(self):
		rows = self.build([item("FILE", last_purchase_rate=2.0)], stock={"FILE": Row(qty=4, value=10.0, rate=0)})
		self.assertAlmostEqual(rows[0]["min_price"], 2.5 * 1.08)

	def test_no_cost_means_no_minimum_price_and_buying_has_none(self):
		self.assertEqual(self.build([item("FILE")])[0]["min_price"], 0.0)
		rows = self.build([item("FILE", last_purchase_rate=2.0)], kind="buying")
		self.assertEqual(rows[0]["min_price"], 0.0)

	def test_the_last_movement_is_carried_over(self):
		rows = self.build([item("FILE")], last={"FILE": {"date": dt.date(2026, 10, 5), "rate": 1.364}})
		self.assertEqual((rows[0]["last_rate"], rows[0]["last_date"]), (1.364, dt.date(2026, 10, 5)))

	def test_status_follows_the_rows_in_the_checked_list(self):
		rows = self.build(
			[item("A"), item("B"), item("C"), item("D")],
			own={"B": [price_row(0)], "C": [price_row(3, valid_upto=dt.date(2026, 1, 1))], "D": [price_row(4.5)]},
		)
		self.assertEqual(
			[r["status"] for r in rows], [report.STATUS_NONE, report.STATUS_ZERO, report.STATUS_EXPIRED, report.STATUS_PRICED]
		)
		self.assertEqual(rows[2]["price_in_list"], 3.0)
		self.assertEqual(rows[3]["price_in_list"], 4.5)

	def test_a_priced_item_says_whom_the_price_serves(self):
		own = {"A": [price_row(4.5, name="IP-A")], "B": [price_row(5.0, name="IP-B")]}
		rows = self.build([item("A"), item("B")], own=own, scopes={"IP-B": {"Mall", "Dock"}})
		self.assertEqual(rows[0]["price_branches"], "All Branches")
		self.assertEqual(rows[1]["price_branches"], "Dock, Mall")

	def test_without_a_branch_the_general_price_is_the_one_shown(self):
		# newest first, as ERPNext orders them: the branch price was made last
		own = {"A": [price_row(9.0, name="IP-B"), price_row(8.0, name="IP-A"), price_row(10.0, name="IP-ALL")]}
		scopes = {"IP-B": {"Dock"}, "IP-A": {"Mall"}}
		row = self.build([item("A")], own=own, scopes=scopes)[0]
		self.assertEqual((row["price_in_list"], row["price_branches"]), (10.0, "All Branches"))

	def test_without_a_branch_a_limited_price_is_shown_when_it_is_the_only_one(self):
		own = {"A": [price_row(9.0, name="IP-B")]}
		row = self.build([item("A")], own=own, scopes={"IP-B": {"Dock"}})[0]
		self.assertEqual((row["status"], row["price_in_list"], row["price_branches"]), (report.STATUS_PRICED, 9.0, "Dock"))

	def test_with_a_branch_the_rows_come_in_the_order_the_branch_sees_them(self):
		own = {"A": [price_row(9.0, name="IP-B"), price_row(10.0, name="IP-ALL")]}
		row = self.build([item("A")], own=own, scopes={"IP-B": {"Dock"}}, branch="Dock")[0]
		self.assertEqual(row["price_in_list"], 9.0)

	def test_an_unpriced_item_has_no_price_scope(self):
		self.assertEqual(self.build([item("A")])[0]["price_branches"], "")

	def test_the_summary_counts_each_kind_of_gap_and_the_priced_elsewhere(self):
		rows = self.build(
			[item("A"), item("B"), item("C"), item("D")],
			own={"B": [price_row(0)], "D": [price_row(2.0)]},
			current={"A": [("Offer Price", 1.0)], "D": [("Standard Selling", 2.0)]},
		)
		summary = {entry["label"]: entry["value"] for entry in report.get_summary(rows)}
		self.assertEqual(summary["Items Listed"], 4)
		self.assertEqual(summary[report.STATUS_PRICED], 1)
		self.assertEqual(summary[report.STATUS_NONE], 2)
		self.assertEqual(summary[report.STATUS_ZERO], 1)
		# only unpriced items count as "priced elsewhere"; the priced one is simply priced
		self.assertEqual(summary["Unpriced Here, Priced in Another List"], 1)
		self.assertEqual(summary["Priced Nowhere"], 2)

	def test_the_columns_follow_the_side(self):
		selling = [c["fieldname"] for c in report.get_columns("selling")]
		buying = [c["fieldname"] for c in report.get_columns("buying")]
		self.assertIn("min_price", selling)
		self.assertNotIn("min_price", buying)
		self.assertIn("current_price_lists", selling)


class TestBatching(FrappeTestCase):
	def test_codes_are_fetched_in_slices(self):
		codes = ["C%d" % n for n in range(report.BATCH * 2 + 1)]
		self.assertEqual([len(chunk) for chunk in report._batches(codes)], [report.BATCH, report.BATCH, 1])
		self.assertEqual(list(report._batches([])), [])


class TestMargins(FrappeTestCase):
	def test_the_nearest_margin_up_the_tree_is_used_and_remembered(self):
		tree = {"Bolt": (0, "Fasteners"), "Fasteners": (8.0, "All"), "All": (0, None)}
		calls = []

		def get_value(doctype, name, field):
			calls.append((name, field))
			margin, parent = tree[name]
			return margin if field == "custom_min_margin_pct" else parent

		memo = {}
		with patch.object(frappe.db, "get_value", side_effect=get_value, create=True):
			self.assertEqual(report._margin_for_group("Bolt", memo), 8.0)
			before = len(calls)
			self.assertEqual(report._margin_for_group("Bolt", memo), 8.0)
		self.assertEqual(len(calls), before)
		self.assertEqual(memo["Fasteners"], 8.0)

	def test_no_margin_anywhere_is_zero(self):
		with patch.object(frappe.db, "get_value", side_effect=lambda d, n, f: 0 if f == "custom_min_margin_pct" else None, create=True):
			self.assertEqual(report._margin_for_group("Lonely", {}), 0.0)
