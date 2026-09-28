"""Tests for the Document Trail walk (sf_trading.document_trail).

    bench --site <scratch-site> run-tests --module sf_trading.tests.test_document_trail
"""

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import add_days, nowdate

from sf_trading.document_trail import get_document_trail
from sf_trading.tests.test_open_items import CUSTOMER, SUPPLIER, TestOpenItems


class TestDocumentTrail(FrappeTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		TestOpenItems.setUpClass()
		cls.company = TestOpenItems.company.name
		cls.warehouse = TestOpenItems.warehouse
		cls.cost_center = TestOpenItems.cost_center
		cls.item_code = TestOpenItems.item_code

	# ---- Purchase side: Purchase Order -> Purchase Receipt -> Purchase Invoice ----

	def make_purchase_order(self, qty=5, rate=100):
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
						"item_code": self.item_code,
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

	def make_purchase_chain(self):
		"""PO -> PR -> PI, the invoice raised off the RECEIPT (two hops from the order)."""
		from erpnext.buying.doctype.purchase_order.purchase_order import make_purchase_receipt
		from erpnext.stock.doctype.purchase_receipt.purchase_receipt import (
			make_purchase_invoice as bill_receipt,
		)

		po = self.make_purchase_order()
		pr = make_purchase_receipt(po.name)
		pr.insert()
		pr.submit()

		pi = bill_receipt(pr.name)
		pi.insert()
		pi.submit()
		return po, pr, pi

	def test_a_three_hop_purchase_chain_is_seen_from_any_of_its_three_documents(self):
		po, pr, pi = self.make_purchase_chain()

		for doctype, name in (
			("Purchase Order", po.name),
			("Purchase Receipt", pr.name),
			("Purchase Invoice", pi.name),
		):
			with self.subTest(doctype=doctype):
				trail = get_document_trail(doctype, name)
				self.assertEqual(trail["chain"], ["Purchase Order", "Purchase Receipt", "Purchase Invoice"])
				documents = trail["documents"]
				self.assertIn(po.name, [d["name"] for d in documents.get("Purchase Order", [])])
				self.assertIn(pr.name, [d["name"] for d in documents.get("Purchase Receipt", [])])
				self.assertIn(pi.name, [d["name"] for d in documents.get("Purchase Invoice", [])])

	# ---- Sales side: Sales Order -> Delivery Note -> Sales Invoice ----

	def make_sales_order(self, qty=3, rate=150):
		so = frappe.get_doc(
			{
				"doctype": "Sales Order",
				"company": self.company,
				"customer": CUSTOMER,
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

	def make_sales_chain(self):
		"""SO -> DN -> SI, the invoice raised off the DELIVERY NOTE (two hops from the order)."""
		from erpnext.selling.doctype.sales_order.sales_order import make_delivery_note
		from erpnext.stock.doctype.delivery_note.delivery_note import make_sales_invoice

		so = self.make_sales_order()
		dn = make_delivery_note(so.name)
		TestOpenItems.fill_site_mandatories(dn)
		dn.insert()
		dn.submit()

		si = make_sales_invoice(dn.name)
		TestOpenItems.fill_site_mandatories(si)
		si.insert()
		si.submit()
		return so, dn, si

	def test_a_three_hop_sales_chain_is_seen_from_any_of_its_three_documents(self):
		so, dn, si = self.make_sales_chain()

		for doctype, name in (
			("Sales Order", so.name),
			("Delivery Note", dn.name),
			("Sales Invoice", si.name),
		):
			with self.subTest(doctype=doctype):
				trail = get_document_trail(doctype, name)
				self.assertEqual(trail["chain"], ["Sales Order", "Delivery Note", "Sales Invoice"])
				documents = trail["documents"]
				self.assertIn(so.name, [d["name"] for d in documents.get("Sales Order", [])])
				self.assertIn(dn.name, [d["name"] for d in documents.get("Delivery Note", [])])
				self.assertIn(si.name, [d["name"] for d in documents.get("Sales Invoice", [])])

	# ---- No chain at all ----

	def test_a_direct_invoice_with_no_chain_returns_only_itself(self):
		si = frappe.get_doc(
			{
				"doctype": "Sales Invoice",
				"company": self.company,
				"customer": CUSTOMER,
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
		TestOpenItems.fill_site_mandatories(si)
		si.insert()

		trail = get_document_trail("Sales Invoice", si.name)
		documents = trail["documents"]

		self.assertEqual([d["name"] for d in documents.get("Sales Invoice", [])], [si.name])
		self.assertFalse(documents.get("Sales Order"))
		self.assertFalse(documents.get("Delivery Note"))

	def test_an_unknown_doctype_is_refused(self):
		with self.assertRaises(frappe.ValidationError):
			get_document_trail("Item", "Some Item")
