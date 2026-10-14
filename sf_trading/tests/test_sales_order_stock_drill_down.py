"""Sales Order Stock Drill Down: item, warehouse, then the orders waiting for the stock, oldest first.

    bench --site <site> run-tests --module sf_trading.tests.test_sales_order_stock_drill_down
"""

from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import add_days, getdate, nowdate

from sf_trading import order_stock
from sf_trading.sf_trading.report.sales_order_stock_drill_down import sales_order_stock_drill_down as drill

TODAY = getdate(nowdate())
SFSB, SFSS = "SFSB - SFB", "SFSS - SFB"


def line(order, item, name, pending, warehouse=SFSB, item_name=None):
	return frappe._dict(name=name, parent=order, item_code=item, item_name=item_name or item.title(), item_group="Steel",
		stock_uom="Nos", pending_stock=pending, pending=pending, warehouse=warehouse, tracked=True)


def entry(order, detail, pending, customer, days_ago):
	return {"sales_order": order, "detail": detail, "pending": pending, "customer_name": customer,
		"transaction_date": add_days(TODAY, -days_ago)}


def bins(**pairs):
	"""bins(item=(warehouse, actual, reserved), ...) -> the (item, warehouse) map and the item map."""
	by_pair, by_item = {}, {}
	for item, rows in pairs.items():
		for warehouse, actual, reserved in rows:
			by_pair[(item, warehouse)] = frappe._dict(actual=actual, reserved=reserved, projected=0)
			by_item.setdefault(item, []).append((warehouse, actual))
	return by_pair, by_item


class TestStockSays(FrappeTestCase):
	def test_a_warehouse_against_everything_reserved_there(self):
		self.assertEqual(drill.stock_says(15, 13), drill.ENOUGH)
		self.assertEqual(drill.stock_says(13, 13), drill.ENOUGH)
		self.assertEqual(drill.stock_says(22, 40), drill.SHORT)
		self.assertEqual(drill.stock_says(0, 4), drill.NO_STOCK)
		self.assertEqual(drill.stock_says(-2, 0), drill.NO_STOCK)

	def test_an_item_from_the_warehouses_its_orders_name(self):
		self.assertEqual(drill.item_says([drill.ENOUGH, drill.ENOUGH]), drill.ENOUGH)
		self.assertEqual(drill.item_says([drill.NO_STOCK, drill.NO_STOCK]), drill.NO_STOCK)
		self.assertEqual(drill.item_says([drill.ENOUGH, drill.NO_STOCK]), drill.SHORT)
		self.assertEqual(drill.item_says([drill.SHORT]), drill.SHORT)
		# a warehouse that holds stock and is asked for nothing says nothing
		self.assertEqual(drill.item_says(["", drill.ENOUGH]), drill.ENOUGH)


class TestTheTree(FrappeTestCase):
	"""Three open orders of Steel Force on 14 October, as the stock saw them."""

	def setUp(self):
		by_pair, by_item = bins(
			**{
				"TUBE": [(SFSB, 15, 13), (SFSS, 116, 0)],
				"FLAT BAR": [(SFSB, 22, 40)],
				"CHANNEL": [(SFSB, 0, 4)],
			}
		)
		self.stock = frappe._dict(bins=by_pair, bins_by_item=by_item, warehouses={SFSB, SFSS, "SFWH - SFB"})
		self.lines = [
			line("SO-244", "TUBE", "d1", 3),
			line("SO-272", "TUBE", "d2", 10),
			line("SO-274", "FLAT BAR", "d3", 40),
			line("SO-259", "CHANNEL", "d4", 4),
		]
		self.sequence = {
			("TUBE", SFSB): order_stock.demand_in_sequence(15, [entry("SO-244", "d1", 3, "Bahrain Stainless", 4),
				entry("SO-272", "d2", 10, "Diya Steel", 2)]),
			("FLAT BAR", SFSB): order_stock.demand_in_sequence(22, [entry("SO-274", "d3", 40, "313 Contracting", 2)]),
			("CHANNEL", SFSB): order_stock.demand_in_sequence(0, [entry("SO-259", "d4", 4, "Day To Day", 4)]),
		}
		self.orders = {name: frappe._dict(name=name, customer_name=name) for name in ("SO-244", "SO-272", "SO-274", "SO-259")}
		self.reasons = {
			"SO-244": (order_stock.DRAFT_INVOICE, "Invoice saved but not submitted: 20010008906", order_stock.IN_STOCK),
			"SO-272": (order_stock.STOCK_READY, "Stock is there", order_stock.IN_STOCK),
			"SO-274": (order_stock.PART_STOCK, "FLAT BAR: 22 of 40", order_stock.PART_STOCK),
			"SO-259": (order_stock.NO_STOCK, "CHANNEL: 0 of 4", order_stock.NO_STOCK),
		}

	def tree(self, **filters):
		return drill.build_tree(self.lines, self.stock, self.sequence, self.orders, self.reasons, TODAY, frappe._dict(filters))

	def labels(self, rows, kind):
		return [row["label"] for row in rows if row["kind"] == kind]

	def test_the_items_with_stock_for_everyone_come_first(self):
		rows, _counts = self.tree()
		items = [row["label"] for row in rows if row["indent"] == 0]
		self.assertEqual(items, ["TUBE - Tube", "FLAT BAR - Flat Bar", "CHANNEL - Channel"])
		self.assertEqual([row["stock_status"] for row in rows if row["indent"] == 0], ["Enough", "Short", "No Stock"])

	def test_an_item_opens_on_its_warehouses_and_a_warehouse_on_its_orders_oldest_first(self):
		rows, _counts = self.tree()
		tube = [row for row in rows if row["node"].startswith("TUBE")]
		self.assertEqual([(row["kind"], row["indent"]) for row in tube],
			[("Item", 0), ("Warehouse", 1), ("Order", 2), ("Order", 2), ("Warehouse", 1)])
		self.assertEqual([row["label"] for row in tube], ["TUBE - Tube", SFSB, "SO-244", "SO-272", SFSS])

	def test_every_row_names_its_parent(self):
		rows, _counts = self.tree()
		nodes = {row["node"] for row in rows}
		for row in rows:
			if row["indent"] == 0:
				self.assertEqual(row["parent_node"], "")
			else:
				self.assertIn(row["parent_node"], nodes, row["node"])
		self.assertEqual(len(nodes), len(rows))

	def test_the_parent_is_always_before_its_children(self):
		rows, _counts = self.tree()
		seen = set()
		for row in rows:
			if row["parent_node"]:
				self.assertIn(row["parent_node"], seen, row["node"])
			seen.add(row["node"])

	def test_the_warehouse_row_holds_the_bins_own_figures(self):
		rows, _counts = self.tree()
		sfsb = next(row for row in rows if row["node"] == "TUBE|%s" % SFSB)
		self.assertEqual((sfsb["pending_qty"], sfsb["stock_qty"], sfsb["reserved_qty"], sfsb["free_qty"]), (13, 15, 13, 2))
		self.assertEqual((sfsb["orders"], sfsb["stock_status"]), (2, "Enough"))

	def test_a_warehouse_that_holds_the_item_and_is_asked_for_nothing_is_shown_with_no_verdict(self):
		rows, _counts = self.tree()
		sfss = next(row for row in rows if row["node"] == "TUBE|%s" % SFSS)
		self.assertEqual((sfss["stock_qty"], sfss["pending_qty"], sfss["orders"], sfss["stock_status"]), (116, 0, 0, ""))
		# a warehouse with nothing in it is not listed at all
		self.assertFalse([row for row in rows if row["node"] == "TUBE|SFWH - SFB"])

	def test_the_item_row_adds_its_warehouses_up(self):
		rows, _counts = self.tree()
		tube = next(row for row in rows if row["node"] == "TUBE")
		self.assertEqual((tube["pending_qty"], tube["stock_qty"], tube["reserved_qty"], tube["free_qty"], tube["orders"]),
			(13, 131, 13, 118, 2))
		self.assertEqual(tube["item_group"], "Steel")

	def test_an_order_row_says_what_is_left_after_it_and_whether_the_stock_covers_it(self):
		rows, _counts = self.tree()
		first, second = [row for row in rows if row["kind"] == "Order" and row["node"].startswith("TUBE")]
		self.assertEqual((first["stock_after"], first["stock_status"]), (12, "Covered"))
		self.assertEqual((second["stock_after"], second["stock_status"]), (2, "Covered"))
		flat = next(row for row in rows if row["kind"] == "Order" and row["sales_order"] == "SO-274")
		self.assertEqual((flat["stock_after"], flat["stock_status"]), (-18, "Part"))
		channel = next(row for row in rows if row["kind"] == "Order" and row["sales_order"] == "SO-259")
		self.assertEqual((channel["stock_after"], channel["stock_status"]), (-4, "Not Covered"))

	def test_an_order_row_carries_the_trackers_reason_and_how_long_it_has_waited(self):
		rows, _counts = self.tree()
		first = next(row for row in rows if row["kind"] == "Order" and row["sales_order"] == "SO-244")
		self.assertEqual(first["why_not_invoiced"], order_stock.DRAFT_INVOICE)
		self.assertIn("20010008906", first["why_detail"])
		self.assertEqual((first["customer_name"], first["waiting_days"]), ("Bahrain Stainless", 4))

	def test_the_orders_a_filter_hides_still_take_their_share_of_the_stock(self):
		# only SO-272 is shown, but SO-244 was served before it: the stock left after SO-272 is still 2
		self.lines = [line("SO-272", "TUBE", "d2", 10)]
		rows, counts = self.tree()
		orders = [row for row in rows if row["kind"] == "Order"]
		self.assertEqual([(row["sales_order"], row["stock_after"]) for row in orders], [("SO-272", 2)])
		self.assertEqual(counts["orders"], 1)

	def test_the_summary_counts(self):
		_rows, counts = self.tree()
		self.assertEqual(counts, {"items": 3, "orders": 4, "covered": 2})
		by_label = {card["label"]: card["value"] for card in drill.get_summary(counts)}
		self.assertEqual(by_label["Orders The Stock Covers"], 2)

	def test_stock_says_filters_the_items(self):
		rows, counts = self.tree(stock_status="Short")
		self.assertEqual(self.labels(rows, "Item"), ["FLAT BAR - Flat Bar"])
		self.assertEqual(counts["items"], 1)

	def test_the_warehouse_filter_shows_that_warehouse_only(self):
		self.lines = [row for row in self.lines if row.item_code == "TUBE"]
		rows, _counts = self.tree(warehouse=SFSB)
		self.assertEqual(self.labels(rows, "Warehouse"), [SFSB])

	def test_an_item_named_by_its_code_is_not_shown_twice(self):
		self.lines = [line("SO-259", "CHANNEL", "d4", 4, item_name="CHANNEL")]
		rows, _counts = self.tree()
		self.assertEqual(self.labels(rows, "Item"), ["CHANNEL"])

	def test_nothing_waiting_is_an_empty_report(self):
		rows, counts = drill.build_tree([], self.stock, {}, {}, {}, TODAY, frappe._dict())
		self.assertEqual((rows, counts), ([], {"items": 0, "orders": 0, "covered": 0}))


class TestTheReport(FrappeTestCase):
	def run_report(self, orders, **filters):
		stock = frappe._dict(lines={}, bins={}, bins_by_item={}, warehouses=set(), draft_invoices={}, draft_notes={},
			delivered_notes={}, blocks={})
		loaded = []

		def load(given, company):
			loaded.append([o.name for o in given])
			return stock

		filters.setdefault("company", "Steel Force Trading WLL")
		with patch.object(drill.tracker, "get_orders", return_value=orders), patch.object(drill.order_stock, "load", side_effect=load), \
				patch.object(drill.order_stock, "open_demand", return_value={}):
			return drill.execute(filters), loaded

	def order(self, name, status="To Deliver and Bill", per_billed=0):
		return frappe._dict(name=name, status=status, per_billed=per_billed, customer="C", customer_name="C", delivery_date=TODAY,
			transaction_date=TODAY)

	def test_a_company_is_required(self):
		with self.assertRaises(frappe.ValidationError):
			drill.execute({})

	def test_only_orders_that_hold_stock_and_are_not_billed_in_full_are_read(self):
		orders = [self.order("A"), self.order("B", status="Completed"), self.order("C", per_billed=100),
			self.order("D", status="To Deliver", per_billed=40), self.order("E", status="On Hold")]
		(_columns, rows, *_rest), loaded = self.run_report(orders)
		self.assertEqual(loaded, [["A", "D"]])
		self.assertEqual(rows, [])

	def test_a_named_order_is_read_whether_or_not_it_is_billed(self):
		(_columns, _rows, *_rest), loaded = self.run_report([self.order("C", per_billed=100)], sales_order="C")
		self.assertEqual(loaded, [["C"]])

	def test_asking_for_all_open_orders_reads_the_billed_ones_too(self):
		(_columns, _rows, *_rest), loaded = self.run_report([self.order("A"), self.order("C", per_billed=100)], only_not_invoiced=0)
		self.assertEqual(loaded, [["A", "C"]])

	def test_the_first_column_is_the_tree_and_the_helpers_are_hidden(self):
		columns = drill.get_columns()
		self.assertEqual(columns[0]["fieldname"], "label")
		hidden = {column["fieldname"] for column in columns if column.get("hidden")}
		self.assertEqual(hidden, {"sales_order", "node", "parent_node"})

	def test_the_lines_the_filters_ask_for(self):
		stock = frappe._dict(lines={"SO-1": [line("SO-1", "TUBE", "a", 1), line("SO-1", "BAR", "b", 1, warehouse=SFSS)]})
		stock.lines["SO-1"].append(frappe._dict(line("SO-1", "SERVICE", "c", 1), tracked=False))
		names = lambda found: sorted(row.name for row in found)  # noqa: E731
		self.assertEqual(names(drill.wanted_lines(stock, frappe._dict())), ["a", "b"])
		self.assertEqual(names(drill.wanted_lines(stock, frappe._dict(item_code="TUBE"))), ["a"])
		self.assertEqual(names(drill.wanted_lines(stock, frappe._dict(warehouse=SFSS))), ["b"])


class TestTheWarehouseOnEveryRow(FrappeTestCase):
	"""Each row says which warehouse it is about, so the rows read the same sorted or exported."""

	def setUp(self):
		by_pair, by_item = bins(**{"TUBE": [(SFSB, 15, 13), (SFSS, 116, 0)], "BAR": [(SFSB, 22, 40), (SFSS, 5, 0)]})
		self.stock = frappe._dict(bins=by_pair, bins_by_item=by_item, warehouses={SFSB, SFSS}, lines={})
		self.lines = [line("SO-1", "TUBE", "d1", 3), line("SO-2", "BAR", "d2", 40), line("SO-3", "BAR", "d3", 2, warehouse=SFSS)]
		self.sequence = {
			("TUBE", SFSB): order_stock.demand_in_sequence(15, [entry("SO-1", "d1", 3, "A", 1)]),
			("BAR", SFSB): order_stock.demand_in_sequence(22, [entry("SO-2", "d2", 40, "B", 2)]),
			("BAR", SFSS): order_stock.demand_in_sequence(5, [entry("SO-3", "d3", 2, "C", 1)]),
		}
		self.orders = {name: frappe._dict(name=name, customer_name=name) for name in ("SO-1", "SO-2", "SO-3")}
		self.reasons = {"SO-1": (order_stock.STOCK_READY, "d", order_stock.IN_STOCK),
			"SO-2": (order_stock.PART_STOCK, "d", order_stock.PART_STOCK), "SO-3": (order_stock.STOCK_READY, "d", order_stock.IN_STOCK)}
		self.rows, _counts = drill.build_tree(self.lines, self.stock, self.sequence, self.orders, self.reasons, TODAY, frappe._dict())

	def test_every_row_has_a_warehouse(self):
		for row in self.rows:
			self.assertTrue(row["warehouse"], row["node"])

	def test_a_warehouse_row_is_its_own_warehouse(self):
		for row in [r for r in self.rows if r["kind"] == "Warehouse"]:
			self.assertEqual(row["warehouse"], row["label"])

	def test_an_order_row_is_the_warehouse_its_line_is_delivered_from(self):
		by_order = {r["sales_order"]: r["warehouse"] for r in self.rows if r["kind"] == "Order"}
		self.assertEqual(by_order, {"SO-1": SFSB, "SO-2": SFSB, "SO-3": SFSS})

	def test_an_order_row_sits_under_its_own_warehouse_row(self):
		by_node = {r["node"]: r for r in self.rows}
		for row in [r for r in self.rows if r["kind"] == "Order"]:
			self.assertEqual(by_node[row["parent_node"]]["warehouse"], row["warehouse"])

	def test_an_item_row_lists_the_warehouses_its_orders_are_to_be_delivered_from(self):
		items = {r["node"]: r["warehouse"] for r in self.rows if r["kind"] == "Item"}
		self.assertEqual(items, {"TUBE": SFSB, "BAR": "%s, %s" % (SFSB, SFSS)})

	def test_an_order_row_says_what_to_do(self):
		by_order = {r["sales_order"]: r["next_step"] for r in self.rows if r["kind"] == "Order"}
		self.assertIn("Raise the Sales Invoice", by_order["SO-1"])
		self.assertIn("Invoice the part that is in stock", by_order["SO-2"])

	def test_the_columns_have_the_warehouse_beside_the_label_and_the_step_beside_the_reason(self):
		names = [c["fieldname"] for c in drill.get_columns()]
		self.assertEqual(names[:2], ["label", "warehouse"])
		self.assertEqual(names.index("next_step"), names.index("why_not_invoiced") + 1)
