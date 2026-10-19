# sf_trading/tests/test_cash_flow_statement_detail.py
"""Cash Flow Statement Detail (sf_trading/cash_flow_statement_detail.py): the vouchers behind a figure.

The database is patched at the module's own seams, so nothing is created or read:

    bench --site <scratch-site> run-tests --module sf_trading.tests.test_cash_flow_statement_detail

The property under test: for EVERY line, group and total of BOTH statements, on random books, the vouchers this
report lists add up to exactly the figure the statement shows for that line.
"""

import json
import os
import random
from datetime import date
from unittest.mock import MagicMock, patch

import frappe
from frappe.tests.utils import FrappeTestCase

from sf_trading import cash_flow_statement as cfs
from sf_trading import cash_flow_statement_detail as det
from sf_trading.tests.test_cash_flow_statement import CASH_ACCOUNTS, LINE_OF

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MONTHS = ("2026-01", "2026-02", "2026-03", "2026-04")
TYPES = ("Sales Invoice", "Purchase Invoice", "Payment Entry", "Journal Entry", "Stock Entry")


def read(*parts):
	with open(os.path.join(ROOT, *parts), encoding="utf-8") as f:
		return f.read()


def random_vouchers(seed, vouchers=150):
	"""GL entries as the detail query returns them (one per voucher and account), from balanced vouchers."""
	rng = random.Random(seed)
	names = list(LINE_OF)
	out = []
	for n in range(vouchers):
		accounts = rng.sample(names, rng.randint(2, 5))
		nets = [round(rng.uniform(1, 3000), 3) * rng.choice((1, -1)) for _a in accounts[:-1]]
		nets.append(-sum(nets))
		vtype, month = rng.choice(TYPES), rng.choice(MONTHS)
		for account, net in zip(accounts, nets):
			out.append(dict(voucher_type=vtype, voucher_no="%s-%04d" % (vtype[:3].upper(), n), account=account,
				party_type="Customer" if rng.random() < 0.5 else None, party=("CUST-%d" % rng.randint(1, 6)) if rng.random() < 0.5 else None,
				branch="SFSB", cost_center="SFSB - SFB", posting_date=date(int(month[:4]), int(month[5:]), rng.randint(1, 28)),
				remarks="r%d" % n, debit=max(-net, 0.0), credit=max(net, 0.0)))
	return out


def flows_of(entries):
	by = {}
	for e in entries:
		key = (e["account"], e["posting_date"].strftime("%Y-%m"))
		by[key] = by.get(key, 0.0) + e["credit"] - e["debit"]
	return [(a, m, net) for (a, m), net in by.items()]


def filters(**extra):
	base = dict(company="Steel Force Trading WLL", from_date=date(2026, 1, 1), to_date=date(2026, 4, 30), method="Direct")
	base.update(extra)
	return frappe._dict(base)


class TestTheTitles(FrappeTestCase):
	def test_every_title_of_a_method_is_unique_so_a_name_means_one_figure(self):
		for method in (cfs.D, cfs.I):
			seen = {}
			for section in cfs.SECTIONS[method]:
				titles = [section["total"]] + [g["label"] for g in section.get("groups") or []]
				titles += [ln["label"] for ln in display_lines_of(section)]
				for t in titles:
					self.assertNotIn(t.lower(), seen, "%s: '%s' names two figures" % (method, t))
					seen[t.lower()] = t
			self.assertNotIn(cfs.GRAND_TITLE.lower(), seen)

	def test_a_plain_line_weighs_its_own_key_once(self):
		table = cfs.title_weights(cfs.D)
		self.assertEqual(table["change in accounts receivable"]["weights"], {"AR": 1})
		self.assertEqual(table["depreciation, amortisation and other non-cash items (net)"]["weights"]["ACC_DEP"], 1)

	def test_an_indirect_line_that_adds_back_or_corrects_carries_its_signs(self):
		table = cfs.title_weights(cfs.I)
		self.assertEqual(table["loss / (gain) on disposal of assets"]["weights"], {"DISPOSAL": -1})
		self.assertEqual(table["inventory write-downs"]["weights"], {"STOCK_ADJ": -1})
		self.assertEqual(table["inventory (before write-downs)"]["weights"], {"INVENTORY": 1, "STOCK_ADJ": 1})

	def test_a_total_adds_its_lines_up_and_a_key_that_nets_to_nothing_drops_out(self):
		table = cfs.title_weights(cfs.I)
		operating = table["net cash from operating activities"]["weights"]
		self.assertNotIn("DISPOSAL", operating, "the gain is in net income and added back: nothing left in operating")
		self.assertEqual(operating["STOCK_ADJ"], 1, "the write-down is in net income, added back and taken out of inventory")
		self.assertNotIn("FIXED_ASSETS", operating)
		self.assertEqual(table["net cash from investing activities"]["weights"]["DISPOSAL"], 1)

	def test_the_whole_statement_weighs_every_line_once(self):
		for method in (cfs.D, cfs.I):
			self.assertEqual(cfs.title_weights(method)[cfs.GRAND_TITLE.lower()]["weights"], {key: 1 for key in cfs.LINES})

	def test_a_title_is_found_whatever_its_case_and_a_blank_means_the_whole_statement(self):
		title, weights = det.figure(filters(line="  CHANGE IN accounts RECEIVABLE "))
		self.assertEqual((title, weights), ("Change in accounts receivable", {"AR": 1}))
		self.assertEqual(det.figure(filters())[0], cfs.GRAND_TITLE)

	def test_an_unknown_title_is_refused_and_the_lines_are_listed(self):
		with self.assertRaises(frappe.ValidationError) as caught:
			det.figure(filters(line="Nonsense"))
		self.assertIn("Cash from customers", str(caught.exception))


def display_lines_of(section):
	lines = list(section.get("lines") or [])
	for group in section.get("groups") or []:
		lines += group["lines"]
	return lines


class TestTheVouchersAddUpToTheFigure(FrappeTestCase):
	"""The same entries, added up two ways: the statement's lines, and this report's vouchers."""

	def statement_figures(self, entries, method):
		periods = cfs.period_list("2026-01-01", "2026-04-30", "Total")
		model = cfs.aggregate(flows_of(entries), LINE_OF, periods)
		rows = cfs.Rows(periods, "BHD", "None", 1)
		cfs.statement_rows(model, method, rows, 0.0)
		return {r["drill_line"]: r["p0"] for r in rows.data if r.get("drill_line")}, model

	def detail_total(self, entries, method, title):
		_t, weights = det.figure(filters(line=title, method="Direct" if method == cfs.D else "Indirect"))
		selected = det.account_weights(filters(), LINE_OF, weights)
		mine = [e for e in entries if e["account"] in selected]
		return sum(e["amount"] for e in det.weighted(mine, selected)), selected, mine

	def test_every_line_group_and_total_of_both_statements_ties_to_its_vouchers(self):
		for seed in range(12):
			entries = random_vouchers(seed)
			for method in (cfs.D, cfs.I):
				figures, _model = self.statement_figures(entries, method)
				self.assertGreater(len(figures), 15)
				for title, shown in figures.items():
					total, _selected, _mine = self.detail_total(entries, method, title)
					self.assertAlmostEqual(total, shown, 5, "%s seed %s: %s" % (method, seed, title))

	def test_every_way_of_grouping_adds_up_to_the_same_total(self):
		entries = random_vouchers(3)
		total, selected, mine = self.detail_total(entries, cfs.I, cfs.GRAND_TITLE)
		weighted = det.weighted(mine, selected)
		for mode in det.GROUPS:
			report = det.build_rows(weighted, mode, "BHD")
			top = [r for r in report.data if r["indent"] == 0]
			self.assertAlmostEqual(sum(r["amount"] for r in top), total, 5, mode)
			self.assertAlmostEqual(sum(r["debit"] for r in top), sum(e["debit"] for e in weighted), 5, mode)

	def test_a_group_adds_up_its_vouchers_and_the_pooled_row(self):
		entries = random_vouchers(4, vouchers=400)
		total, selected, mine = self.detail_total(entries, cfs.D, cfs.GRAND_TITLE)
		weighted = det.weighted(mine, selected)
		with patch.object(det, "PER_GROUP", 3):
			report = det.build_rows(weighted, "Voucher Type", "BHD")
		heads = [i for i, r in enumerate(report.data) if r["indent"] == 0]
		for n, start in enumerate(heads):
			end = heads[n + 1] if n + 1 < len(heads) else len(report.data)
			children = report.data[start + 1:end]
			self.assertLessEqual(len([c for c in children if c.get("is_voucher")]), 3)
			self.assertAlmostEqual(sum(c["amount"] for c in children), report.data[start]["amount"], 5)
			self.assertTrue(any(c.get("is_pooled") for c in children))

	def test_the_flat_views_keep_the_biggest_and_pool_the_rest(self):
		weighted = det.weighted(random_vouchers(5, vouchers=300), {a: 1 for a in LINE_OF})
		for mode in ("Vouchers", "Transactions"):
			with patch.object(det, "MAX_ROWS", 20):
				report = det.build_rows(weighted, mode, "BHD")
			self.assertEqual(len(report.data), 21, mode)
			pooled = report.data[-1] if report.data[-1].get("is_pooled") else next(r for r in report.data if r.get("is_pooled"))
			self.assertTrue(pooled["label"].startswith("Other "))
			self.assertAlmostEqual(sum(r["amount"] for r in report.data), sum(e["amount"] for e in weighted), 5, mode)


class TestWhichAccountsAndWhatAnEntryIsWorth(FrappeTestCase):
	def test_the_accounts_of_a_figure_are_those_whose_line_it_weighs(self):
		selected = det.account_weights(filters(), LINE_OF, {"AR": 1})
		self.assertEqual(set(selected), {a for a, k in LINE_OF.items() if k == "AR"})
		self.assertTrue(all(w == 1 for w in selected.values()))

	def test_an_account_filter_narrows_it_and_cannot_widen_it(self):
		ar = sorted(a for a, k in LINE_OF.items() if k == "AR")
		self.assertEqual(set(det.account_weights(filters(account=[ar[0]]), LINE_OF, {"AR": 1})), {ar[0]})
		self.assertEqual(det.account_weights(filters(account=["13090100001 - Trading Inventory - SFB"]), LINE_OF, {"AR": 1}), {})
		self.assertEqual(det.account_weights(filters(account=json.dumps([ar[0]])), LINE_OF, {"AR": 1}).keys(), {ar[0]})
		self.assertEqual(det.account_weights(filters(account=ar[0]), LINE_OF, {"AR": 1}).keys(), {ar[0]})

	def test_an_entry_is_worth_weight_times_credit_minus_debit(self):
		entries = [dict(account="A", debit=40.0, credit=0.0), dict(account="B", debit=0.0, credit=25.0)]
		out = det.weighted(entries, {"A": -1, "B": 1})
		self.assertEqual([e["amount"] for e in out], [40.0, 25.0])

	def test_filters_arrive_as_lists_json_or_commas(self):
		self.assertEqual(det.listed(["a", " b "]), ["a", "b"])
		self.assertEqual(det.listed('["a", "b"]'), ["a", "b"])
		self.assertEqual(det.listed("a, b"), ["a", "b"])
		self.assertEqual(det.listed(None), [])


class TestTheRows(FrappeTestCase):
	def entries(self):
		base = dict(branch="SFSB", cost_center="SFSB - SFB", remarks="", debit=0.0)
		return det.weighted([
			dict(base, voucher_type="Sales Invoice", voucher_no="SINV-1", account="AR-1", party_type="Customer", party="C1", posting_date=date(2026, 1, 5), credit=0.0, debit=100.0),
			dict(base, voucher_type="Sales Invoice", voucher_no="SINV-1", account="Sales", party_type=None, party=None, posting_date=date(2026, 1, 5), credit=100.0, debit=0.0),
			dict(base, voucher_type="Payment Entry", voucher_no="PE-1", account="AR-1", party_type="Customer", party="C1", posting_date=date(2026, 2, 9), credit=60.0, debit=0.0),
		], {"AR-1": 1, "Sales": 1})

	def test_a_voucher_row_adds_its_accounts_and_names_the_first(self):
		report = det.build_rows(self.entries(), "Vouchers", "BHD")
		by = {r["label"]: r for r in report.data}
		self.assertEqual(by["SINV-1"]["account"], "AR-1 (+1)")
		self.assertEqual(by["SINV-1"]["amount"], 0.0)
		self.assertEqual(by["SINV-1"]["entries"], 2)
		self.assertEqual((by["SINV-1"]["voucher_type"], by["SINV-1"]["voucher_no"]), ("Sales Invoice", "SINV-1"))
		self.assertEqual(by["PE-1"]["amount"], 60.0)
		self.assertEqual(by["PE-1"]["party"], "C1")

	def test_groups_are_labelled_by_type_account_party_and_month(self):
		def labels(mode):
			return [r["label"] for r in det.build_rows(self.entries(), mode, "BHD").data if r.get("is_group")]
		self.assertEqual(sorted(labels("Voucher Type")), ["Payment Entry", "Sales Invoice"])
		self.assertEqual(sorted(labels("Account")), ["AR-1", "Sales"])
		self.assertEqual(sorted(labels("Party")), ["(no party)", "Customer: C1"])
		self.assertEqual(labels("Month"), ["Jan 2026", "Feb 2026"], "months run in date order")

	def test_the_biggest_group_comes_first_except_by_month(self):
		labels = [r["label"] for r in det.build_rows(self.entries(), "Voucher Type", "BHD").data if r.get("is_group")]
		self.assertEqual(labels, ["Payment Entry", "Sales Invoice"], "60 against 0")

	def test_vouchers_inside_a_group_run_in_date_order(self):
		weighted = det.weighted([dict(voucher_type="Journal Entry", voucher_no="JV-%d" % n, account="A", party_type=None, party=None, branch=None,
			cost_center=None, remarks="", debit=0.0, credit=float(n), posting_date=date(2026, 1, 10 - n)) for n in (1, 2, 3)], {"A": 1})
		rows = det.build_rows(weighted, "Voucher Type", "BHD").data
		self.assertEqual([r["label"] for r in rows if r.get("is_voucher")], ["JV-3", "JV-2", "JV-1"])


class TestTheLedgerQuery(FrappeTestCase):
	def test_it_reads_the_entries_the_statement_reads_and_binds_every_value(self):
		with patch.object(det.frappe.db, "sql", MagicMock(return_value=[]), create=True) as sql:
			det.fetch_entries(filters(), ["A", "B"], " and gle.branch in %(branches)s", {"branches": ("SFSB",)})
		query, params = sql.call_args.args
		for fragment in ("is_cancelled = 0", "ifnull(gle.is_opening, 'No') != 'Yes'", "voucher_type != 'Period Closing Voucher'",
				"gle.account in %(accounts)s", "gle.branch in %(branches)s", "group by gle.voucher_type, gle.voucher_no, gle.account"):
			self.assertIn(fragment, query)
		self.assertEqual(params["accounts"], ("A", "B"))
		self.assertNotIn("voucher_type = %(voucher_type)s", query)
		self.assertTrue(sql.call_args.kwargs.get("as_dict"))

	def test_voucher_type_and_party_narrow_it_with_bound_values(self):
		with patch.object(det.frappe.db, "sql", MagicMock(return_value=[]), create=True) as sql:
			det.fetch_entries(filters(voucher_type="Payment Entry", party_type="Customer", party="Easy Touch"), ["A"], "", {})
		query, params = sql.call_args.args
		for fragment in ("gle.voucher_type = %(voucher_type)s", "gle.party_type = %(party_type)s", "gle.party = %(party)s"):
			self.assertIn(fragment, query)
		self.assertEqual((params["voucher_type"], params["party_type"], params["party"]), ("Payment Entry", "Customer", "Easy Touch"))
		self.assertNotIn("Easy Touch", query)

	def test_the_query_text_holds_placeholders_only(self):
		for text in (det.ENTRIES, det.BY_VOUCHER_TYPE, det.BY_PARTY_TYPE, det.BY_PARTY, det.ENTRIES_GROUP):
			self.assertNotIn("{", text)


class TestTheReport(FrappeTestCase):
	BASE = dict(company="Steel Force Trading WLL", from_date="2026-01-01", to_date="2026-04-30", method="Direct", line="Change in accounts receivable")

	def run_report(self, extra=None, entries=None, statement_flows=None):
		entries = random_vouchers(7) if entries is None else entries
		flows = flows_of(entries) if statement_flows is None else statement_flows
		patches = [
			patch.object(det.cfs, "load_accounts", return_value=(LINE_OF, {})),
			patch.object(det, "fetch_entries", side_effect=lambda f, accounts, *a: [dict(e) for e in entries if e["account"] in set(accounts)]),
			patch.object(det.cfs, "fetch_flows", return_value=flows),
			patch.object(det.money, "scope_params", return_value=("", {})),
			patch.object(det.frappe, "has_permission", MagicMock(return_value=True), create=True),
			patch.object(det.frappe, "get_cached_value", MagicMock(return_value="BHD"), create=True),
		]
		for p in patches:
			p.start()
		try:
			return det.execute(frappe._dict(dict(self.BASE, **(extra or {}))))
		finally:
			for p in reversed(patches):
				p.stop()

	def test_execute_returns_columns_rows_a_total_a_message_and_a_summary(self):
		columns, data, message, chart, summary = self.run_report()
		self.assertEqual([c["fieldname"] for c in columns][:3], ["label", "posting_date", "voucher_type"])
		self.assertIsNone(chart)
		total = data[-1]
		self.assertTrue(total["is_total"])
		self.assertEqual(summary[0]["label"], "Cash Flow Effect")
		self.assertAlmostEqual(total["amount"], summary[0]["value"], 2)

	def test_the_total_row_names_the_accounts_for_the_general_ledger_button(self):
		_c, data, *_rest = self.run_report()
		self.assertEqual(data[-1]["accounts"], sorted(a for a, k in LINE_OF.items() if k == "AR" and a in {e["account"] for e in random_vouchers(7)}))

	def test_it_says_the_vouchers_add_up_to_the_statements_figure(self):
		_c, _d, message, *_rest = self.run_report()
		self.assertIn("the figure on the Cash Flow Statement", message)
		self.assertNotIn("tell the accounts team", message)

	def test_a_line_that_weighs_minus_one_still_ties_to_the_statements_figure(self):
		# the indirect statement adds a disposal gain back: its vouchers count against the figure
		for line in ("Loss / (gain) on disposal of assets", "Inventory (before write-downs)", "Net cash from operating activities", cfs.GRAND_TITLE):
			_c, data, message, *_rest = self.run_report({"method": "Indirect", "line": line})
			self.assertIn("the figure on the Cash Flow Statement", message, line)
			self.assertNotIn("tell the accounts team", message, line)

	def test_it_says_so_when_they_do_not(self):
		wrong = [("Customer 1 - SFB", "2026-01", 0.0)] + [(a, m, n + 5) for a, m, n in flows_of(random_vouchers(7))]
		_c, _d, message, *_rest = self.run_report(statement_flows=wrong)
		self.assertIn("tell the accounts team", message)

	def test_narrowing_by_voucher_type_or_party_says_it_is_part_of_the_figure(self):
		_c, _d, message, *_rest = self.run_report({"voucher_type": "Sales Invoice"})
		self.assertIn("part of the figure", message)
		self.assertNotIn("tell the accounts team", message)

	def test_an_unknown_group_by_or_method_or_voucher_type_is_refused(self):
		for bad in ({"group_by": "Weekly"}, {"method": "Both"}, {"voucher_type": "Quotation"}, {"company": ""}, {"line": "Nope"}):
			with self.assertRaises(frappe.ValidationError, msg=str(bad)):
				self.run_report(bad)

	def test_each_group_by_runs(self):
		for mode in det.GROUPS:
			columns, data, *_rest = self.run_report({"group_by": mode})
			self.assertTrue(data, mode)

	def test_a_figure_with_no_account_gives_an_empty_report_that_says_why(self):
		_c, data, message, *_rest = self.run_report({"account": ["13090100001 - Trading Inventory - SFB"]}, entries=[])
		self.assertEqual(len(data), 1)
		self.assertIn("No account of the company", message)


class TestWhatTheSystemShips(FrappeTestCase):
	def test_the_report_files_exist_with_the_right_names(self):
		base = os.path.join(ROOT, "sf_trading", "report", "cash_flow_statement_detail")
		for ext in ("py", "js", "json"):
			self.assertTrue(os.path.exists(os.path.join(base, "cash_flow_statement_detail." + ext)))
		report = json.loads(read("sf_trading", "report", "cash_flow_statement_detail", "cash_flow_statement_detail.json"))
		self.assertEqual(report["name"], "Cash Flow Statement Detail")

	def test_the_filters_the_statement_sends_exist_on_the_detail(self):
		js = read("sf_trading", "report", "cash_flow_statement_detail", "cash_flow_statement_detail.js")
		for name in ("company", "from_date", "to_date", "method", "line", "group_by", "account", "branch", "cost_center", "voucher_type", "party_type", "party"):
			self.assertIn('fieldname: "%s"' % name, js)
		for mode in det.GROUPS:
			self.assertIn(mode, js)

	def test_the_statement_marks_every_drillable_row_with_the_title_the_detail_resolves(self):
		periods = cfs.period_list("2026-01-01", "2026-04-30", "Total")
		model = cfs.aggregate(flows_of(random_vouchers(1)), LINE_OF, periods)
		for method, name in ((cfs.D, "Direct"), (cfs.I, "Indirect")):
			rows = cfs.Rows(periods, "BHD", "Top 15 and others", 1)
			cfs.statement_rows(model, method, rows, 0.0)
			titles = cfs.title_weights(method)
			for row in rows.data:
				if row.get("drill_line"):
					self.assertIn(row["drill_line"].lower(), titles, "%s: %s" % (name, row["drill_line"]))
			# an account row asks for its line's weight: the line it sits under
			line = None
			for row in rows.data:
				if row.get("is_line"):
					line = row
				elif row.get("is_account"):
					self.assertEqual(row["drill_line"], line["drill_line"], "%s: %s" % (name, row["label"]))
		self.assertTrue([r for r in rows.data if r.get("is_account")])
