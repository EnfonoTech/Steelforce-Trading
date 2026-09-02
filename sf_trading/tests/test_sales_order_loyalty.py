"""Loyalty on a Sales Order payment — the invoice mechanism, on an order.

"Loyalty" on this site is the write-off under a business-facing name: the field is `write_off`,
the money lands on the Company's Write Off Account (named "Loyalty Rewards" on steelforce, an
expense account) as a deduction on the Payment Entry, and the Company's Max Payment Write Off
caps it — 0.400 there, because what it is for is closing a fils-level shortfall so the document
reads settled. Production has 2,513 of them since 15 July totalling 149.926.

The order adds one rule the invoice does not need: loyalty may only CLOSE an order. An order
accepts partial deposits, and forgiving part of one nobody is settling would drop the balance
with no bill behind the forgiven part.

    bench --site <site> run-tests --module sf_trading.tests.test_sales_order_loyalty
"""

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import add_days, flt, nowdate

from sf_trading.api.sales_order_payment import (
	create_payments_for_sales_order,
	get_sales_order_payment_state,
)
from sf_trading.tests.test_open_items import CUSTOMER, TestOpenItems

COMPANY = None  # resolved in setUpClass from the same test company the family uses


class TestSalesOrderLoyalty(FrappeTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		# the open-item suite already builds a company, a stocked item and the parties
		TestOpenItems.setUpClass()
		cls.company = TestOpenItems.company.name
		cls.warehouse = TestOpenItems.warehouse
		cls.cost_center = TestOpenItems.cost_center
		cls.item_code = TestOpenItems.item_code

	def setUp(self):
		self.write_off_account = frappe.db.get_value("Company", self.company, "write_off_account")
		if not self.write_off_account:
			self.write_off_account = frappe.db.get_value(
				"Account", {"company": self.company, "root_type": "Expense", "is_group": 0}, "name"
			)
			if not self.write_off_account:
				self.skipTest("no expense account on the test company to write off to")
			frappe.db.set_value("Company", self.company, "write_off_account", self.write_off_account)
		frappe.db.set_value("Company", self.company, "custom_max_payment_write_off", 0.4)
		frappe.clear_cache(doctype="Company")

	def make_order(self, qty=1, rate=100):
		so = frappe.get_doc(
			{
				"doctype": "Sales Order",
				"company": self.company,
				"customer": CUSTOMER,
				"transaction_date": nowdate(),
				"delivery_date": add_days(nowdate(), 3),
				"cost_center": self.cost_center,
				"items": [
					{
						"item_code": self.item_code,
						"qty": qty,
						"rate": rate,
						"warehouse": self.warehouse,
						"cost_center": self.cost_center,
						"delivery_date": add_days(nowdate(), 3),
					}
				],
			}
		)
		TestOpenItems.fill_site_mandatories(so)
		so.insert()
		so.submit()
		return so

	def mode(self):
		"""A mode of payment the test company can actually resolve an account for."""
		from erpnext.accounts.doctype.sales_invoice.sales_invoice import get_bank_cash_account

		for name in frappe.get_all("Mode of Payment", pluck="name"):
			try:
				if (get_bank_cash_account(name, self.company) or {}).get("account"):
					return name
			except Exception:
				continue
		self.skipTest("no mode of payment resolves to an account on the test company")

	def collect(self, so, cash, loyalty):
		return create_payments_for_sales_order(
			sales_order=so.name,
			payments=[{"mode_of_payment": self.mode(), "amount": cash}],
			write_off_amount=loyalty,
		)

	# ── what the dialog is told ──────────────────────────────────────────────────
	def test_the_state_carries_the_company_loyalty_settings(self):
		so = self.make_order(qty=1, rate=100)
		state = get_sales_order_payment_state(so.name)
		self.assertEqual(state["write_off_account"], self.write_off_account)
		self.assertAlmostEqual(flt(state["max_write_off"]), 0.4, places=3)

	# ── the guards, none of which may create anything ────────────────────────────
	def test_a_negative_loyalty_is_refused(self):
		so = self.make_order()
		before = frappe.db.count("Payment Entry")
		with self.assertRaises(frappe.ValidationError) as caught:
			self.collect(so, cash=99, loyalty=-1)
		self.assertIn("cannot be negative", str(caught.exception))
		self.assertEqual(frappe.db.count("Payment Entry"), before)

	def test_loyalty_over_the_company_cap_is_refused(self):
		so = self.make_order(qty=1, rate=100)
		before = frappe.db.count("Payment Entry")
		with self.assertRaises(frappe.ValidationError) as caught:
			self.collect(so, cash=98, loyalty=2)
		text = frappe.utils.strip_html(str(caught.exception))
		self.assertIn("exceeds the company limit", text)
		self.assertEqual(frappe.db.count("Payment Entry"), before)

	def test_loyalty_on_a_part_payment_is_refused(self):
		"""The one rule the invoice does not need."""
		so = self.make_order(qty=1, rate=100)
		before = frappe.db.count("Payment Entry")
		with self.assertRaises(frappe.ValidationError) as caught:
			self.collect(so, cash=50, loyalty=0.3)
		text = frappe.utils.strip_html(str(caught.exception))
		self.assertIn("may only close an order", text)
		self.assertEqual(frappe.db.count("Payment Entry"), before)

	def test_cash_plus_loyalty_over_the_balance_is_refused(self):
		so = self.make_order(qty=1, rate=100)
		with self.assertRaises(frappe.ValidationError):
			self.collect(so, cash=100, loyalty=0.3)

	def test_a_missing_company_cap_refuses_loyalty(self):
		frappe.db.set_value("Company", self.company, "custom_max_payment_write_off", 0)
		frappe.clear_cache(doctype="Company")
		so = self.make_order()
		with self.assertRaises(frappe.ValidationError) as caught:
			self.collect(so, cash=99.7, loyalty=0.3)
		self.assertIn("Max Payment Write Off", frappe.utils.strip_html(str(caught.exception)))

	def test_loyalty_is_refused_on_a_foreign_currency_order(self):
		"""A deduction is always in company currency; the identity cannot close otherwise."""
		other = frappe.db.get_value(
			"Currency", {"name": ["!=", frappe.db.get_value("Company", self.company, "default_currency")],
			             "enabled": 1}, "name"
		)
		if not other:
			self.skipTest("no second enabled currency on this site")

		so = self.make_order(qty=1, rate=100)
		frappe.db.set_value("Sales Order", so.name, "currency", other)
		before = frappe.db.count("Payment Entry")
		with self.assertRaises(frappe.ValidationError) as caught:
			self.collect(so, cash=99.7, loyalty=0.3)
		self.assertIn("only available on an order in", frappe.utils.strip_html(str(caught.exception)))
		self.assertEqual(frappe.db.count("Payment Entry"), before)

	def test_loyalty_is_refused_once_billing_has_started(self):
		from erpnext.selling.doctype.sales_order.sales_order import make_sales_invoice

		so = self.make_order(qty=2, rate=50)
		si = make_sales_invoice(so.name)
		si.items[0].qty = 1
		TestOpenItems.fill_site_mandatories(si)
		si.insert()
		si.submit()
		so.reload()
		if flt(so.per_billed) <= 0.01:
			self.skipTest("the invoice did not register against the order on this site")

		with self.assertRaises(frappe.ValidationError) as caught:
			self.collect(so, cash=flt(so.grand_total) - flt(so.advance_paid) - 0.3, loyalty=0.3)
		self.assertIn("Take the loyalty on the invoice instead",
		              frappe.utils.strip_html(str(caught.exception)))

	# ── the happy path ───────────────────────────────────────────────────────────
	def test_loyalty_closes_the_order_and_books_the_difference(self):
		so = self.make_order(qty=1, rate=100)
		balance = get_sales_order_payment_state(so.name)["balance"]
		loyalty = 0.3
		cash = flt(balance - loyalty, 3)

		created = self.collect(so, cash=cash, loyalty=loyalty)
		self.assertEqual(len(created), 1)

		pe = frappe.get_doc("Payment Entry", created[0])
		self.assertEqual(pe.docstatus, 1)
		# the cash that actually moved
		self.assertAlmostEqual(flt(pe.paid_amount), cash, places=3)
		# ... while the order is credited with the whole balance
		self.assertAlmostEqual(flt(pe.references[0].allocated_amount), balance, places=3)
		self.assertEqual(pe.references[0].reference_doctype, "Sales Order")
		self.assertEqual(pe.references[0].reference_name, so.name)
		# ... funded by one deduction row on the write-off account
		self.assertEqual(len(pe.deductions), 1)
		self.assertEqual(pe.deductions[0].account, self.write_off_account)
		self.assertAlmostEqual(flt(pe.deductions[0].amount), loyalty, places=3)

		so.reload()
		self.assertAlmostEqual(flt(so.advance_paid), balance, places=3)
		self.assertAlmostEqual(get_sales_order_payment_state(so.name)["balance"], 0, places=3)

	def test_a_plain_part_payment_still_works(self):
		"""The regression net: an order takes deposits, and that path is untouched."""
		so = self.make_order(qty=1, rate=100)
		created = self.collect(so, cash=40, loyalty=0)
		self.assertEqual(len(created), 1)
		pe = frappe.get_doc("Payment Entry", created[0])
		self.assertFalse(pe.deductions)
		self.assertAlmostEqual(flt(pe.paid_amount), 40, places=3)
		so.reload()
		self.assertAlmostEqual(flt(so.advance_paid), 40, places=3)

	# ── the cap is per SALE, not per document ────────────────────────────────────
	def test_the_invoice_refuses_loyalty_the_order_already_took(self):
		"""One sale, one loyalty. The company cap is enforced per payment, so without this the
		same sale could take it on the order and again on the invoice."""
		from erpnext.selling.doctype.sales_order.sales_order import make_sales_invoice
		from sf_trading.api.sales_invoice_payment import (
			create_pos_payments_for_invoice,
			get_loyalty_state,
			loyalty_already_given,
		)

		so = self.make_order(qty=1, rate=100)
		balance = get_sales_order_payment_state(so.name)["balance"]
		self.collect(so, cash=flt(balance - 0.3, 3), loyalty=0.3)

		si = make_sales_invoice(so.name)
		TestOpenItems.fill_site_mandatories(si)
		si.insert()
		si.submit()

		# the order's loyalty is found through the invoice's own item rows
		already = loyalty_already_given(si)
		self.assertAlmostEqual(already["amount"], 0.3, places=3)
		self.assertIn(so.name, already["orders"])

		# ... so the popup is told not to offer the field at all
		state = get_loyalty_state(si.name)
		self.assertFalse(state["allowed"])
		self.assertAlmostEqual(flt(state["already_given"]), 0.3, places=3)

		# ... and the server refuses it even if something asks anyway
		before = frappe.db.count("Payment Entry")
		with self.assertRaises(frappe.ValidationError) as caught:
			create_pos_payments_for_invoice(
				sales_invoice=si.name,
				payments=[{"mode_of_payment": self.mode(), "amount": 1}],
				write_off_amount=0.1,
			)
		text = frappe.utils.strip_html(str(caught.exception))
		self.assertIn("already given", text)
		self.assertIn(so.name, text)
		self.assertEqual(frappe.db.count("Payment Entry"), before)

	def test_an_invoice_with_no_order_still_offers_loyalty(self):
		"""The regression net: 3,462 of 3,469 invoices on production name no order."""
		from sf_trading.api.sales_invoice_payment import get_loyalty_state, loyalty_already_given

		si = TestOpenItems.make_si(self, qty=1, rate=100)
		self.assertEqual(loyalty_already_given(si)["amount"], 0.0)
		state = get_loyalty_state(si.name)
		self.assertTrue(state["allowed"])
		self.assertAlmostEqual(flt(state["max_write_off"]), 0.4, places=3)
