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

	def __init__(self, user="buyer@example.com", stock=True, live=True, receipt=False, tracked=False):
		self.user, self.stock, self.live, self.receipt, self.tracked = user, stock, live, receipt, tracked
		self.patches = []

	def __enter__(self):
		table = [
			(poi, "_stock_rows", lambda inv: [r for r in inv.get("items") or [] if r.get("item_code")] if self.stock else []),
			(poi, "_orders_are_live", lambda orders: self.live),
			(poi, "_received_rows", lambda inv: list(inv.get("items") or []) if self.receipt else []),
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

	def test_a_received_order_line_or_a_closed_order_is_not(self):
		rows = [row(purchase_order="PO-1", po_detail="r1")]
		with Seams(receipt=True):
			self.assertFalse(poi.is_the_arrival(invoice(rows)))
		with Seams(live=False):
			self.assertFalse(poi.is_the_arrival(invoice(rows)))

	def test_a_tracked_item_is_left_to_the_buyer(self):
		with Seams(tracked=True):
			self.assertFalse(poi.is_the_arrival(invoice()))

	def test_an_invoice_made_from_a_goods_in_is_the_arrival_whatever_the_order_says(self):
		rows = [row(purchase_order="PO-1", po_detail="r1", custom_goods_in_item="GI-1-1")]
		with Seams(live=False, receipt=True):
			self.assertTrue(poi.is_the_arrival(invoice(rows)))


class Lines:
	"""The database as `_received_rows` reads it: order lines, receipt rows, orders."""

	def __init__(self, lines=None, receipted=(), received_orders=(), receipt_orders=()):
		self.lines = lines or {}
		self.receipted, self.received_orders, self.receipt_orders = set(receipted), set(received_orders), set(receipt_orders)

	def get_all(self, doctype, filters=None, fields=None, pluck=None):
		wanted = set(filters["name"][1]) if "name" in filters else None
		if doctype == "Purchase Order Item":
			return [frappe._dict(name=n, qty=q, received_qty=r) for n, (q, r) in self.lines.items() if n in wanted]
		if doctype == "Purchase Receipt Item" and "purchase_order_item" in filters:
			return [d for d in filters["purchase_order_item"][1] if d in self.receipted]
		if doctype == "Purchase Receipt Item":
			return [o for o in filters["purchase_order"][1] if o in self.receipt_orders]
		if doctype == "Purchase Order":
			return [o for o in wanted if o in self.received_orders]
		raise AssertionError("unexpected read of %s" % doctype)

	def __enter__(self):
		self.patches = [
			patch.object(poi.frappe, "get_all", side_effect=self.get_all),
			patch.object(poi, "_stock_rows", side_effect=lambda inv: [r for r in inv.get("items") or [] if r.get("item_code")]),
		]
		for p in self.patches:
			p.start()
		return self

	def __exit__(self, *exc):
		for p in reversed(self.patches):
			p.stop()


def ordered(item="STEEL-1", order="PO-1", line="r1", qty=3):
	return row(item, purchase_order=order, po_detail=line, qty=qty)


class TestReceivedIsJudgedPerOrderLine(FrappeTestCase):
	"""PUR-ORD-2026-00472: lines 1 and 2 taken into stock by one invoice, line 3 billed on another."""

	def test_an_untouched_line_of_a_part_received_order_is_not_received(self):
		# the order sits at 50% received, but nothing has arrived on r3
		with Lines({"r1": (2, 2), "r2": (1, 1), "r3": (3, 0)}, received_orders={"PO-1"}):
			self.assertEqual(poi._received_rows(invoice([ordered(line="r3")])), [])

	def test_a_line_with_a_receipt_is_received_even_a_draft_one(self):
		with Lines({"r1": (3, 0)}, receipted={"r1"}):
			inv = invoice([ordered(line="r1")])
			self.assertEqual(poi._received_rows(inv), inv["items"])

	def test_one_received_line_is_enough_to_take_the_whole_invoice_off(self):
		with Lines({"r1": (2, 0), "r3": (3, 0)}, receipted={"r1"}):
			first, third = ordered("A", line="r1"), ordered("C", line="r3")
			self.assertEqual(poi._received_rows(invoice([first, third])), [first])

	def test_the_rest_of_a_line_an_earlier_ticked_invoice_part_received_is_not_received(self):
		with Lines({"r1": (10, 4)}):
			self.assertEqual(poi._received_rows(invoice([ordered(qty=6)])), [])
			self.assertTrue(poi._received_rows(invoice([ordered(qty=7)])), "more than the line still awaits")

	def test_two_rows_of_one_line_count_together(self):
		with Lines({"r1": (10, 4)}):
			self.assertEqual(poi._received_rows(invoice([ordered(qty=3), ordered(qty=3)])), [])
			self.assertEqual(len(poi._received_rows(invoice([ordered(qty=3), ordered(qty=4)]))), 2)

	def test_a_row_that_names_no_line_is_judged_by_its_order(self):
		loose = row(purchase_order="PO-1", qty=1)
		with Lines(received_orders={"PO-1"}):
			self.assertEqual(poi._received_rows(invoice([loose])), [loose])
		with Lines(receipt_orders={"PO-1"}):
			self.assertEqual(poi._received_rows(invoice([loose])), [loose])
		with Lines():
			self.assertEqual(poi._received_rows(invoice([loose])), [])

	def test_a_row_that_bills_a_receipt_is_received_without_a_read(self):
		billed = row(purchase_receipt="MAT-PRE-1")
		with Lines():
			self.assertEqual(poi._received_rows(invoice([billed])), [billed])

	def test_a_direct_row_is_never_received(self):
		with Lines():
			self.assertEqual(poi._received_rows(invoice([row()])), [])


class TestTheUntickNamesWhatArrived(FrappeTestCase):
	def untick(self, doc, live=True, receipt=False):
		with Seams(live=live, receipt=receipt) as s:
			poi.untick_stock_when_the_goods_already_arrived(doc)
			return s.msgprint

	def test_an_untouched_line_keeps_its_tick(self):
		doc = invoice([ordered(line="r3")], update_stock=1)
		self.untick(doc).assert_not_called()
		self.assertEqual(doc.update_stock, 1)

	def test_a_received_line_is_unticked_and_named(self):
		doc = invoice([ordered("110201040016", line="r3")], update_stock=1)
		said = self.untick(doc, receipt=True)
		self.assertEqual(doc.update_stock, 0)
		self.assertIn("110201040016", said.call_args.args[0])

	def test_a_closed_order_says_so(self):
		doc = invoice([ordered()], update_stock=1)
		said = self.untick(doc, live=False)
		self.assertEqual(doc.update_stock, 0)
		self.assertIn("Closed", said.call_args.args[0])


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
