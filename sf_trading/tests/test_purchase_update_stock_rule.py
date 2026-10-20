# sf_trading/tests/test_purchase_update_stock_rule.py
"""Update Stock on a Purchase Invoice stays ticked, for everyone but Administrator, when the invoice IS the
goods' arrival (sf_trading/api/purchase_order_invoice.py: is_the_arrival, require_stock_update).

    bench --site <site> run-tests --module sf_trading.tests.test_purchase_update_stock_rule

The database is patched at the module's own seams, so nothing is created or read.
"""

import ast
import json
import os
import re
from unittest.mock import MagicMock, patch

import frappe
from frappe.tests.utils import FrappeTestCase

from sf_trading.api import purchase_order_invoice as poi

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def read(*parts):
	with open(os.path.join(ROOT, *parts), encoding="utf-8") as f:
		return f.read()


def row(item="STEEL-1", **extra):
	return frappe._dict(item_code=item, **extra)


def invoice(rows=None, update_stock=0, docstatus=0, before=None, **values):
	fields = dict(doctype="Purchase Invoice", docstatus=docstatus, update_stock=update_stock, is_return=0,
		is_opening="No", items=rows if rows is not None else [row()])
	fields.update(values)
	doc = frappe._dict(fields)
	doc["get_doc_before_save"] = lambda: before
	return doc


class Seams:
	"""The module's own helpers, each to a settable value."""

	def __init__(self, user="buyer@example.com", stock=True, eligible=True, receipt=False, tracked=False):
		self.user, self.stock, self.eligible, self.receipt, self.tracked = user, stock, eligible, receipt, tracked
		self.patches = []

	def __enter__(self):
		table = [
			(poi, "_stock_rows", lambda inv: [r for r in inv.get("items") or [] if r.get("item_code")] if self.stock else []),
			(poi, "_orders_are_eligible", lambda orders: self.eligible),
			(poi, "_has_a_receipt", lambda orders, inv: self.receipt),
			(poi, "_is_tracked", lambda code: self.tracked),
		]
		for target, name, fn in table:
			p = patch.object(target, name, side_effect=fn)
			p.start()
			self.patches.append(p)
		self.msgprint = MagicMock()
		for target, attr, value in [
			(poi.frappe, "msgprint", self.msgprint),
			(poi.frappe, "session", frappe._dict(user=self.user)),
		]:
			p = patch.object(target, attr, value, create=True)
			p.start()
			self.patches.append(p)
		return self

	def __exit__(self, *exc):
		for p in reversed(self.patches):
			p.stop()


class TestWhenTheInvoiceIsTheArrival(FrappeTestCase):
	def test_a_direct_invoice_of_stock_items_is_the_arrival(self):
		with Seams():
			self.assertTrue(poi.is_the_arrival(invoice()))

	def test_an_invoice_from_an_untouched_order_is_the_arrival(self):
		with Seams():
			self.assertTrue(poi.is_the_arrival(invoice([row(purchase_order="PO-1", po_detail="r1")])))

	def test_a_return_and_an_opening_entry_are_not(self):
		with Seams():
			self.assertFalse(poi.is_the_arrival(invoice(is_return=1)))
			self.assertFalse(poi.is_the_arrival(invoice(is_opening="Yes")))

	def test_a_bill_with_no_stock_row_is_not(self):
		with Seams(stock=False):
			self.assertFalse(poi.is_the_arrival(invoice()))

	def test_a_row_that_bills_a_purchase_receipt_is_not(self):
		with Seams():
			self.assertFalse(poi.is_the_arrival(invoice([row(purchase_receipt="MAT-PRE-1")])))
			self.assertFalse(poi.is_the_arrival(invoice([row(pr_detail="x1")])))

	def test_an_order_that_has_a_receipt_or_is_part_received_or_closed_is_not(self):
		rows = [row(purchase_order="PO-1", po_detail="r1")]
		with Seams(receipt=True):
			self.assertFalse(poi.is_the_arrival(invoice(rows)))
		with Seams(eligible=False):
			self.assertFalse(poi.is_the_arrival(invoice(rows)))

	def test_a_tracked_item_is_left_to_the_buyer(self):
		with Seams(tracked=True):
			self.assertFalse(poi.is_the_arrival(invoice()))

	def test_an_invoice_made_from_a_goods_in_is_the_arrival_whatever_the_order_says(self):
		rows = [row(purchase_order="PO-1", po_detail="r1", custom_goods_in_item="GI-1-1")]
		with Seams(eligible=False, receipt=True):
			self.assertTrue(poi.is_the_arrival(invoice(rows)))


class TestRequireStockUpdate(FrappeTestCase):
	def test_anyone_but_administrator_has_the_box_ticked_again(self):
		with Seams() as s:
			doc = invoice()
			poi.require_stock_update(doc)
			self.assertEqual(doc.update_stock, 1)
			s.msgprint.assert_called_once()

	def test_administrator_may_leave_it_off(self):
		with Seams(user="Administrator") as s:
			doc = invoice()
			poi.require_stock_update(doc)
			self.assertEqual(doc.update_stock, 0)
			s.msgprint.assert_not_called()

	def test_a_box_already_ticked_is_left_alone(self):
		with Seams() as s:
			doc = invoice(update_stock=1)
			poi.require_stock_update(doc)
			self.assertEqual(doc.update_stock, 1)
			s.msgprint.assert_not_called()

	def test_goods_already_received_are_never_ticked(self):
		with Seams(receipt=True) as s:
			doc = invoice([row(purchase_order="PO-1", po_detail="r1")])
			poi.require_stock_update(doc)
			self.assertEqual(doc.update_stock, 0)
			s.msgprint.assert_not_called()

	def test_the_approvers_save_is_caught(self):
		# PI 20020000419: the approval save arrives marked submitted, with the draft as the document before it
		with Seams():
			doc = invoice(docstatus=1, before=frappe._dict(docstatus=0))
			poi.require_stock_update(doc)
			self.assertEqual(doc.update_stock, 1)

	def test_a_submitted_invoice_is_never_touched_again(self):
		with Seams():
			doc = invoice(docstatus=1, before=frappe._dict(docstatus=1))
			poi.require_stock_update(doc)
			self.assertEqual(doc.update_stock, 0)
			doc = invoice(docstatus=2, before=frappe._dict(docstatus=1))
			poi.require_stock_update(doc)
			self.assertEqual(doc.update_stock, 0)

	def test_a_document_with_no_before_save_is_a_draft_only_while_it_is_one(self):
		with Seams():
			doc = invoice(docstatus=1, before=None)
			poi.require_stock_update(doc)
			self.assertEqual(doc.update_stock, 0)


class TestTheRuleForTheForm(FrappeTestCase):
	def ask(self, doc, locked):
		with Seams(), patch.object(poi.frappe, "has_permission", MagicMock(return_value=True), create=True), \
				patch.object(poi.frappe, "parse_json", side_effect=lambda x: x, create=True), \
				patch.object(poi.frappe, "get_doc", MagicMock(return_value=doc), create=True):
			return poi.update_stock_rule(json.dumps({"doctype": "Purchase Invoice"}))

	def test_it_says_locked_for_an_arrival(self):
		self.assertEqual(self.ask(invoice(), True), {"locked": True})

	def test_it_says_not_locked_for_a_return(self):
		self.assertEqual(self.ask(invoice(is_return=1), False), {"locked": False})

	def test_it_asks_for_read_permission(self):
		has = MagicMock(return_value=True)
		with Seams(), patch.object(poi.frappe, "has_permission", has, create=True), \
				patch.object(poi.frappe, "parse_json", side_effect=lambda x: x, create=True), \
				patch.object(poi.frappe, "get_doc", MagicMock(return_value=invoice()), create=True):
			poi.update_stock_rule("{}")
		has.assert_called_once_with("Purchase Invoice", "read", throw=True)


class TestWiring(FrappeTestCase):
	"""A hook dropped from hooks.py, or a script not registered, takes the rule away without a test of the logic failing."""

	def hooks(self):
		return read("hooks.py")

	def test_the_save_hook_runs_right_after_the_unticking_hook(self):
		text = self.hooks()
		first = text.index('"sf_trading.api.purchase_order_invoice.untick_stock_when_the_goods_already_arrived"')
		second = text.index('"sf_trading.api.purchase_order_invoice.require_stock_update"')
		between = text[first:second]
		self.assertEqual(len(re.findall(r'"sf_trading\.[\w.]+"', between)), 1)  # nothing in between

	def test_every_hook_and_method_named_exists(self):
		tree = ast.parse(read("api", "purchase_order_invoice.py"))
		defined = {n.name for n in tree.body if isinstance(n, ast.FunctionDef)}
		for name in re.findall(r"sf_trading\.api\.purchase_order_invoice\.(\w+)", self.hooks()):
			self.assertIn(name, defined)
		self.assertIn("update_stock_rule", defined)
		self.assertIn("update_stock_rule", read("public", "js", "purchase_invoice_update_stock.js"))

	def test_the_form_script_is_registered_on_the_purchase_invoice(self):
		self.assertIn("public/js/purchase_invoice_update_stock.js", self.hooks())

	def test_the_rule_is_whitelisted(self):
		tree = ast.parse(read("api", "purchase_order_invoice.py"))
		fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "update_stock_rule")
		self.assertTrue(any("whitelist" in ast.dump(d) for d in fn.decorator_list))


class TestPriceListOnQuotationAndSalesOrder(FrappeTestCase):
	"""The Sales Invoice's per-row Price List, on the quotation and the order too."""

	def fixtures(self):
		return {f["name"]: f for f in json.loads(read("fixtures", "custom_field.json"))}

	def test_the_row_field_matches_the_sales_invoices(self):
		fields = self.fixtures()
		base = fields["Sales Invoice Item-custom_price_list"]
		for dt in ("Quotation Item", "Sales Order Item"):
			f = fields[dt + "-custom_price_list"]
			self.assertEqual((f["dt"], f["fieldname"], f["fieldtype"], f["options"]), (dt, "custom_price_list", "Link", "Price List"))
			for key in ("insert_after", "in_list_view", "columns", "label", "print_hide", "no_copy", "reqd"):
				self.assertEqual(f[key], base[key], key)

	def test_the_fields_are_exported_with_the_app(self):
		text = self.hooks()
		for name in ("Quotation Item-custom_price_list", "Sales Order Item-custom_price_list"):
			self.assertIn('"%s"' % name, text)

	def hooks(self):
		return read("hooks.py")

	def test_the_script_is_registered_on_both_documents(self):
		text = self.hooks()
		self.assertEqual(text.count('"public/js/row_price_list.js"'), 2)  # registrations, not the comments naming it
		quotation = text[text.index('"Quotation":        ['):]
		self.assertLess(quotation.index('"public/js/row_price_list.js"'), quotation.index('"Sales Order":'))

	def test_the_script_handles_both_documents_and_their_rows(self):
		js = read("public", "js", "row_price_list.js")
		for needle in ('Quotation: "Quotation Item"', '"Sales Order": "Sales Order Item"', "get_row_price_list_rate",
				"custom_price_list", "items_add", "selling_price_list"):
			self.assertIn(needle, js)

	def test_a_quotation_turned_into_an_invoice_keeps_each_rows_price_list(self):
		self.assertIn('"custom_price_list": q.get("custom_price_list")', read("api", "quotation.py"))

	def test_the_minimum_price_check_already_reads_the_rows_list(self):
		self.assertIn('item.get("custom_price_list")', read("api", "selling_price_validation.py"))
