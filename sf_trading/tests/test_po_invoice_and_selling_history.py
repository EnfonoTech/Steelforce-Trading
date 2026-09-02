"""Two buyer-side features: Update Stock on a PI from an unreceived PO, and Selling History.

    bench --site <site> run-tests --module sf_trading.tests.test_po_invoice_and_selling_history
"""

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import add_days, add_months, flt, nowdate

from sf_trading.api.purchase_order_invoice import make_purchase_invoice, set_update_stock
from sf_trading.api.selling_history import _branches_to_read, get_selling_history
from sf_trading.tests.test_open_items import CUSTOMER, SUPPLIER, TestOpenItems


class TestPurchaseInvoiceUpdateStock(FrappeTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		TestOpenItems.setUpClass()
		cls.company = TestOpenItems.company.name
		cls.warehouse = TestOpenItems.warehouse
		cls.cost_center = TestOpenItems.cost_center
		cls.item_code = TestOpenItems.item_code

	def make_order(self, qty=5, rate=100, item_code=None):
		po = frappe.get_doc(
			{
				"doctype": "Purchase Order",
				"company": self.company,
				"supplier": SUPPLIER,
				"transaction_date": nowdate(),
				"schedule_date": add_days(nowdate(), 3),
				"cost_center": self.cost_center,
				"items": [
					{
						"item_code": item_code or self.item_code,
						"qty": qty,
						"rate": rate,
						"warehouse": self.warehouse,
						"cost_center": self.cost_center,
						"schedule_date": add_days(nowdate(), 3),
					}
				],
			}
		)
		po.insert()
		po.submit()
		return po

	def test_an_unreceived_order_bills_with_update_stock_ticked(self):
		po = self.make_order()
		pi = make_purchase_invoice(po.name)
		self.assertEqual(int(pi.update_stock or 0), 1, "the invoice is the goods' first entry")
		self.assertTrue(all(row.warehouse for row in pi.items))

	def test_a_received_order_does_not_tick(self):
		"""The receipt already put the goods in; ticking here would count them twice."""
		from erpnext.buying.doctype.purchase_order.purchase_order import make_purchase_receipt

		po = self.make_order()
		pr = make_purchase_receipt(po.name)
		pr.insert()
		pr.submit()

		pi = make_purchase_invoice(po.name)
		self.assertEqual(int(pi.update_stock or 0), 0)

	def test_even_a_DRAFT_receipt_stops_it(self):
		from erpnext.buying.doctype.purchase_order.purchase_order import make_purchase_receipt

		po = self.make_order()
		pr = make_purchase_receipt(po.name)
		pr.insert()          # left as a draft on purpose

		pi = make_purchase_invoice(po.name)
		self.assertEqual(int(pi.update_stock or 0), 0, "somebody intends to receive these goods")

	def test_a_service_only_order_is_left_alone(self):
		from erpnext.stock.doctype.item.test_item import make_item

		# stock_uom spelled out: this site's Item defaults were cleared, so make_item cannot insert
		# without it (the same reason test_open_items spells it out)
		service = make_item(
			"SF Test Service Item",
			properties={"is_stock_item": 0, "item_group": "Products", "stock_uom": "Nos"},
		).name
		po = self.make_order(item_code=service, qty=1, rate=50)
		pi = make_purchase_invoice(po.name)
		self.assertEqual(int(pi.update_stock or 0), 0, "there is no stock to post")

	def test_an_untick_survives_the_save(self):
		"""The tick is an opening default, not a rule. A buyer who unticks it keeps that.

		This holds because the tick happens at mapping time only. Were it wired into a document
		event it would be re-applied on every save and the untick could never stick -- so the
		absence of that wiring is asserted too, not just the behaviour it produces today.
		"""
		from sf_trading import hooks

		wired = str(getattr(hooks, "doc_events", {}).get("Purchase Invoice", {}))
		self.assertNotIn("purchase_order_invoice", wired, "the tick must not run on save")

		po = self.make_order()
		pi = make_purchase_invoice(po.name)
		self.assertEqual(int(pi.update_stock or 0), 1)

		pi.update_stock = 0
		pi.insert()
		self.assertEqual(int(frappe.db.get_value("Purchase Invoice", pi.name, "update_stock") or 0), 0)

	def test_it_leaves_an_invoice_that_is_already_ticked_alone(self):
		po = self.make_order()
		pi = make_purchase_invoice(po.name)
		self.assertFalse(set_update_stock(pi), "already ticked; nothing left to do")

	def test_a_closed_order_is_refused(self):
		"""A ticked invoice reaches update_ordered_and_reserved_qty, which throws on a Closed order.

		Without this gate the box would be ticked for the buyer and the invoice would then refuse
		to submit -- a document that works today breaking because of a default nobody asked for.
		"""
		po = self.make_order()
		po.update_status("Closed")

		pi = make_purchase_invoice(po.name)
		self.assertEqual(int(pi.update_stock or 0), 0)
		po.update_status("Submitted")

	def test_an_order_already_part_received_is_refused(self):
		"""per_received moves for a receipt AND for an earlier ticked invoice; only it sees both."""
		po = self.make_order()
		frappe.db.set_value("Purchase Order", po.name, "per_received", 40)

		pi = make_purchase_invoice(po.name)
		self.assertEqual(int(pi.update_stock or 0), 0, "some of these goods are already in stock")

	def test_a_drop_ship_row_is_refused(self):
		"""The goods never enter a warehouse, and no receipt will ever arrive to stop the tick."""
		po = self.make_order()
		pi = make_purchase_invoice(po.name)
		pi.update_stock = 0
		for row in pi.items:
			row.delivered_by_supplier = 1

		self.assertFalse(set_update_stock(pi))
		self.assertEqual(int(pi.update_stock or 0), 0)

	def test_a_batch_tracked_item_is_refused(self):
		"""A ticked invoice demands a Serial and Batch Bundle nobody entered."""
		from erpnext.stock.doctype.item.test_item import make_item

		tracked = make_item(
			"SF Test Batched Item",
			properties={
				"is_stock_item": 1,
				"item_group": "Products",
				"stock_uom": "Nos",
				"has_batch_no": 1,
				"create_new_batch": 1,
				"batch_number_series": "SFTB-.####",
			},
		).name
		po = self.make_order(item_code=tracked, qty=2, rate=10)
		pi = make_purchase_invoice(po.name)
		self.assertEqual(int(pi.update_stock or 0), 0)

	def test_an_invoice_naming_no_order_is_untouched(self):
		pi = frappe.get_doc(
			{
				"doctype": "Purchase Invoice",
				"company": self.company,
				"supplier": SUPPLIER,
				"cost_center": self.cost_center,
				"items": [
					{
						"item_code": self.item_code,
						"qty": 1,
						"rate": 100,
						"warehouse": self.warehouse,
						"cost_center": self.cost_center,
					}
				],
			}
		)
		self.assertFalse(set_update_stock(pi))

	def test_the_ticked_invoice_actually_posts_stock(self):
		"""The whole point: the ledger moves when the bill is submitted."""
		po = self.make_order(qty=4, rate=100)
		pi = make_purchase_invoice(po.name)
		pi.insert()
		pi.submit()

		self.assertEqual(int(pi.update_stock or 0), 1)
		ledger = frappe.get_all(
			"Stock Ledger Entry",
			filters={"voucher_no": pi.name, "is_cancelled": 0},
			fields=["actual_qty", "warehouse"],
		)
		self.assertTrue(ledger, "an update-stock invoice must write a stock ledger entry")
		self.assertAlmostEqual(sum(flt(r.actual_qty) for r in ledger), 4, places=3)


class TestSellingHistory(FrappeTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		TestOpenItems.setUpClass()
		cls.company = TestOpenItems.company.name
		cls.cost_center = TestOpenItems.cost_center
		cls.item_code = TestOpenItems.item_code

	def sell(self, qty=2, rate=150, customer=CUSTOMER, item_code=None):
		si = frappe.get_doc(
			{
				"doctype": "Sales Invoice",
				"company": self.company,
				"customer": customer,
				"cost_center": self.cost_center,
				"items": [
					{
						"item_code": item_code or self.item_code,
						"qty": qty,
						"rate": rate,
						"warehouse": TestOpenItems.warehouse,
						"cost_center": self.cost_center,
					}
				],
			}
		)
		TestOpenItems.fill_site_mandatories(si)
		si.insert()
		si.submit()
		return si

	def test_it_reports_what_the_item_sold_for(self):
		si = self.sell(qty=2, rate=150)
		data = get_selling_history(items=[self.item_code], company=self.company)

		# the reported rate is company currency, so it is compared against the invoice's own
		# base_net_rate rather than the 150 typed in -- a customer whose price list is in another
		# currency sells at 150 of that currency, not 150 of the company's
		expected = flt(si.items[0].base_net_rate, 3)

		mine = [row for row in data["rows"] if row["invoice"] == si.name]
		self.assertEqual(len(mine), 1)
		self.assertAlmostEqual(mine[0]["qty"], 2, places=3)
		self.assertAlmostEqual(mine[0]["rate"], expected, places=2)

		summary = {row["item_code"]: row for row in data["summary"]}
		self.assertIn(self.item_code, summary)
		self.assertAlmostEqual(summary[self.item_code]["last_rate"], expected, places=2)

	def test_a_foreign_invoice_is_marked_as_one(self):
		"""Every rate is company currency; the row says so when the sale itself was not."""
		si = self.sell(qty=1, rate=150)
		data = get_selling_history(items=[self.item_code], company=self.company)
		mine = [row for row in data["rows"] if row["invoice"] == si.name][0]

		company_currency = frappe.get_cached_value("Company", self.company, "default_currency")
		if si.currency == company_currency:
			self.assertIsNone(mine["foreign"])
		else:
			self.assertEqual(mine["foreign"], si.currency)
			self.assertAlmostEqual(mine["rate"], flt(si.items[0].base_net_rate, 3), places=2)

	def test_a_credit_note_is_not_a_sale(self):
		from erpnext.controllers.sales_and_purchase_return import make_return_doc

		si = self.sell(qty=2, rate=150)
		credit = make_return_doc("Sales Invoice", si.name)
		credit.items[0].qty = -1
		TestOpenItems.fill_site_mandatories(credit)
		credit.insert()
		try:
			credit.submit()
		except frappe.ValidationError as refused:
			self.skipTest("the site's return rules refuse a direct submit: %s" % refused)

		data = get_selling_history(items=[self.item_code], company=self.company)
		self.assertFalse(
			[row for row in data["rows"] if row["invoice"] == credit.name],
			"a return is not demand and must not be shown as a sale",
		)

	def test_the_branch_filter_narrows_and_all_branches_widens(self):
		self.sell(qty=1, rate=120)
		here = get_selling_history(
			items=[self.item_code], company=self.company, cost_center=self.cost_center
		)
		self.assertTrue(all(row["cost_center"] == self.cost_center for row in here["rows"]))

		everywhere = get_selling_history(
			items=[self.item_code], company=self.company, cost_center=self.cost_center, all_branches=1
		)
		self.assertGreaterEqual(len(everywhere["rows"]), len(here["rows"]))
		self.assertIsNone(everywhere["filters"]["cost_center"])

	def test_the_window_defaults_to_a_year_and_can_be_narrowed(self):
		self.sell(qty=1, rate=130)
		data = get_selling_history(items=[self.item_code], company=self.company)
		self.assertEqual(data["filters"]["from_date"], str(add_months(nowdate(), -12)))

		empty = get_selling_history(
			items=[self.item_code],
			company=self.company,
			from_date=add_days(nowdate(), -400),
			to_date=add_days(nowdate(), -300),
		)
		self.assertFalse(empty["rows"], "nothing was sold in that window")

	def test_the_payload_names_the_currency_the_rates_are_in(self):
		"""The dialog formats with this. An order in the supplier's currency must not relabel it."""
		self.sell(qty=1, rate=140)
		company_currency = frappe.get_cached_value("Company", self.company, "default_currency")
		data = get_selling_history(items=[self.item_code], company=self.company)
		self.assertEqual(data["currency"], company_currency)
		self.assertEqual(
			get_selling_history(items=[], company=self.company)["currency"], company_currency
		)

	def test_every_item_gets_its_own_share_of_the_limit(self):
		"""A flat limit lets the busiest item eat it and reports the quiet one as never sold."""
		from erpnext.stock.doctype.item.test_item import make_item

		# non-stock on purpose: this site's invoices post stock, and the point being tested is the
		# per-item share of the limit, not whether a brand-new item has any
		quiet = make_item(
			"SF Test Quiet Item",
			properties={"is_stock_item": 0, "item_group": "Products", "stock_uom": "Nos"},
		).name
		for _ in range(3):
			self.sell(qty=1, rate=100)
		self.sell(qty=1, rate=90, item_code=quiet)

		data = get_selling_history(
			items=[self.item_code, quiet], company=self.company, limit=200
		)
		codes = {row["item_code"] for row in data["rows"]}
		self.assertIn(quiet, codes, "the quiet item must not be crowded out")
		self.assertIn(self.item_code, codes)
		self.assertGreaterEqual(data["filters"]["rows_per_item"], 10)

	def test_a_free_line_is_not_a_price(self):
		"""A zero rate is a sample, not a price, and one of them drags Lowest down to nothing.

		The row is zeroed after submit because Selling Settings on this site refuses to submit a
		rate below valuation; what is under test is the query, not how the row came to be free.
		"""
		si = self.sell(qty=1, rate=150)
		frappe.db.set_value("Sales Invoice Item", si.items[0].name, "rate", 0)

		data = get_selling_history(items=[self.item_code], company=self.company)
		self.assertFalse([row for row in data["rows"] if row["invoice"] == si.name])

	def test_the_newest_sale_of_the_day_is_the_last_rate(self):
		"""Two invoices on one date: posting_time decides, not the order rows came back in."""
		early = self.sell(qty=1, rate=111)
		frappe.db.set_value("Sales Invoice", early.name, "posting_time", "01:00:00")
		late = self.sell(qty=1, rate=222)
		frappe.db.set_value("Sales Invoice", late.name, "posting_time", "23:00:00")

		data = get_selling_history(items=[self.item_code], company=self.company)
		mine = [row for row in data["rows"] if row["invoice"] in (early.name, late.name)]
		self.assertEqual(mine[0]["invoice"], late.name, "the later invoice is reported first")

	def test_a_branch_outside_a_users_permissions_is_refused(self):
		"""frappe.qb applies no User Permissions; without this gate any branch could be named."""
		with self.assertRaises(frappe.PermissionError):
			_branches_to_read("Some Other Branch - X", 0, ["Only Mine - Y"])

		self.assertEqual(
			_branches_to_read(None, 1, ["Only Mine - Y"]),
			["Only Mine - Y"],
			"All Branches widens as far as the permissions reach, and no further",
		)
		self.assertIsNone(_branches_to_read(None, 1, None), "an unrestricted user sees every branch")
		self.assertEqual(_branches_to_read("A Branch - Z", 0, None), ["A Branch - Z"])

	def test_no_items_asks_nothing_of_the_database(self):
		self.assertEqual(get_selling_history(items=[], company=self.company)["rows"], [])
