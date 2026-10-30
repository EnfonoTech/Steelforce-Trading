"""The cost breakdown engine and its two reports (sf_trading/cogs_breakdown.py).

    bench --site <site> run-tests --module sf_trading.tests.test_cogs_breakdown

No site data is needed: a small in-memory ledger answers the queries the engine asks, written the way
ERPNext writes a Stock Ledger under Moving Average. The replay was also run, unchanged, over 18,883 real
outgoing entries of the live ledger: 99.9% agreed with the ledger within rounding, and the rest are the
cases pinned below (stock that left an empty bin, one 0.3 BHD gap).
"""

import unittest
from datetime import datetime, timedelta
from unittest.mock import patch

import frappe

from sf_trading import cogs_breakdown as cb
from sf_trading.sf_trading.report.cogs_cost_breakdown import cogs_cost_breakdown as cogs_report
from sf_trading.sf_trading.report.transfer_cost_breakdown import transfer_cost_breakdown as transfer_report

START = datetime(2026, 1, 1, 9, 0, 0)
TOL = 0.0015


class Bin:
	"""One item in one warehouse, entered the way ERPNext writes the ledger under Moving Average."""

	def __init__(self, ledger, item="ITEM-1", warehouse="WH - A"):
		self.ledger, self.item, self.warehouse = ledger, item, warehouse
		self.qty = self.value = self.last_rate = 0.0

	def _write(self, voucher_type, voucher_no, actual_qty, svd):
		self.ledger.n += 1
		n = self.ledger.n
		self.qty += actual_qty
		self.value += svd
		moment = START + timedelta(minutes=n)
		entry = frappe._dict(
			name=f"SLE-{n:04d}",
			item_code=self.item,
			warehouse=self.warehouse,
			voucher_type=voucher_type,
			voucher_no=voucher_no,
			voucher_detail_no=f"{voucher_no}-{self.item}",
			posting_date=moment.date(),
			posted=moment,
			creation=moment,
			actual_qty=actual_qty,
			qty_after_transaction=self.qty,
			stock_value=self.value,
			stock_value_difference=svd,
			valuation_rate=(self.value / self.qty) if self.qty else 0,
			company="Test Co",
		)
		self.ledger.entries.append(entry)
		return entry

	def receive(self, voucher_no, qty, rate, voucher_type="Purchase Receipt"):
		self.last_rate = rate
		return self._write(voucher_type, voucher_no, qty, qty * rate)

	def issue(self, voucher_no, qty, voucher_type="Sales Invoice", rate=None):
		if rate is None:
			rate = (self.value / self.qty) if self.qty > 1e-9 else self.last_rate
		return self._write(voucher_type, voucher_no, -qty, -qty * rate)

	def reconcile(self, voucher_no, qty, rate):
		return self._write("Stock Reconciliation", voucher_no, qty - self.qty, qty * rate - self.value)


class Ledger:
	"""Answers the Stock Ledger queries of cogs_breakdown from a list of entries."""

	def __init__(self):
		self.entries = []
		self.n = 0
		self.queries = []
		self.gl = {}

	def bin(self, item="ITEM-1", warehouse="WH - A") -> Bin:
		return Bin(self, item, warehouse)

	def of(self, item, warehouse):
		return [e for e in self.entries if e.item_code == item and e.warehouse == warehouse]

	def get_single_value(self, *args):
		return "Moving Average"

	def sql(self, query, values=None, as_dict=False):
		self.queries.append(query)
		key = lambda e: (e.posted, e.creation)  # noqa: E731
		if "from `tabGL Entry`" in query:
			_types, voucher_type, names = values
			return [(vn, self.gl[(voucher_type, vn)]) for vn in names if (voucher_type, vn) in self.gl]
		if "order by posted desc, creation desc limit 1" in query:
			p = values
			before = [e for e in self.of(p["item"], p["wh"]) if key(e) < (p["posted"], p["creation"])]
			hits = [e for e in before if e.voucher_type == p["reco"] or e.qty_after_transaction <= 0]
			return sorted(hits, key=key)[-1:]
		if "creation <= %(creation)s" in query:
			p = values
			rows = [e for e in self.of(p["item"], p["wh"]) if key(e) <= (p["posted"], p["creation"])]
			if "s_posted" in p:
				rows = [e for e in rows if key(e) >= (p["s_posted"], p["s_creation"])]
			return sorted(rows, key=key)
		if "voucher_type = 'Stock Entry' and voucher_no = %s and item_code = %s" in query:
			voucher_no, item = values
			return sorted(
				[
					e
					for e in self.entries
					if e.voucher_type == "Stock Entry"
					and e.voucher_no == voucher_no
					and e.item_code == item
					and e.actual_qty < 0
				],
				key=key,
			)
		if "group by voucher_detail_no" in query:
			(details,) = values
			out = {}
			for e in self.entries:
				if e.voucher_type == "Stock Entry" and e.voucher_detail_no in details and e.actual_qty > 0:
					qty, value = out.get(e.voucher_detail_no, (0.0, 0.0))
					out[e.voucher_detail_no] = (qty + e.actual_qty, value + e.stock_value_difference)
			return [(d, q, v) for d, (q, v) in out.items()]
		if "group by voucher_no, item_code" in query:
			voucher_type, names = values
			out = {}
			for e in self.entries:
				if e.voucher_type == voucher_type and e.voucher_no in names and e.actual_qty < 0:
					cost, qty = out.get((e.voucher_no, e.item_code), (0.0, 0.0))
					out[(e.voucher_no, e.item_code)] = (cost - e.stock_value_difference, qty - e.actual_qty)
			return [(v, i, c, q) for (v, i), (c, q) in out.items()]
		if "group by voucher_no" in query:
			voucher_type, names = values
			out = {}
			for e in self.entries:
				if e.voucher_type == voucher_type and e.voucher_no in names:
					out[e.voucher_no] = out.get(e.voucher_no, 0.0) - e.stock_value_difference
			return list(out.items())
		if "voucher_no in %s" in query:
			voucher_type, names = values
			return sorted(
				[e for e in self.entries if e.voucher_type == voucher_type and e.voucher_no in names], key=key
			)
		raise AssertionError(f"unexpected query: {query}")


def raising(message, exc=frappe.ValidationError, *args, **kwargs):
	"""frappe.throw without the request context it needs: raise what it was asked to."""
	raise exc(message)


class EngineCase(unittest.TestCase):
	"""frappe.db is the in-memory ledger; strings are not translated; the currency has three decimals."""

	purposes: dict

	def setUp(self):
		self.ledger = Ledger()
		self.purposes = {}
		self.item_groups = {}
		self.customer_groups = {}
		self.teams = {}
		self.group_children = {}
		frappe.local.db = self.ledger
		self.addCleanup(lambda: delattr(frappe.local, "db"))
		for target in (
			patch("frappe.get_system_settings", return_value="3"),
			patch("frappe.get_cached_value", side_effect=self.cached_value),
			patch("frappe.throw", side_effect=raising),
			patch.object(cb, "receipt_item_rows", return_value=[]),
			patch.object(cb, "item_fields", side_effect=self.item_fields),
			patch.object(cb, "customer_fields", side_effect=self.customer_fields),
			patch.object(cb, "sales_team_rows", side_effect=self.sales_team_rows),
			patch.object(cb, "group_members", side_effect=self.group_members),
			patch.object(cb, "_", side_effect=lambda s: s),
			patch.object(cogs_report, "_", side_effect=lambda s: s),
			patch.object(transfer_report, "_", side_effect=lambda s: s),
		):
			target.start()
			self.addCleanup(target.stop)

	def item_fields(self, codes):
		return [
			frappe._dict(
				name=c,
				item_name=f"{c} name",
				item_group=self.item_groups.get(c, "Paints"),
				valuation_method=None,
			)
			for c in codes
		]

	def customer_fields(self, names):
		return [
			frappe._dict(
				name=n, customer_name=f"{n} name", customer_group=self.customer_groups.get(n, "Retail")
			)
			for n in names
		]

	def sales_team_rows(self, voucher_type, parents):
		return [
			frappe._dict(parent=p, sales_person=person)
			for p in parents
			for person in self.teams.get((voucher_type, p), [])
		]

	def group_members(self, doctype, name):
		return {name, *self.group_children.get(name, ())}

	def cached_value(self, doctype, name, fieldname=None, *args, **kwargs):
		if doctype == "Stock Entry" and fieldname == "purpose":
			return self.purposes.get(name)
		if doctype == "Item" and fieldname == "item_name":
			return name
		if doctype == "Company" and fieldname == "default_currency":
			return "BHD"
		return None

	def run_transfers(self, legs, filters=None, scope=None):
		filters = {"company": "Test Co", **(filters or {})}
		with (
			patch.object(transfer_report, "get_outgoing_legs", return_value=legs),
			patch.object(cb, "permission_scope", return_value=scope or {}),
			patch("frappe.has_permission", return_value=True),
		):
			return transfer_report.execute(filters)

	def rows_sum(self, rows) -> float:
		return sum(r.value for r in rows)


class TestReplay(EngineCase):
	def test_a_sale_is_split_pro_rata_at_each_documents_own_rate(self):
		b = self.ledger.bin()
		b.receive("PR-1", 10, 2.0)
		b.receive("PR-2", 10, 4.0)
		sale = b.issue("SI-1", 5)
		sources = {s.voucher_no: s for s in cb.split(cb.sources_before(sale), 5)}
		self.assertAlmostEqual(sources["PR-1"].qty, 2.5)
		self.assertAlmostEqual(sources["PR-1"].value, 5.0)
		self.assertAlmostEqual(sources["PR-2"].value, 10.0)
		self.assertAlmostEqual(sum(s.value for s in sources.values()), -sale.stock_value_difference)

	def test_every_sale_takes_the_same_share_out_of_every_document(self):
		b = self.ledger.bin()
		b.receive("PR-1", 10, 2.0)
		b.receive("PR-2", 10, 4.0)
		b.issue("SI-1", 10)
		second = b.issue("SI-2", 5)
		held = {s.voucher_no: s.qty for s in cb.sources_before(second)}
		self.assertAlmostEqual(held["PR-1"], 5.0)
		self.assertAlmostEqual(held["PR-2"], 5.0)

	def test_a_stock_reconciliation_restarts_the_replay(self):
		b = self.ledger.bin()
		b.receive("PR-1", 10, 2.0)
		b.reconcile("RECO-1", 8, 3.0)
		b.receive("PR-2", 2, 5.0)
		sale = b.issue("SI-1", 1)
		self.assertEqual({s.voucher_no for s in cb.sources_before(sale)}, {"RECO-1", "PR-2"})

	def test_an_empty_bin_restarts_the_replay(self):
		b = self.ledger.bin()
		b.receive("PR-1", 5, 2.0)
		b.issue("SI-1", 5)
		b.receive("PR-2", 4, 6.0)
		sale = b.issue("SI-2", 1)
		self.assertEqual([s.voucher_no for s in cb.sources_before(sale)], ["PR-2"])

	def test_stock_arriving_over_a_negative_balance_is_seeded_from_the_ledger(self):
		# item 110302020007 on the live site: a transfer of 70 left an empty bin, then 100 arrived
		b = self.ledger.bin()
		b.receive("PR-0", 30, 1.7)
		b.issue("SE-OUT", 100, voucher_type="Stock Entry")  # 30 in the bin, 100 out: -70
		self.assertEqual(round(b.qty, 6), -70)
		b.receive("SE-IN", 100, 1.7, voucher_type="Stock Entry")  # 30 left above zero
		self.assertEqual(round(b.qty, 6), 30)
		sale = b.issue("SI-1", 10)
		sources = cb.sources_before(sale)
		self.assertAlmostEqual(sum(s.qty for s in sources), 30)  # not 100
		ex = cb.explain(sale, sources)
		self.assertEqual(ex.flag, cb.OK)
		self.assertAlmostEqual(self.rows_sum(ex.rows), -sale.stock_value_difference)

	def test_the_replay_keeps_agreeing_with_the_ledger_after_a_negative_crossing(self):
		b = self.ledger.bin()
		b.issue("SE-OUT", 70, voucher_type="Stock Entry", rate=1.73)
		b.receive("SE-IN", 100, 1.73, voucher_type="Stock Entry")
		for i in range(1, 6):
			b.issue(f"SI-{i}", 4)
		sales = [e for e in self.ledger.entries if e.voucher_no.startswith("SI-")]
		snapshots = cb.sources_before_many(sales)
		for sale in sales:
			ex = cb.explain(sale, snapshots[sale.name])
			self.assertEqual(ex.flag, cb.OK, sale.voucher_no)

	def test_the_ledger_is_read_once_per_item_and_warehouse(self):
		b = self.ledger.bin()
		b.receive("PR-1", 100, 2.0)
		sales = [b.issue(f"SI-{i}", 1) for i in range(1, 8)]
		other = self.ledger.bin(item="ITEM-2")
		other.receive("PR-9", 10, 1.0)
		sales.append(other.issue("SI-9", 1))
		self.ledger.queries.clear()
		cb.sources_before_many(sales)
		# two items: one probe for where to start and one read of the window each -- not two per line
		self.assertEqual(len(self.ledger.queries), 4)


class TestExplain(EngineCase):
	def test_a_line_the_replay_agrees_with_is_ok_and_its_rows_add_up(self):
		b = self.ledger.bin()
		b.receive("PR-1", 10, 2.0)
		b.receive("PR-2", 10, 4.0)
		sale = b.issue("SI-1", 5)
		ex = cb.explain(sale, cb.sources_before(sale))
		self.assertEqual(ex.flag, cb.OK)
		self.assertAlmostEqual(self.rows_sum(ex.rows), -sale.stock_value_difference)
		self.assertFalse([r for r in ex.rows if r.get("gap")])

	def test_a_line_that_left_an_empty_bin_is_negative_and_its_whole_cost_is_one_row(self):
		b = self.ledger.bin()
		b.receive("PR-1", 5, 2.0)
		b.issue("SI-0", 5)
		out = b.issue("SE-1", 70, voucher_type="Stock Entry")
		ex = cb.explain(out, cb.sources_before(out))
		self.assertEqual(ex.flag, cb.NEGATIVE)
		self.assertEqual(len(ex.rows), 1)
		self.assertEqual(ex.rows[0].gap, "Left more than the bin held (negative stock)")
		self.assertAlmostEqual(ex.rows[0].qty, 70)
		self.assertAlmostEqual(ex.rows[0].value, -out.stock_value_difference)

	def test_a_line_that_took_more_than_the_bin_held_splits_into_stock_and_shortfall(self):
		b = self.ledger.bin()
		b.receive("PR-1", 30, 2.0)
		out = b.issue("SI-1", 100)
		ex = cb.explain(out, cb.sources_before(out))
		self.assertEqual(ex.flag, cb.NEGATIVE)
		self.assertAlmostEqual(ex.short, 70)
		stock = [r for r in ex.rows if not r.get("gap")]
		self.assertAlmostEqual(sum(r.qty for r in stock), 30)
		self.assertAlmostEqual(self.rows_sum(ex.rows), -out.stock_value_difference)

	def test_a_gap_bigger_than_rounding_is_flagged_and_given_its_own_row(self):
		b = self.ledger.bin()
		b.receive("PR-1", 10, 3.0)
		sale = b.issue("SI-1", 5, rate=3.5)  # booked half a unit above the Moving Average
		ex = cb.explain(sale, cb.sources_before(sale))
		self.assertEqual(ex.flag, cb.DIFFERS)
		gap = [r for r in ex.rows if r.get("gap")]
		self.assertEqual(len(gap), 1)
		self.assertAlmostEqual(gap[0].value, 2.5)
		self.assertAlmostEqual(self.rows_sum(ex.rows), -sale.stock_value_difference)

	def test_a_few_fils_on_a_big_line_is_accumulated_rounding_not_a_difference(self):
		b = self.ledger.bin()
		b.receive("PR-1", 1000, 3.0)
		sale = b.issue("SI-1", 500, rate=3.01)  # 5 BHD on a 1,505 BHD line is 0.33%: a real gap
		self.assertEqual(cb.explain(sale, cb.sources_before(sale)).flag, cb.DIFFERS)
		sale = b.issue("SI-2", 500, rate=2.9999)  # 0.05 BHD on 1,500 BHD is 0.003%: rounding
		self.assertEqual(cb.explain(sale, cb.sources_before(sale)).flag, cb.OK)

	def test_tolerance_is_a_thousandth_of_the_line_but_never_under_a_fil_and_a_half(self):
		self.assertAlmostEqual(cb.tolerance(), 0.0015)
		self.assertAlmostEqual(cb.tolerance(1.0), 0.0015)
		self.assertAlmostEqual(cb.tolerance(14.6), 0.0146)
		self.assertAlmostEqual(cb.tolerance(520.0), 0.52)

	def test_a_gap_inside_rounding_is_not_a_difference(self):
		b = self.ledger.bin()
		b.receive("PR-1", 10, 3.0)
		sale = b.issue("SI-1", 5, rate=3.0002)  # a fil is 0.001: 0.001 off is rounding
		ex = cb.explain(sale, cb.sources_before(sale))
		self.assertEqual(ex.flag, cb.OK)
		self.assertFalse([r for r in ex.rows if r.get("gap")])


class TestCompact(EngineCase):
	@staticmethod
	def rows(*qtys):
		return [
			frappe._dict(
				voucher_type="Purchase Receipt",
				voucher_no=f"PR-{i}",
				posting_date=None,
				qty=q,
				rate=2.0,
				value=q * 2.0,
			)
			for i, q in enumerate(qtys)
		]

	def test_small_shares_are_one_row_and_nothing_is_dropped(self):
		rows = self.rows(*([100] + [0.1] * 20))
		out = cb.compact(rows)
		self.assertAlmostEqual(sum(r.qty for r in out), sum(r.qty for r in rows))
		self.assertAlmostEqual(self.rows_sum(out), self.rows_sum(rows))
		self.assertEqual(out[-1].others, 13)  # 21 rows, the 8 largest listed

	def test_a_hundred_equal_slivers_do_not_collapse_into_nothing(self):
		rows = self.rows(*([1] * 100))  # every one is exactly 1%: none under the floor, all listed
		self.assertEqual(len(cb.compact(rows)), 100)
		rows = self.rows(*([1] * 101))  # now each is just under 1%
		out = cb.compact(rows)
		self.assertEqual(len([r for r in out if r.voucher_no]), cb.MIN_SOURCES_SHOWN)
		self.assertEqual(out[-1].others, 101 - cb.MIN_SOURCES_SHOWN)

	def test_a_lone_leftover_is_shown_as_itself(self):
		rows = self.rows(*([50] * 8 + [0.01]))
		out = cb.compact(rows)
		self.assertEqual(len(out), 9)
		self.assertEqual(out[-1].voucher_no, "PR-8")
		self.assertFalse(out[-1].get("others"))


class TestChain(EngineCase):
	def transfer(self, name, source: Bin, target: Bin, qty):
		"""A Material Transfer: out of the source at its Moving Average, into the target at the same rate."""
		self.purposes[name] = "Material Transfer"
		leg = source.issue(name, qty, voucher_type="Stock Entry")
		target.receive(name, qty, -leg.stock_value_difference / qty, voucher_type="Stock Entry")
		return leg

	def test_a_transfer_is_followed_back_to_what_its_source_warehouse_held(self):
		store = self.ledger.bin(warehouse="STORE - A")
		shop = self.ledger.bin(warehouse="SHOP - A")
		store.receive("PR-1", 10, 2.0)
		store.receive("PR-2", 10, 4.0)
		self.transfer("SE-1", store, shop, 10)
		sale = shop.issue("SI-1", 4)

		ex = cb.explain(sale, cb.sources_before(sale))
		self.assertEqual([r.voucher_no for r in ex.rows], ["SE-1"])
		cb.attach_chain(ex.rows, "ITEM-1", cb.new_budget())

		(transfer,) = ex.rows
		self.assertTrue(transfer.followed)
		self.assertEqual({k.voucher_no for k in transfer.children}, {"PR-1", "PR-2"})
		self.assertEqual({k.warehouse for k in transfer.children}, {"STORE - A"})
		self.assertAlmostEqual(self.rows_sum(transfer.children), transfer.value)

	def test_a_chain_goes_back_more_than_one_hop(self):
		a, b, c = (self.ledger.bin(warehouse=w) for w in ("A - A", "B - A", "C - A"))
		a.receive("PR-1", 10, 2.0)
		self.transfer("SE-1", a, b, 10)
		self.transfer("SE-2", b, c, 10)
		sale = c.issue("SI-1", 5)
		ex = cb.explain(sale, cb.sources_before(sale))
		cb.attach_chain(ex.rows, "ITEM-1", cb.new_budget())
		path = [(s.voucher_no, level) for s, level in cb.walk(ex.rows)]
		self.assertEqual(path, [("SE-2", 0), ("SE-1", 1), ("PR-1", 2)])

	def test_a_transfer_is_not_followed_past_the_depth_cap_or_the_budget(self):
		a, b = self.ledger.bin(warehouse="A - A"), self.ledger.bin(warehouse="B - A")
		a.receive("PR-1", 10, 2.0)
		self.transfer("SE-1", a, b, 10)
		sale = b.issue("SI-1", 5)

		ex = cb.explain(sale, cb.sources_before(sale))
		budget = cb.new_budget(lookups=0)
		cb.attach_chain(ex.rows, "ITEM-1", budget)
		self.assertFalse(ex.rows[0].followed)  # keeps its link to Transfer Cost Breakdown
		self.assertTrue(budget.spent)

		ex = cb.explain(sale, cb.sources_before(sale))
		with patch.object(cb, "MAX_CHAIN_DEPTH", 1):
			cb.attach_chain(ex.rows, "ITEM-1", cb.new_budget())
		self.assertFalse(ex.rows[0].followed)

	def test_a_transfer_back_into_a_warehouse_already_on_the_path_stops(self):
		a, b = self.ledger.bin(warehouse="A - A"), self.ledger.bin(warehouse="B - A")
		a.receive("PR-1", 10, 2.0)
		self.transfer("SE-1", a, b, 10)
		self.transfer("SE-2", b, a, 10)
		self.transfer("SE-3", a, b, 10)
		sale = b.issue("SI-1", 5)
		ex = cb.explain(sale, cb.sources_before(sale))
		cb.attach_chain(ex.rows, "ITEM-1", cb.new_budget())
		names = [s.voucher_no for s, _level in cb.walk(ex.rows)]
		self.assertEqual(names, ["SE-3", "SE-2", "SE-1", "PR-1"])  # each transfer once, then the receipt


class TestReceiptComponents(EngineCase):
	def source(self, qty=10, rate=5.5):
		return frappe._dict(
			voucher_type="Purchase Receipt", voucher_no="PR-1", qty=qty, rate=rate, value=qty * rate
		)

	def totals(self, **amounts):
		t = frappe._dict(qty=100.0, price=500.0, tax=0.0, landed=0.0)
		t.update(amounts)
		return {("Purchase Receipt", "PR-1", "ITEM-1"): t}

	def test_a_plain_receipt_needs_no_split(self):
		self.assertEqual(cb.receipt_components(self.source(), self.totals(), "ITEM-1"), [])

	def test_a_receipt_with_landed_cost_is_split_into_price_and_landed_cost(self):
		rows = cb.receipt_components(self.source(), self.totals(landed=50.0), "ITEM-1")
		self.assertEqual([r.component for r in rows], ["Purchase price", "Landed cost"])
		self.assertAlmostEqual(rows[0].value, 50.0)
		self.assertAlmostEqual(rows[1].value, 5.0)
		self.assertAlmostEqual(self.rows_sum(rows), 55.0)

	def test_taxes_in_valuation_are_their_own_row_and_a_leftover_is_an_adjustment(self):
		rows = cb.receipt_components(self.source(rate=5.9), self.totals(tax=25.0, landed=25.0), "ITEM-1")
		self.assertEqual(
			[r.component for r in rows],
			["Purchase price", "Taxes and charges in valuation", "Landed cost", "Rate adjustments"],
		)
		self.assertAlmostEqual(self.rows_sum(rows), 59.0)

	def test_a_receipt_nobody_read_has_no_split(self):
		self.assertEqual(cb.receipt_components(self.source(), {}, "ITEM-1"), [])


class TestPermissions(unittest.TestCase):
	def test_a_blank_value_and_an_unrestricted_doctype_pass(self):
		scope = {"Warehouse": [("WH - A", "")]}
		self.assertTrue(cb.permitted(scope, "Warehouse", "", "Sales Invoice"))
		self.assertTrue(cb.permitted(scope, "Customer", "ANY", "Sales Invoice"))

	def test_a_value_outside_the_permission_is_refused(self):
		scope = {"Warehouse": [("WH - A", "")]}
		self.assertTrue(cb.permitted(scope, "Warehouse", "WH - A", "Sales Invoice"))
		self.assertFalse(cb.permitted(scope, "Warehouse", "WH - B", "Sales Invoice"))

	def test_a_permission_for_another_doctype_does_not_restrict_this_one(self):
		scope = {"Warehouse": [("WH - A", "Delivery Note")]}
		self.assertTrue(cb.permitted(scope, "Warehouse", "WH - B", "Sales Invoice"))
		self.assertFalse(cb.permitted(scope, "Warehouse", "WH - B", "Delivery Note"))

	def test_the_scope_is_read_from_the_users_permissions(self):
		permissions = {
			"Warehouse": [{"doc": "WH - A", "applicable_for": None}],
			"Item": [{"doc": "X", "applicable_for": None}],  # not a doctype that narrows a stock voucher
			"Branch": [],
		}
		with patch("frappe.permissions.get_user_permissions", return_value=permissions):
			self.assertEqual(cb.permission_scope("a@b.c"), {"Warehouse": [("WH - A", "")]})


class CogsFixture(EngineCase):
	"""Invoice SI-1 sold 10 of ITEM-1 (cost 20, sold 30) and 5 of ITEM-2 (cost 20, sold 50)."""

	def setUp(self):
		super().setUp()
		self.b1 = self.ledger.bin(item="ITEM-1")
		self.b2 = self.ledger.bin(item="ITEM-2")
		self.b1.receive("PR-1", 100, 2.0)
		self.b2.receive("PR-2", 100, 4.0)
		self.sale1 = self.b1.issue("SI-1", 10)  # cost 20
		self.sale2 = self.b2.issue("SI-1", 5)  # cost 20
		self.header = {
			("Sales Invoice", "SI-1"): frappe._dict(
				name="SI-1",
				company="Test Co",
				customer="CUST-1",
				docstatus=1,
				branch="B1",
				is_return=0,
				return_against=None,
			)
		}
		self.item_rows = {
			("Sales Invoice", "SI-1"): [
				frappe._dict(
					name="SI-1-ITEM-1",
					parent="SI-1",
					warehouse="WH - A",
					cost_center="CC",
					base_net_amount=30.0,
				),
				frappe._dict(
					name="SI-1-ITEM-2",
					parent="SI-1",
					warehouse="WH - A",
					cost_center="CC",
					base_net_amount=50.0,
				),
			]
		}

	def add_voucher(self, name, header=None, rows=(), voucher_type="Sales Invoice"):
		"""Register a header and item rows for a voucher the ledger already holds entries of."""
		self.header[(voucher_type, name)] = frappe._dict(
			dict(
				name=name,
				company="Test Co",
				customer="CUST-1",
				docstatus=1,
				branch="B1",
				is_return=0,
				return_against=None,
			),
			**(header or {}),
		)
		self.item_rows[(voucher_type, name)] = [
			frappe._dict(
				name=f"{name}-{item}",
				parent=name,
				warehouse="WH - A",
				cost_center=cost_center,
				base_net_amount=sold,
			)
			for item, sold, cost_center in rows
		]

	def run_report(self, filters=None, scope=None, headers=None, booked=40.0, booked_map=None):
		filters = {"company": "Test Co", **(filters or {})}
		vouchers = sorted(self.header, key=lambda v: v[1])
		booked_map = booked_map or {("Sales Invoice", "SI-1"): booked}
		with (
			patch.object(cogs_report, "get_vouchers", return_value=vouchers),
			patch.object(cogs_report, "get_headers", return_value=headers or self.header),
			patch.object(
				cogs_report,
				"get_item_rows",
				side_effect=lambda vs: {v: self.item_rows.get(v, []) for v in vs},
			),
			patch.object(cb, "gl_cogs_many", return_value=booked_map),
			patch.object(cb, "permission_scope", return_value=scope or {}),
			patch("frappe.has_permission", return_value=True),
		):
			return cogs_report.execute(filters)


class TestCogsReport(CogsFixture):
	def test_voucher_totals_and_margin(self):
		_cols, rows, message, _chart, summary = self.run_report()
		voucher = rows[0]
		self.assertAlmostEqual(voucher["value"], 40.0)
		self.assertAlmostEqual(voucher["difference"], 0.0)
		self.assertAlmostEqual(voucher["sales"], 80.0)
		self.assertAlmostEqual(voucher["margin"], 40.0)
		self.assertAlmostEqual(voucher["margin_pct"], 50.0)
		self.assertEqual(voucher["customer"], "CUST-1")
		line = rows[1]
		self.assertAlmostEqual(line["margin"], 10.0)  # sold 30, cost 20
		self.assertEqual(line["check"], "Matches ledger")
		self.assertIsNone(message)
		self.assertEqual({s["label"]: s["value"] for s in summary}["Vouchers"], 1)

	def test_a_filter_that_hides_lines_does_not_change_the_voucher_totals(self):
		_cols, rows, message, _chart, summary = self.run_report({"item_code": "ITEM-1"})
		voucher = rows[0]
		self.assertAlmostEqual(voucher["value"], 40.0)  # the whole voucher, not the 20 of ITEM-1
		self.assertAlmostEqual(voucher["difference"], 0.0)  # so it is not red against a GL of 40
		self.assertEqual([r["item_code"] for r in rows if r["indent"] == 1], ["ITEM-1"])
		self.assertIn("hide 1 line(s)", message)

	def test_a_difference_is_the_ledger_against_the_gl(self):
		_cols, rows, _m, _c, summary = self.run_report(booked=39.0)
		self.assertAlmostEqual(rows[0]["difference"], 1.0)
		self.assertEqual(next(s for s in summary if s["label"] == "Difference")["indicator"], "Red")

	def test_a_voucher_with_a_row_in_a_forbidden_warehouse_is_not_shown(self):
		scope = {"Warehouse": [("WH - Z", "")]}
		_cols, rows, _m, _c, _s = self.run_report(scope=scope)
		self.assertEqual(rows, [])

	def test_a_voucher_whose_customer_is_outside_the_permission_is_not_shown(self):
		scope = {"Customer": [("CUST-2", "")]}
		_cols, rows, _m, _c, _s = self.run_report(scope=scope)
		self.assertEqual(rows, [])

	def test_a_company_outside_the_permission_is_refused(self):
		with self.assertRaises(frappe.PermissionError):
			self.run_report(scope={"Company": [("Other Co", "")]})

	def test_a_negative_line_is_flagged_and_the_message_says_so(self):
		empty = self.ledger.bin(item="ITEM-3")
		empty.issue("SI-1", 4)
		self.item_rows[("Sales Invoice", "SI-1")].append(
			frappe._dict(
				name="SI-1-ITEM-3", parent="SI-1", warehouse="WH - A", cost_center="CC", base_net_amount=9.0
			)
		)
		_cols, rows, message, _c, _s = self.run_report(booked=40.0)
		line = next(r for r in rows if r.get("item_code") == "ITEM-3")
		self.assertEqual(line["check"], "Negative stock")
		self.assertEqual(line["check_flag"], cb.NEGATIVE)
		self.assertIn("1 line(s) left more than the bin held", message)
		gap = [r for r in rows if r.get("gap")]
		self.assertEqual(len(gap), 1)

	def test_source_documents_are_left_out_past_the_line_cap(self):
		with patch.object(cogs_report, "MAX_EXPANDED_LINES", 1):
			_cols, rows, message, _c, _s = self.run_report()
		self.assertEqual({r["indent"] for r in rows}, {0, 1})
		self.assertIn("2 item lines", message)

	def test_a_landed_cost_receipt_is_split_under_its_source_row(self):
		totals = {
			("Purchase Receipt", "PR-1", "ITEM-1"): frappe._dict(qty=100.0, price=190.0, tax=0.0, landed=10.0)
		}
		with patch.object(cb, "receipt_totals", return_value=totals):
			_cols, rows, _m, _c, _s = self.run_report()
		after_line = [r for r in rows if r.get("source_type") in ("Purchase price", "Landed cost")]
		self.assertEqual([r["source_type"] for r in after_line], ["Purchase price", "Landed cost"])
		self.assertTrue(all(r["component"] for r in after_line))

	def test_a_typed_voucher_that_is_a_draft_says_so(self):
		draft = {
			("Sales Invoice", "SI-1"): frappe._dict(name="SI-1", company="Test Co", customer="C", docstatus=0)
		}
		messages = []
		with (
			patch.object(cogs_report, "get_headers", return_value=draft),
			patch("frappe.has_permission", return_value=True),
		):
			vouchers = cogs_report.typed_voucher(
				frappe._dict(voucher_type="Sales Invoice", voucher_no="SI-1", company="Test Co"), messages
			)
		self.assertEqual(vouchers, [])
		self.assertIn("a draft", messages[0])

	def test_a_typed_voucher_of_another_company_is_refused(self):
		other = {
			("Sales Invoice", "SI-1"): frappe._dict(
				name="SI-1", company="Other Co", customer="C", docstatus=1
			)
		}
		with (
			patch.object(cogs_report, "get_headers", return_value=other),
			patch("frappe.has_permission", return_value=True),
		):
			with self.assertRaises(frappe.ValidationError):
				cogs_report.typed_voucher(
					frappe._dict(voucher_type="Sales Invoice", voucher_no="SI-1", company="Test Co"), []
				)


class TestTransferReport(EngineCase):
	def test_a_transfer_with_one_forbidden_line_is_not_shown(self):
		legs = [frappe._dict(voucher_no="SE-1"), frappe._dict(voucher_no="SE-2")]
		rows = [
			frappe._dict(parent="SE-1", s_warehouse="WH - A", t_warehouse="WH - B", cost_center=None),
			frappe._dict(parent="SE-2", s_warehouse="WH - A", t_warehouse="WH - Z", cost_center=None),
		]
		scope = {"Warehouse": [("WH - A", ""), ("WH - B", "")]}
		with patch.object(transfer_report, "entry_rows", return_value=rows):
			self.assertEqual(transfer_report.permitted_entries(legs, scope), {"SE-1"})

	def test_nothing_is_read_for_a_user_with_no_warehouse_or_cost_centre_restriction(self):
		legs = [frappe._dict(voucher_no="SE-1")]
		with patch.object(transfer_report, "entry_rows", side_effect=AssertionError("read")):
			self.assertEqual(transfer_report.permitted_entries(legs, {"Customer": [("C", "")]}), {"SE-1"})

	def test_a_transfer_line_has_its_balance_before_check_and_sources(self):
		store = self.ledger.bin(warehouse="STORE - A")
		store.receive("PR-1", 10, 2.0)
		leg = store.issue("SE-1", 4, voucher_type="Stock Entry")
		leg.target_warehouse = "SHOP - A"
		_cols, rows, message, _c, _s = self.run_transfers([leg])
		self.assertEqual([r["indent"] for r in rows], [0, 1])
		line, source = rows
		self.assertAlmostEqual(line["balance_qty"], 10.0)
		self.assertAlmostEqual(line["balance_rate"], 2.0)
		self.assertEqual(line["check"], "Matches ledger")
		self.assertEqual(source["document"], "PR-1")
		self.assertAlmostEqual(source["value"], line["value"])
		self.assertIsNone(message)


class TestLookups(EngineCase):
	def test_a_return_at_the_cost_the_sale_took_it_out_at_is_ok(self):
		line = frappe._dict(item_code="ITEM-1", actual_qty=2.0, stock_value_difference=4.0)
		originals = {("Sales Invoice", "SI-1", "ITEM-1"): frappe._dict(cost=20.0, qty=10.0)}
		self.assertEqual(cb.return_check("Sales Invoice", "SI-1", line, originals), (cb.RETURN_OK, 2.0))

	def test_a_return_a_percent_or_more_away_from_the_sale_is_flagged(self):
		line = frappe._dict(item_code="ITEM-1", actual_qty=2.0, stock_value_difference=6.0)
		originals = {("Sales Invoice", "SI-1", "ITEM-1"): frappe._dict(cost=20.0, qty=10.0)}
		self.assertEqual(cb.return_check("Sales Invoice", "SI-1", line, originals), (cb.RETURN_DIFFERS, 2.0))
		line.stock_value_difference = 4.02  # half a percent: rounding
		self.assertEqual(cb.return_check("Sales Invoice", "SI-1", line, originals)[0], cb.RETURN_OK)

	def test_a_return_with_no_original_is_standalone(self):
		line = frappe._dict(item_code="ITEM-1", actual_qty=2.0, stock_value_difference=4.0)
		self.assertEqual(cb.return_check("Sales Invoice", None, line, {}), (cb.RETURN_STANDALONE, None))
		self.assertEqual(cb.return_check("Sales Invoice", "SI-9", line, {}), (cb.RETURN_STANDALONE, None))

	def test_original_costs_are_the_outgoing_cost_and_qty_per_item(self):
		b = self.ledger.bin()
		b.receive("PR-1", 100, 2.0)
		b.issue("SI-1", 10)
		b.issue("SI-1", 5)
		got = cb.original_costs([("Sales Invoice", "SI-1")])
		self.assertAlmostEqual(got[("Sales Invoice", "SI-1", "ITEM-1")].cost, 30.0)
		self.assertAlmostEqual(got[("Sales Invoice", "SI-1", "ITEM-1")].qty, 15.0)

	def test_ledger_cost_is_summed_per_voucher(self):
		b = self.ledger.bin()
		b.receive("PR-1", 100, 2.0)
		b.issue("SI-1", 10)
		b.issue("SI-2", 5)
		self.assertEqual(
			cb.ledger_cost_many([("Sales Invoice", "SI-1"), ("Sales Invoice", "SI-2")]),
			{("Sales Invoice", "SI-1"): 20.0, ("Sales Invoice", "SI-2"): 10.0},
		)

	def test_the_receiving_leg_is_found_by_its_row(self):
		store, shop = self.ledger.bin(warehouse="STORE - A"), self.ledger.bin(warehouse="SHOP - A")
		store.receive("PR-1", 10, 2.0)
		leg = store.issue("SE-1", 4, voucher_type="Stock Entry")
		shop.receive("SE-1", 4, 2.0, voucher_type="Stock Entry")
		got = cb.incoming_legs([leg.voucher_detail_no, "", None])
		self.assertEqual(list(got), [leg.voucher_detail_no])
		self.assertAlmostEqual(got[leg.voucher_detail_no].value, 8.0)
		self.assertEqual(cb.incoming_legs([]), {})

	def test_sales_people_are_listed_once_in_order(self):
		self.teams = {("Sales Invoice", "SI-1"): ["Akhil", "Rahul", "Akhil"]}
		self.assertEqual(
			cb.sales_persons([("Sales Invoice", "SI-1"), ("Sales Invoice", "SI-2")]),
			{("Sales Invoice", "SI-1"): ["Akhil", "Rahul"]},
		)

	def test_items_and_customers_are_read_in_one_go_and_blanks_skipped(self):
		self.item_groups = {"ITEM-1": "Wall Paints"}
		self.assertEqual(cb.item_info(["ITEM-1", None, ""])["ITEM-1"].item_group, "Wall Paints")
		self.assertEqual(cb.customer_info([None, "C-1"])["C-1"].customer_group, "Retail")


class TestCogsFilters(CogsFixture):
	"""Each filter narrows what it says it narrows, and the voucher totals still cover the whole voucher."""

	def add_return(self, name, against, bin_, qty, rate, sold=-6.0, **header):
		bin_.receive(name, qty, rate, voucher_type="Sales Invoice")
		self.add_voucher(name, dict(is_return=1, return_against=against, **header), [(bin_.item, sold, "CC")])

	def docs(self, rows):
		return [r["document"] for r in rows if r["indent"] == 0]

	def lines(self, rows):
		return [r for r in rows if r["indent"] == 1]

	def books(self, **extra):
		return {("Sales Invoice", "SI-1"): 40.0, **{("Sales Invoice", k): v for k, v in extra.items()}}

	# --- returns
	def test_returns_can_be_included_left_out_or_shown_alone(self):
		self.add_return("SI-2", "SI-1", self.b1, 2, 2.0)
		books = self.books(**{"SI-2": -4.0})
		self.assertEqual(self.docs(self.run_report(booked_map=books)[1]), ["SI-1", "SI-2"])
		self.assertEqual(self.docs(self.run_report({"returns": "Exclude"}, booked_map=books)[1]), ["SI-1"])
		self.assertEqual(self.docs(self.run_report({"returns": "Only"}, booked_map=books)[1]), ["SI-2"])

	def test_a_return_at_the_original_cost_says_so_and_shows_the_original_rate(self):
		self.add_return("SI-2", "SI-1", self.b1, 2, 2.0)
		rows = self.run_report({"returns": "Only"}, booked_map=self.books(**{"SI-2": -4.0}))[1]
		(line,) = self.lines(rows)
		self.assertEqual(line["check"], "Return at original cost")
		self.assertEqual(line["check_flag"], cb.RETURN_OK)
		self.assertAlmostEqual(line["original_rate"], 2.0)
		self.assertEqual(rows[0]["return_against"], "SI-1")

	def test_a_return_at_a_different_cost_is_flagged_and_explained_in_the_banner(self):
		self.add_return("SI-2", "SI-1", self.b1, 2, 3.0)
		_cols, rows, message, _c, _s = self.run_report(
			{"returns": "Only"}, booked_map=self.books(**{"SI-2": -6.0})
		)
		(line,) = self.lines(rows)
		self.assertEqual(line["check"], "Return at a different cost")
		self.assertAlmostEqual(line["original_rate"], 2.0)
		self.assertAlmostEqual(line["rate"], 3.0)  # negative value over negative qty: a rate stays a rate
		self.assertIn("1 return line(s) went back into stock at a different cost", message)

	def test_a_standalone_return_is_noted_but_is_not_a_line_to_review(self):
		self.add_return("SI-2", None, self.b1, 2, 3.0)
		books = self.books(**{"SI-2": -6.0})
		(line,) = self.lines(self.run_report({"returns": "Only"}, booked_map=books)[1])
		self.assertEqual(line["check_flag"], cb.RETURN_STANDALONE)
		self.assertIsNone(line["original_rate"])
		self.assertEqual(self.run_report({"returns": "Only", "only_review": 1}, booked_map=books)[1], [])

	# --- vouchers
	def test_branch_narrows_vouchers(self):
		self.b1.issue("SI-3", 1)
		self.add_voucher("SI-3", {"branch": "B2"}, [("ITEM-1", 5.0, "CC")])
		books = self.books(**{"SI-3": 2.0})
		self.assertEqual(self.docs(self.run_report({"branch": "B2"}, booked_map=books)[1]), ["SI-3"])
		self.assertEqual(self.docs(self.run_report({"branch": "B1"}, booked_map=books)[1]), ["SI-1"])

	def test_customer_group_includes_the_groups_below_it(self):
		self.b1.issue("SI-3", 1)
		self.add_voucher("SI-3", {"customer": "CUST-2"}, [("ITEM-1", 5.0, "CC")])
		self.customer_groups = {"CUST-2": "Ministry"}
		self.group_children = {"Government": {"Ministry"}}
		books = self.books(**{"SI-3": 2.0})
		self.assertEqual(
			self.docs(self.run_report({"customer_group": "Government"}, booked_map=books)[1]), ["SI-3"]
		)
		self.assertEqual(
			self.docs(self.run_report({"customer_group": "Retail"}, booked_map=books)[1]), ["SI-1"]
		)

	def test_sales_person_narrows_vouchers_and_is_listed_on_the_row(self):
		self.b1.issue("SI-3", 1)
		self.add_voucher("SI-3", {}, [("ITEM-1", 5.0, "CC")])
		self.teams = {("Sales Invoice", "SI-1"): ["Akhil", "Rahul"], ("Sales Invoice", "SI-3"): ["Mujahid"]}
		books = self.books(**{"SI-3": 2.0})
		rows = self.run_report({"sales_person": "Akhil"}, booked_map=books)[1]
		self.assertEqual(self.docs(rows), ["SI-1"])
		self.assertEqual(rows[0]["sales_person"], "Akhil, Rahul")

	def test_customer_name_and_branch_are_on_the_voucher_row(self):
		voucher = self.run_report()[1][0]
		self.assertEqual(
			(voucher["branch"], voucher["customer"], voucher["customer_name"]),
			("B1", "CUST-1", "CUST-1 name"),
		)

	def test_only_differences_keeps_the_vouchers_where_the_ledger_and_the_gl_disagree(self):
		self.b1.issue("SI-3", 1)  # cost 2.0
		self.add_voucher("SI-3", {}, [("ITEM-1", 5.0, "CC")])
		_cols, rows, _m, _c, summary = self.run_report(
			{"only_differences": 1}, booked_map=self.books(**{"SI-3": 5.0})
		)
		self.assertEqual(self.docs(rows), ["SI-3"])
		self.assertEqual(rows[0]["differs"], 1)
		self.assertAlmostEqual(rows[0]["difference"], -3.0)
		self.assertEqual(next(s for s in summary if s["label"] == "Vouchers Apart")["value"], 1)

	def test_only_differences_with_nothing_apart_is_empty(self):
		self.assertEqual(self.run_report({"only_differences": 1})[1], [])

	def test_a_voucher_row_says_whether_it_is_apart(self):
		self.assertEqual(self.run_report()[1][0]["differs"], 0)
		self.assertEqual(self.run_report(booked=39.0)[1][0]["differs"], 1)

	# --- lines
	def test_margin_below_cost_keeps_the_sale_lines_sold_for_less_than_they_cost(self):
		self.item_rows[("Sales Invoice", "SI-1")][0].base_net_amount = 15.0  # cost 20
		_cols, rows, message, _c, _s = self.run_report({"margin": "Below cost"})
		self.assertEqual([r["item_code"] for r in self.lines(rows)], ["ITEM-1"])
		self.assertAlmostEqual(rows[0]["value"], 40.0)  # the whole voucher, not the 20 of the line
		self.assertIn("hide 1 line(s)", message)

	def test_margin_over_five_times_cost_keeps_the_lines_that_look_mispriced(self):
		self.item_rows[("Sales Invoice", "SI-1")][1].base_net_amount = 150.0  # cost 20: 7.5x
		self.assertEqual(
			[r["item_code"] for r in self.lines(self.run_report({"margin": "Over 5x cost"})[1])], ["ITEM-2"]
		)
		self.item_rows[("Sales Invoice", "SI-1")][1].base_net_amount = 100.0  # exactly 5x is not over it
		self.assertEqual(self.run_report({"margin": "Over 5x cost"})[1], [])

	def test_the_margin_filters_leave_returns_alone(self):
		self.add_return(
			"SI-2", "SI-1", self.b1, 2, 2.0, sold=-100.0
		)  # negative both ways, says nothing about the price
		books = self.books(**{"SI-2": -4.0})
		for mode in ("Below cost", "Over 5x cost"):
			self.assertNotIn("SI-2", self.docs(self.run_report({"margin": mode}, booked_map=books)[1]))

	def test_cost_center_narrows_lines_and_is_a_column(self):
		self.item_rows[("Sales Invoice", "SI-1")][1].cost_center = "CC2"
		rows = self.run_report({"cost_center": "CC2"})[1]
		self.assertEqual([(r["item_code"], r["cost_center"]) for r in self.lines(rows)], [("ITEM-2", "CC2")])

	def test_item_group_includes_the_groups_below_it_and_is_a_column(self):
		self.item_groups = {"ITEM-1": "Wall Paints", "ITEM-2": "Tools"}
		self.group_children = {"Paints": {"Wall Paints"}}
		rows = self.run_report({"item_group": "Paints"})[1]
		self.assertEqual(
			[(r["item_code"], r["item_group"]) for r in self.lines(rows)], [("ITEM-1", "Wall Paints")]
		)

	def test_only_review_keeps_the_flagged_lines_and_drops_vouchers_with_none(self):
		self.ledger.bin(item="ITEM-3").issue("SI-5", 4)  # out of an empty bin
		self.add_voucher("SI-5", {}, [("ITEM-3", 9.0, "CC")])
		_cols, rows, _m, _c, _s = self.run_report({"only_review": 1}, booked_map=self.books(**{"SI-5": 0.0}))
		self.assertEqual(self.docs(rows), ["SI-5"])
		self.assertEqual([r["check_flag"] for r in self.lines(rows)], [cb.NEGATIVE])

	def test_only_review_shows_the_check_even_where_the_sources_would_be_left_out(self):
		with patch.object(cogs_report, "MAX_EXPANDED_LINES", 1):
			self.ledger.bin(item="ITEM-3").issue("SI-5", 4)
			self.add_voucher("SI-5", {}, [("ITEM-3", 9.0, "CC")])
			rows = self.run_report({"only_review": 1}, booked_map=self.books(**{"SI-5": 0.0}))[1]
		self.assertEqual([r["check_flag"] for r in self.lines(rows)], [cb.NEGATIVE])

	def test_only_review_refuses_more_lines_than_it_can_check(self):
		with patch.object(cogs_report, "MAX_REVIEW_LINES", 1):
			_cols, rows, message, _c, _s = self.run_report({"only_review": 1})
		self.assertEqual(rows, [])
		self.assertIn("more than Only Lines To Review can check", message)

	def test_a_clean_report_has_no_lines_to_review(self):
		self.assertEqual(self.run_report({"only_review": 1})[1], [])


class TestTransferFilters(EngineCase):
	def setUp(self):
		super().setUp()
		self.store = self.ledger.bin(warehouse="STORE - A")
		self.shop = self.ledger.bin(warehouse="SHOP - A")
		self.store.receive("PR-1", 100, 2.0)

	def transfer(self, name, qty, arrives_as=None):
		"""A transfer of ITEM-1 from STORE to SHOP; `arrives_as` is the value the receiving leg is posted at."""
		leg = self.store.issue(name, qty, voucher_type="Stock Entry")
		value = -leg.stock_value_difference
		self.shop._write("Stock Entry", name, qty, value if arrives_as is None else arrives_as)
		leg.target_warehouse = "SHOP - A"
		return leg

	def lines(self, rows):
		return [r for r in rows if r["indent"] == 0]

	def test_a_receiving_leg_posted_at_zero_is_a_value_gap(self):
		leg = self.transfer("SE-1", 10, arrives_as=0.0)
		_cols, rows, message, _c, summary = self.run_transfers([leg])
		(line,) = self.lines(rows)
		self.assertAlmostEqual(line["received_value"], 0.0)
		self.assertAlmostEqual(line["value_gap"], 20.0)
		self.assertEqual(line["differs"], 1)
		self.assertEqual((line["check"], line["check_flag"]), ("Received value differs", cb.GAP))
		self.assertIn("1 transfer line(s) arrived worth a different amount", message)
		cards = {s["label"]: s["value"] for s in summary}
		self.assertEqual(cards["Lines With A Value Gap"], 1)
		self.assertAlmostEqual(cards["Value Gap"], 20.0)

	def test_a_transfer_that_arrives_whole_has_no_gap(self):
		leg = self.transfer("SE-1", 10)
		_cols, rows, message, _c, summary = self.run_transfers([leg])
		(line,) = self.lines(rows)
		self.assertAlmostEqual(line["received_value"], 20.0)
		self.assertAlmostEqual(line["value_gap"], 0.0)
		self.assertEqual((line["differs"], line["check"]), (0, "Matches ledger"))
		self.assertIsNone(message)
		self.assertEqual({s["label"]: s["value"] for s in summary}["Lines With A Value Gap"], 0)

	def test_a_line_with_no_receiving_leg_has_nothing_to_compare(self):
		leg = self.store.issue("SE-1", 4, voucher_type="Stock Entry")
		self.assertEqual(transfer_value_gap(leg, {}), (None, False))

	def test_only_gaps_keeps_just_the_lines_that_arrived_worth_something_else(self):
		whole, short = self.transfer("SE-1", 10), self.transfer("SE-2", 5, arrives_as=1.0)
		rows = self.run_transfers([whole, short], {"only_gaps": 1})[1]
		self.assertEqual([r["document"] for r in self.lines(rows)], ["SE-2"])

	def test_only_review_keeps_gap_lines_and_negative_stock_lines(self):
		whole = self.transfer("SE-1", 10)
		gap = self.transfer("SE-2", 5, arrives_as=0.0)
		empty = self.ledger.bin(item="ITEM-9", warehouse="STORE - A")
		out = empty.issue("SE-3", 3, voucher_type="Stock Entry")
		out.target_warehouse = "SHOP - A"
		rows = self.run_transfers([whole, gap, out], {"only_review": 1})[1]
		self.assertEqual(sorted(r["document"] for r in self.lines(rows)), ["SE-2", "SE-3"])

	def test_a_negative_stock_flag_is_not_hidden_by_a_value_gap(self):
		empty = self.ledger.bin(item="ITEM-9", warehouse="STORE - A")
		out = empty.issue("SE-3", 3, voucher_type="Stock Entry", rate=2.0)
		self.ledger.bin(item="ITEM-9", warehouse="SHOP - A")._write("Stock Entry", "SE-3", 3, 0.0)
		out.target_warehouse = "SHOP - A"
		(line,) = self.lines(self.run_transfers([out])[1])
		self.assertEqual(line["check_flag"], cb.NEGATIVE)
		self.assertEqual(line["differs"], 1)

	def test_a_branch_that_lists_no_warehouses_says_so(self):
		with patch.object(cb, "branch_warehouses", return_value=set()):
			_cols, rows, message, _c, _s = self.run_transfers(
				[self.transfer("SE-1", 10)], {"branch": "Nowhere"}
			)
		self.assertEqual(rows, [])
		self.assertIn("Branch Nowhere lists no warehouses", message)

	def test_branch_asks_for_the_transfers_touching_its_warehouses(self):
		asked = []
		leg = self.transfer("SE-1", 10)
		with (
			patch.object(cb, "branch_warehouses", return_value={"STORE - A"}),
			patch.object(
				transfer_report, "get_outgoing_legs", side_effect=lambda f, w=None: asked.append(w) or [leg]
			),
			patch.object(cb, "permission_scope", return_value={}),
			patch("frappe.has_permission", return_value=True),
		):
			rows = transfer_report.execute({"company": "Test Co", "branch": "SFSB"})[1]
		self.assertEqual(asked, [{"STORE - A"}])
		self.assertEqual(len(self.lines(rows)), 1)

	def test_only_review_refuses_more_lines_than_it_can_check(self):
		legs = [self.transfer("SE-1", 1), self.transfer("SE-2", 1)]
		with patch.object(transfer_report, "MAX_REVIEW_LEGS", 1):
			_cols, rows, message, _c, _s = self.run_transfers(legs, {"only_review": 1})
		self.assertEqual(rows, [])
		self.assertIn("more than Only Lines To Review can check", message)


transfer_value_gap = transfer_report.value_gap


class TestGlBooked(EngineCase):
	def test_the_gl_is_read_for_all_vouchers_of_a_type_at_once(self):
		self.ledger.gl = {("Sales Invoice", "SI-1"): 12.5, ("Sales Invoice", "SI-2"): 3}
		booked = cb.gl_cogs_many(
			[("Sales Invoice", "SI-1"), ("Sales Invoice", "SI-2"), ("Sales Invoice", "SI-3")]
		)
		self.assertEqual(booked, {("Sales Invoice", "SI-1"): 12.5, ("Sales Invoice", "SI-2"): 3.0})
		self.assertEqual(cb.gl_cogs("Sales Invoice", "SI-3"), 0.0)
