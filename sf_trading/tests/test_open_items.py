"""Tests for the open item engine (sf_trading.open_items).

Run on a scratch site, never on a client site — the suite creates its own
company, warehouse, items and parties:

    bench --site <scratch-site> run-tests --module sf_trading.tests.test_open_items
"""

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import add_days, nowdate

from sf_trading.open_items import (
	ageing_bucket,
	billed_items_pending_receipt,
	delivered_items_pending_billing,
	invoiced_items_to_be_delivered,
	received_items_pending_billing,
)
from sf_trading.tests.test_sdbnb import ABBR, COMPANY, get_test_company

CUSTOMER = "_Test Open Items Customer"
OTHER_CUSTOMER = "_Test Open Items Customer 2"
SUPPLIER = "_Test Open Items Supplier"


class TestOpenItems(FrappeTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		cls.company = get_test_company()
		cls.warehouse = "Stores - " + ABBR
		cls.cost_center = "Main - " + ABBR
		cls.item_code = cls.make_stocked_item()
		cls.make_parties()

	@classmethod
	def make_stocked_item(cls):
		from erpnext.stock.doctype.item.test_item import make_item
		from erpnext.stock.doctype.stock_entry.test_stock_entry import make_stock_entry

		item_code = make_item(
			"Open Items Test Item", properties={"is_stock_item": 1, "item_group": "Products"}
		).name
		make_stock_entry(
			item_code=item_code, target=cls.warehouse, qty=500, basic_rate=100, company=COMPANY
		)
		return item_code

	@classmethod
	def make_parties(cls):
		for name in (CUSTOMER, OTHER_CUSTOMER):
			if not frappe.db.exists("Customer", name):
				frappe.get_doc(
					{
						"doctype": "Customer",
						"customer_name": name,
						"customer_group": "All Customer Groups",
						"territory": "All Territories",
					}
				).insert()
		if not frappe.db.exists("Supplier", SUPPLIER):
			frappe.get_doc(
				{
					"doctype": "Supplier",
					"supplier_name": SUPPLIER,
					"supplier_group": "All Supplier Groups",
				}
			).insert()

	# ------------------------------------------------------------------
	# document helpers
	# ------------------------------------------------------------------

	def filters(self, **overrides):
		base = {"company": COMPANY, "as_on": nowdate()}
		base.update(overrides)
		return frappe._dict(base)

	def rows_for(self, data, docname):
		return [row for row in data if row.document == docname]

	def make_si(self, qty=5, rate=150, update_stock=0, customer=CUSTOMER, posting_date=None):
		si = frappe.get_doc(
			{
				"doctype": "Sales Invoice",
				"company": COMPANY,
				"customer": customer,
				"update_stock": update_stock,
				"cost_center": self.cost_center,
				"items": [
					{
						"item_code": self.item_code,
						"qty": qty,
						"rate": rate,
						"warehouse": self.warehouse,
						"cost_center": self.cost_center,
					}
				],
			}
		)
		if posting_date:
			si.set_posting_time = 1
			si.posting_date = posting_date
		si.insert()
		si.submit()
		return si

	def make_dn_from_si(self, si, qty=None):
		from erpnext.accounts.doctype.sales_invoice.sales_invoice import make_delivery_note

		dn = make_delivery_note(si.name)
		if qty is not None:
			dn.items[0].qty = qty
		dn.insert()
		dn.submit()
		return dn

	def make_dn(self, qty=4, rate=150, customer=CUSTOMER):
		dn = frappe.get_doc(
			{
				"doctype": "Delivery Note",
				"company": COMPANY,
				"customer": customer,
				"cost_center": self.cost_center,
				"items": [
					{
						"item_code": self.item_code,
						"qty": qty,
						"rate": rate,
						"warehouse": self.warehouse,
						"cost_center": self.cost_center,
					}
				],
			}
		)
		dn.insert()
		dn.submit()
		return dn

	def make_si_from_dn(self, dn, qty=None):
		from erpnext.stock.doctype.delivery_note.delivery_note import make_sales_invoice

		si = make_sales_invoice(dn.name)
		si.update_stock = 0
		if qty is not None:
			si.items[0].qty = qty
		si.insert()
		si.submit()
		return si

	def make_pr(self, qty=6, rate=90, supplier=SUPPLIER):
		pr = frappe.get_doc(
			{
				"doctype": "Purchase Receipt",
				"company": COMPANY,
				"supplier": supplier,
				"cost_center": self.cost_center,
				"items": [
					{
						"item_code": self.item_code,
						"qty": qty,
						"rate": rate,
						"warehouse": self.warehouse,
						"cost_center": self.cost_center,
					}
				],
			}
		)
		pr.insert()
		pr.submit()
		return pr

	def make_pi_from_pr(self, pr, qty=None):
		from erpnext.stock.doctype.purchase_receipt.purchase_receipt import make_purchase_invoice

		pi = make_purchase_invoice(pr.name)
		if qty is not None:
			pi.items[0].qty = qty
		pi.insert()
		pi.submit()
		return pi

	def make_pi(self, qty=3, rate=90, supplier=SUPPLIER):
		pi = frappe.get_doc(
			{
				"doctype": "Purchase Invoice",
				"company": COMPANY,
				"supplier": supplier,
				"update_stock": 0,
				"cost_center": self.cost_center,
				"items": [
					{
						"item_code": self.item_code,
						"qty": qty,
						"rate": rate,
						"warehouse": self.warehouse,
						"cost_center": self.cost_center,
					}
				],
			}
		)
		pi.insert()
		pi.submit()
		return pi

	def make_pr_from_pi(self, pi, qty=None):
		from erpnext.accounts.doctype.purchase_invoice.purchase_invoice import make_purchase_receipt

		pr = make_purchase_receipt(pi.name)
		if qty is not None:
			pr.items[0].qty = qty
		pr.insert()
		pr.submit()
		return pr

	# ------------------------------------------------------------------
	# sales: invoice first, deliver later
	# ------------------------------------------------------------------

	def test_invoice_stays_open_until_delivered(self):
		si = self.make_si(qty=5)

		rows = self.rows_for(invoiced_items_to_be_delivered(self.filters()), si.name)
		self.assertEqual(len(rows), 1)
		self.assertEqual(rows[0].pending_qty, 5)
		self.assertEqual(rows[0].pending_amount, 750)

		self.make_dn_from_si(si, qty=2)
		rows = self.rows_for(invoiced_items_to_be_delivered(self.filters()), si.name)
		self.assertEqual(rows[0].pending_qty, 3)
		self.assertEqual(rows[0].delivered_qty, 2)

		self.make_dn_from_si(si, qty=3)
		rows = self.rows_for(invoiced_items_to_be_delivered(self.filters()), si.name)
		self.assertEqual(rows, [])

	def test_update_stock_invoice_is_never_open(self):
		si = self.make_si(qty=1, update_stock=1)
		rows = self.rows_for(invoiced_items_to_be_delivered(self.filters()), si.name)
		self.assertEqual(rows, [])

	def test_as_on_uses_counterpart_posting_dates(self):
		yesterday = add_days(nowdate(), -1)
		si = self.make_si(qty=5, posting_date=yesterday)
		self.make_dn_from_si(si, qty=5)

		# fully delivered today, but yesterday the whole invoice was open
		rows = self.rows_for(invoiced_items_to_be_delivered(self.filters(as_on=yesterday)), si.name)
		self.assertEqual(rows[0].pending_qty, 5)
		self.assertEqual(rows[0].age, 0)

		rows = self.rows_for(invoiced_items_to_be_delivered(self.filters()), si.name)
		self.assertEqual(rows, [])

	def test_zero_rate_row_still_shows(self):
		si = self.make_si(qty=2, rate=0)
		rows = self.rows_for(invoiced_items_to_be_delivered(self.filters()), si.name)
		self.assertEqual(rows[0].pending_qty, 2)
		self.assertEqual(rows[0].pending_amount, 0)

	# ------------------------------------------------------------------
	# sales: deliver first, bill later
	# ------------------------------------------------------------------

	def test_delivery_stays_open_until_billed(self):
		dn = self.make_dn(qty=4)

		rows = self.rows_for(delivered_items_pending_billing(self.filters()), dn.name)
		self.assertEqual(rows[0].pending_qty, 4)

		self.make_si_from_dn(dn, qty=1)
		rows = self.rows_for(delivered_items_pending_billing(self.filters()), dn.name)
		self.assertEqual(rows[0].pending_qty, 3)
		self.assertEqual(rows[0].billed_qty, 1)

	def test_delivery_return_reduces_pending(self):
		from erpnext.controllers.sales_and_purchase_return import make_return_doc

		dn = self.make_dn(qty=4)
		return_dn = make_return_doc("Delivery Note", dn.name)
		return_dn.items[0].qty = -1
		return_dn.insert()
		return_dn.submit()

		rows = self.rows_for(delivered_items_pending_billing(self.filters()), dn.name)
		self.assertEqual(rows[0].pending_qty, 3)
		self.assertEqual(rows[0].returned_qty, 1)

		# the return itself must never appear as an open item
		self.assertEqual(
			self.rows_for(delivered_items_pending_billing(self.filters()), return_dn.name), []
		)

	# ------------------------------------------------------------------
	# purchase: receive first, bill later
	# ------------------------------------------------------------------

	def test_receipt_stays_open_until_billed(self):
		pr = self.make_pr(qty=6)

		rows = self.rows_for(received_items_pending_billing(self.filters()), pr.name)
		self.assertEqual(rows[0].pending_qty, 6)

		self.make_pi_from_pr(pr)
		rows = self.rows_for(received_items_pending_billing(self.filters()), pr.name)
		self.assertEqual(rows, [])

	# ------------------------------------------------------------------
	# purchase: bill first, receive later
	# ------------------------------------------------------------------

	def test_bill_stays_open_until_received(self):
		pi = self.make_pi(qty=3)

		rows = self.rows_for(billed_items_pending_receipt(self.filters()), pi.name)
		self.assertEqual(rows[0].pending_qty, 3)

		self.make_pr_from_pi(pi)
		rows = self.rows_for(billed_items_pending_receipt(self.filters()), pi.name)
		self.assertEqual(rows, [])

	# ------------------------------------------------------------------
	# filters and ageing
	# ------------------------------------------------------------------

	def test_row_level_filters(self):
		si = self.make_si(qty=2, customer=OTHER_CUSTOMER)

		report = invoiced_items_to_be_delivered
		self.assertEqual(len(self.rows_for(report(self.filters(party=OTHER_CUSTOMER)), si.name)), 1)
		self.assertEqual(self.rows_for(report(self.filters(party=CUSTOMER)), si.name), [])
		self.assertEqual(
			len(self.rows_for(report(self.filters(warehouse=self.warehouse)), si.name)), 1
		)
		self.assertEqual(
			self.rows_for(report(self.filters(warehouse="Work In Progress - " + ABBR)), si.name), []
		)
		self.assertEqual(
			len(self.rows_for(report(self.filters(item_code=self.item_code)), si.name)), 1
		)
		self.assertEqual(
			len(self.rows_for(report(self.filters(cost_center=self.cost_center)), si.name)), 1
		)

	def test_ageing_buckets(self):
		ranges = [30, 60, 90]
		self.assertEqual(ageing_bucket(0, ranges), "0-30")
		self.assertEqual(ageing_bucket(30, ranges), "0-30")
		self.assertEqual(ageing_bucket(31, ranges), "31-60")
		self.assertEqual(ageing_bucket(90, ranges), "61-90")
		self.assertTrue(ageing_bucket(91, ranges).startswith("91-"))

	def test_age_and_bucket_on_rows(self):
		posting = add_days(nowdate(), -45)
		si = self.make_si(qty=1, posting_date=posting)

		rows = self.rows_for(invoiced_items_to_be_delivered(self.filters()), si.name)
		self.assertEqual(rows[0].age, 45)
		self.assertEqual(rows[0].bucket, "31-60")
