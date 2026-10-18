# sf_trading/tests/test_lcv_with_invoice.py
"""A Landed Cost Voucher travels with the import Purchase Invoice it costs (sf_trading/landed_cost_with_invoice.py).

The database is patched at the module's own seams, so nothing is created or read:

    bench --site <scratch-site> run-tests --module sf_trading.tests.test_lcv_with_invoice

Run against a live site's data instead with the end-to-end script in the delivery notes (rolled back).
"""

import ast
import json
import os
import re
import inspect
from unittest.mock import MagicMock, patch

import frappe
from frappe.tests.utils import FrappeTestCase

from sf_trading import landed_cost_with_invoice as lcv

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def read(*parts):
	with open(os.path.join(ROOT, *parts), encoding="utf-8") as f:
		return f.read()


class Doc(frappe._dict):
	"""Just enough of a Document: attribute access, get(), flags, and the few methods the hooks call."""

	def __init__(self, **values):
		super().__init__(**values)
		if "flags" not in self:
			self["flags"] = frappe._dict()

	def get_doc_before_save(self):
		return self.get("_before")

	def is_new(self):
		return not self.get("name")


def row(name, item="STEEL-1", qty=10, amount=100.0, idx=1, **extra):
	return frappe._dict(name=name, item_code=item, qty=qty, base_amount=amount, idx=idx, **extra)


def invoice(**values):
	base = dict(doctype="Purchase Invoice", name="PINV-1", company="Steel Force Trading WLL", currency="SAR", update_stock=1,
		is_return=0, docstatus=0, workflow_state="Draft", supplier="SUP-1", base_grand_total=100.0,
		items=[row("r1", "STEEL-1", 10, 100.0, 1)])
	base.update(values)
	return Doc(**base)


def voucher(name="MAT-LCV-1", items=None, **values):
	items = items if items is not None else [frappe._dict(purchase_receipt_item="r1", item_code="STEEL-1", qty=10, amount=100.0)]
	base = dict(doctype="Landed Cost Voucher", name=name, company="Steel Force Trading WLL", docstatus=0, items=items,
		purchase_receipts=[frappe._dict(receipt_document_type="Purchase Invoice", receipt_document="PINV-1", idx=1)],
		distribute_charges_based_on="Qty", total_taxes_and_charges=50.0, owner="buyer@example.com")
	base.update(values)
	return Doc(**base)


class Seams:
	"""Patches the module's own seams, each to a settable value."""

	def __init__(self, **overrides):
		self.values = dict(require=True, currency="BHD", drafts=[], submitted=[], problems=[], one_document=True,
			stock=True, info=None, strict_problems=None, pending=[], calls=[])
		self.values.update(overrides)
		self.patches = []

	def __enter__(self):
		v = self.values
		stock_flags = {"STEEL-1": (1, 0), "SVC-1": (0, 0), "ASSET-1": (0, 1)}
		table = {
			"require_with_import": lambda: v["require"],
			"company_currency": lambda company: v["currency"],
			"vouchers_of": lambda name, docstatus=None: v["drafts"] if docstatus == 0 else (v["submitted"] if docstatus == 1 else v["drafts"] + v["submitted"]),
			# strict is the final check, made when the invoice is about to be submitted; it may know more
			"preflight": lambda name, doc, strict=False: (v["calls"].append(strict),
				v["strict_problems"] if strict and v["strict_problems"] is not None else v["problems"])[1],
			"pending_entries": lambda name: v["pending"],
			"names_one_document": lambda name: v["one_document"],
			"invoice_info": lambda name: v["info"],
		}
		for name, fn in table.items():
			p = patch.object(lcv, name, side_effect=fn)
			p.start()
			self.patches.append(p)
		for target, attr, value in [
			(lcv.frappe, "get_cached_value", MagicMock(side_effect=lambda dt, name, fields: stock_flags.get(name, (1, 0)))),
			(lcv.frappe, "msgprint", MagicMock()),
			(lcv.frappe, "clear_document_cache", MagicMock()),
			(lcv.frappe, "delete_doc", MagicMock()),
			(lcv.frappe, "get_doc", MagicMock()),
		]:
			p = patch.object(target, attr, value, create=True)
			p.start()
			self.patches.append(p)
		self.frappe = lcv.frappe
		return self

	def __exit__(self, *exc):
		for p in reversed(self.patches):
			p.stop()


def draft(name="MAT-LCV-1", **extra):
	return frappe._dict(name=name, docstatus=0, owner="buyer@example.com", total_taxes_and_charges=50.0,
		distribute_charges_based_on="Qty", **extra)


def refused(testcase, fn, *args, text=None):
	with testcase.assertRaises(frappe.ValidationError) as caught:
		fn(*args)
	if text:
		testcase.assertIn(text, str(caught.exception))
	return str(caught.exception)


class TestWhatIsAnImport(FrappeTestCase):
	def test_a_foreign_currency_stock_invoice_is_an_import(self):
		with Seams():
			self.assertTrue(lcv.is_import(invoice(currency="SAR")))

	def test_a_bayan_number_makes_a_company_currency_invoice_an_import(self):
		with Seams():
			self.assertTrue(lcv.is_import(invoice(currency="BHD", custom_bayan_value="202623014102935")))

	def test_a_local_purchase_is_not(self):
		with Seams():
			self.assertFalse(lcv.is_import(invoice(currency="BHD")))
			self.assertFalse(lcv.is_import(invoice(currency="BHD", custom_bayan_value="   ")))

	def test_an_invoice_that_does_not_update_stock_is_not(self):
		with Seams():
			self.assertFalse(lcv.is_import(invoice(update_stock=0)))

	def test_a_return_is_not(self):
		with Seams():
			self.assertFalse(lcv.is_import(invoice(is_return=1)))

	def test_a_voucher_is_required_only_while_the_switch_is_on_and_the_import_is_not_opted_out(self):
		with Seams(require=True):
			self.assertTrue(lcv.must_have_voucher(invoice()))
			self.assertFalse(lcv.must_have_voucher(invoice(custom_import_without_lcv=1)))
		with Seams(require=False):
			self.assertFalse(lcv.must_have_voucher(invoice()))

	def test_the_switch_defaults_to_on_when_it_was_never_saved(self):
		# a Check that was never saved has no row: reading it with get_single_value gives 0, which is "off"
		for row, expected in (({}, True), ({lcv.SWITCH: None}, True), ({lcv.SWITCH: ""}, True),
				({lcv.SWITCH: "0"}, False), ({lcv.SWITCH: "1"}, True), ({lcv.SWITCH: 0}, False)):
			with patch.object(lcv.frappe.db, "get_singles_dict", return_value=row, create=True):
				self.assertEqual(lcv.require_with_import(), expected, row)

	def test_the_switch_is_not_read_through_get_single_value(self):
		with patch.object(lcv.frappe.db, "get_single_value", side_effect=AssertionError("get_single_value reads a missing Check as 0"), create=True):
			with patch.object(lcv.frappe.db, "get_singles_dict", return_value={}, create=True):
				self.assertTrue(lcv.require_with_import())


class TestDoesTheVoucherMatch(FrappeTestCase):
	def test_a_voucher_drawn_from_the_invoice_matches(self):
		with Seams():
			self.assertEqual(lcv.differences(voucher(), invoice()), [])

	def test_a_changed_quantity_is_a_difference(self):
		with Seams():
			diff = lcv.differences(voucher(), invoice(items=[row("r1", qty=12, amount=100.0)]))
		self.assertEqual(len(diff), 1)
		self.assertIn("row 1", diff[0])

	def test_a_changed_amount_is_a_difference(self):
		with Seams():
			self.assertTrue(lcv.differences(voucher(), invoice(items=[row("r1", qty=10, amount=140.0)])))

	def test_a_sub_fils_rounding_is_not(self):
		with Seams():
			self.assertEqual(lcv.differences(voucher(), invoice(items=[row("r1", qty=10, amount=100.0001)])), [])

	def test_a_row_added_to_the_invoice_is_a_difference(self):
		with Seams():
			diff = lcv.differences(voucher(), invoice(items=[row("r1"), row("r2", "STEEL-1", 5, 50.0, 2)]))
		self.assertEqual(len(diff), 1)
		self.assertIn("not on the voucher", diff[0])

	def test_a_row_removed_from_the_invoice_is_a_difference(self):
		with Seams():
			diff = lcv.differences(voucher(), invoice(items=[]))
		self.assertEqual(len(diff), 1)
		self.assertIn("no longer on the invoice", diff[0])

	def test_a_row_not_saved_yet_is_a_difference(self):
		with Seams():
			diff = lcv.differences(voucher(), invoice(items=[row("r1"), frappe._dict(name=None, item_code="STEEL-1", qty=1, base_amount=1.0, idx=2)]))
		self.assertTrue(any("not saved yet" in d for d in diff))

	def test_a_service_row_is_not_spread_over(self):
		with Seams():
			self.assertEqual(lcv.differences(voucher(), invoice(items=[row("r1"), row("r2", "SVC-1", 1, 30.0, 2)])), [])

	def test_a_different_company_is_a_difference(self):
		with Seams():
			self.assertTrue(lcv.differences(voucher(company="Other Co"), invoice()))


class TestTheGateToApproval(FrappeTestCase):
	def sent(self, **values):
		"""An invoice saved as Pending, which was Draft."""
		return invoice(workflow_state="Pending", _before=Doc(update_stock=1, workflow_state="Draft"), **values)

	def test_an_import_with_no_voucher_cannot_go_for_approval(self):
		with Seams(drafts=[]):
			refused(self, lcv.validate_invoice, self.sent(), text="needs a Landed Cost Voucher")

	def test_an_import_with_a_ready_voucher_goes(self):
		with Seams(drafts=[draft()], problems=[]):
			lcv.validate_invoice(self.sent())

	def test_a_voucher_that_would_not_submit_stops_it_with_the_reason(self):
		with Seams(drafts=[draft()], problems=["it carries no charges"]):
			refused(self, lcv.validate_invoice, self.sent(), text="it carries no charges")

	def test_a_local_purchase_needs_no_voucher(self):
		with Seams(drafts=[], currency="BHD"):
			lcv.validate_invoice(self.sent(currency="BHD"))

	def test_a_voucher_on_a_local_purchase_is_still_checked(self):
		with Seams(drafts=[draft()], problems=["it carries no charges"], currency="BHD"):
			refused(self, lcv.validate_invoice, self.sent(currency="BHD"), text="no charges")

	def test_an_opted_out_import_goes_without_one(self):
		with Seams(drafts=[]):
			lcv.validate_invoice(self.sent(custom_import_without_lcv=1, custom_import_without_lcv_reason="CIF delivered"))

	def test_the_switch_off_lets_an_import_through(self):
		with Seams(drafts=[], require=False):
			lcv.validate_invoice(self.sent())

	def test_a_move_between_approval_steps_is_not_the_gate(self):
		doc = invoice(workflow_state="Pending Accounts", _before=Doc(update_stock=1, workflow_state="Pending"))
		with Seams(drafts=[]):
			lcv.validate_invoice(doc)

	def test_a_return_to_draft_is_not_the_gate(self):
		doc = invoice(workflow_state="Rejected", _before=Doc(update_stock=1, workflow_state="Pending"))
		with Seams(drafts=[]):
			lcv.validate_invoice(doc)

	def test_a_rejected_invoice_sent_again_is_the_gate_again(self):
		doc = invoice(workflow_state="Pending", _before=Doc(update_stock=1, workflow_state="Rejected"))
		with Seams(drafts=[]):
			refused(self, lcv.validate_invoice, doc, text="needs a Landed Cost Voucher")

	def test_a_draft_expense_entry_is_allowed_at_the_gate_with_a_warning(self):
		with Seams(drafts=[draft()], problems=[], pending=[("Purchase Invoice", "PI-FREIGHT")]) as s:
			lcv.validate_invoice(self.sent())
			s.frappe.msgprint.assert_called_once()
			text = s.frappe.msgprint.call_args.args[0]
			self.assertIn("still a draft", text)
			self.assertIn("PI-FREIGHT", text)
			self.assertEqual(s.values["calls"], [False])

	def test_no_warning_when_every_expense_entry_is_submitted(self):
		with Seams(drafts=[draft()], problems=[], pending=[]) as s:
			lcv.validate_invoice(self.sent())
			s.frappe.msgprint.assert_not_called()

	def test_a_plain_draft_save_is_not_gated(self):
		doc = invoice(workflow_state="Draft", _before=Doc(update_stock=1, workflow_state="Draft"))
		with Seams(drafts=[]):
			lcv.validate_invoice(doc)

	def test_a_new_invoice_is_not_gated(self):
		with Seams(drafts=[]):
			lcv.validate_invoice(invoice(name=None, workflow_state="Pending"))

	def test_a_submitted_invoice_is_not_revalidated(self):
		with Seams(drafts=[]):
			lcv.validate_invoice(invoice(docstatus=1, workflow_state="Approved", _before=Doc(update_stock=1, workflow_state="Pending")))

	def test_no_workflow_field_means_the_final_check_decides(self):
		with Seams(drafts=[]):
			lcv.validate_invoice(invoice(workflow_state=None, _before=Doc(update_stock=1)))


class TestAVoucherIsNotOrphaned(FrappeTestCase):
	def saved(self, **values):
		return invoice(_before=Doc(update_stock=1, workflow_state="Draft"), **values)

	def test_unticking_update_stock_with_a_voucher_attached_is_refused(self):
		with Seams(drafts=[draft("MAT-LCV-7")]):
			refused(self, lcv.validate_invoice, self.saved(update_stock=0), text="MAT-LCV-7")

	def test_fixed_asset_rows_with_a_voucher_attached_are_refused(self):
		with Seams(drafts=[draft()]):
			refused(self, lcv.validate_invoice, self.saved(items=[row("r1", is_fixed_asset=1)]), text="fixed-asset")

	def test_opting_out_with_a_voucher_attached_is_refused(self):
		with Seams(drafts=[draft()]):
			refused(self, lcv.validate_invoice, self.saved(custom_import_without_lcv=1, custom_import_without_lcv_reason="x"),
				text="carrying no landed cost")

	def test_opting_out_needs_a_reason(self):
		with Seams():
			refused(self, lcv.validate_invoice, self.saved(custom_import_without_lcv=1), text="Say why")


class TestTheFinalCheck(FrappeTestCase):
	def test_submitting_an_import_with_no_voucher_is_refused(self):
		with Seams(drafts=[]):
			refused(self, lcv.require_voucher_at_submit, invoice(), text="needs a Landed Cost Voucher")

	def test_a_ready_voucher_passes(self):
		with Seams(drafts=[draft()], problems=[]):
			lcv.require_voucher_at_submit(invoice())

	def test_a_voucher_that_would_not_submit_is_refused_with_its_reason_not_silently_changed(self):
		with Seams(drafts=[draft()], problems=["it does not match this invoice yet: row 1"]):
			refused(self, lcv.require_voucher_at_submit, invoice(), text="does not match this invoice")

	def test_the_final_check_is_strict_and_refuses_while_an_expense_entry_is_a_draft(self):
		reason = "the cost it takes was booked by Purchase Invoice PI-F, which is still a draft: submit that first"
		with Seams(drafts=[draft()], problems=[], strict_problems=[reason]) as s:
			refused(self, lcv.require_voucher_at_submit, invoice(), text="still a draft")
			self.assertEqual(s.values["calls"], [True])

	def test_the_final_check_passes_once_the_entries_are_submitted(self):
		with Seams(drafts=[draft()], problems=[], strict_problems=[]) as s:
			lcv.require_voucher_at_submit(invoice())
			self.assertEqual(s.values["calls"], [True])

	def test_a_return_is_not_asked(self):
		with Seams(drafts=[]):
			lcv.require_voucher_at_submit(invoice(is_return=1))

	def test_an_opted_out_import_passes(self):
		with Seams(drafts=[]):
			lcv.require_voucher_at_submit(invoice(custom_import_without_lcv=1))


class TestPreflight(FrappeTestCase):
	"""The real dry run, with the voucher it loads and the draft expense entries it finds faked."""

	def run_it(self, strict, waiting=(), total=50.0, validate_error=None, diff=()):
		doc = voucher(total_taxes_and_charges=total)
		doc["run_method"] = MagicMock(side_effect=validate_error)
		doc["validate_applicable_charges_for_item"] = MagicMock()
		with patch.object(lcv.frappe, "get_doc", return_value=doc, create=True), \
				patch.object(lcv, "differences", return_value=list(diff)), \
				patch.object(lcv.landed_cost, "draft_expense_entries", return_value=list(waiting)):
			return lcv.preflight("MAT-LCV-1", invoice(), strict=strict)

	def test_a_ready_voucher_has_no_problems(self):
		self.assertEqual(self.run_it(False), [])
		self.assertEqual(self.run_it(True), [])

	def test_a_draft_expense_entry_is_no_problem_until_the_final_check(self):
		waiting = [("Purchase Invoice", "PI-F")]
		self.assertEqual(self.run_it(False, waiting), [])
		problems = self.run_it(True, waiting)
		self.assertEqual(len(problems), 1)
		self.assertIn("PI-F", problems[0])
		self.assertIn("still a draft", problems[0])

	def test_a_mismatch_is_reported_first(self):
		problems = self.run_it(True, [("Purchase Invoice", "PI-F")], diff=["row 1 changed"])
		self.assertEqual(len(problems), 1)
		self.assertIn("does not match", problems[0])

	def test_a_voucher_with_no_charges_is_a_problem(self):
		self.assertEqual(len(self.run_it(False, total=0)), 1)

	def test_a_refusal_by_the_vouchers_own_checks_is_reported_with_its_words(self):
		problems = self.run_it(True, validate_error=frappe.ValidationError("<b>JV-1</b> booked less than that"))
		self.assertEqual(problems, ["JV-1 booked less than that"])


class TestWhatADraftWillBook(FrappeTestCase):
	"""landed_cost.booked_by_draft: what a draft expense entry is going to post, read from the document."""

	def read(self, tables, account=None, doctype="Journal Entry", stock=("STOCK-1",)):
		from sf_trading import landed_cost as lc

		def get_all(dt, filters=None, fields=None, **kwargs):
			return [frappe._dict(r) for r in tables.get(dt, [])]

		with patch.object(lc.frappe, "get_all", side_effect=get_all, create=True), \
				patch.object(lc.frappe, "get_cached_value", side_effect=lambda dt, name, field: 1 if name in stock else 0, create=True):
			return lc.booked_by_draft(doctype, "X-1", account)

	def test_a_journal_books_its_net_debits(self):
		tables = {"Journal Entry Account": [
			{"account": "Freight - SF", "debit": 100, "credit": 0},
			{"account": "Freight - SF", "debit": 0, "credit": 10},
			{"account": "Bank - SF", "debit": 0, "credit": 90}]}
		self.assertEqual(self.read(tables), {"Freight - SF": 90.0, "Bank - SF": -90.0})
		self.assertEqual(self.read(tables, account="Freight - SF"), {"Freight - SF": 90.0})

	def test_a_bill_books_its_non_stock_rows_and_its_total_taxes(self):
		tables = {
			"Purchase Invoice Item": [
				{"item_code": "SVC-1", "expense_account": "Freight - SF", "base_net_amount": 80},
				{"item_code": "SVC-2", "expense_account": "Freight - SF", "base_net_amount": 20},
				{"item_code": "STOCK-1", "expense_account": "Inventory - SF", "base_net_amount": 200},
				{"item_code": "SVC-3", "expense_account": None, "base_net_amount": 5},
			],
			"Purchase Taxes and Charges": [
				{"account_head": "VAT - SF", "base_tax_amount": 10, "add_deduct_tax": "Add", "category": "Total"},
				{"account_head": "Freight - SF", "base_tax_amount": 5, "add_deduct_tax": "Deduct", "category": "Total"},
				{"account_head": "Duty - SF", "base_tax_amount": 7, "add_deduct_tax": "Add", "category": "Valuation"},
				{"account_head": "Port - SF", "base_tax_amount": 3, "add_deduct_tax": "Add", "category": "Valuation and Total"},
			],
		}
		self.assertEqual(self.read(tables, doctype="Purchase Invoice"), {"Freight - SF": 95.0, "VAT - SF": 10.0, "Port - SF": 3.0})

	def test_a_payment_is_not_read_ahead(self):
		self.assertEqual(self.read({"Journal Entry Account": [{"account": "Freight - SF", "debit": 5, "credit": 0}]}, doctype="Payment Entry"), {})

	def test_an_empty_draft_books_nothing(self):
		self.assertEqual(self.read({}), {})
		self.assertEqual(self.read({}, doctype="Purchase Invoice"), {})


class TestTheExpenseEntryList(FrappeTestCase):
	def query(self, doctype, filters, rows):
		from sf_trading import landed_cost as lc

		seen = {}

		def get_list(dt, filters=None, fields=None, **kwargs):
			seen["filters"], seen["fields"] = filters, fields
			return [frappe._dict(r) for r in rows]

		with patch.object(lc.frappe, "get_list", side_effect=get_list, create=True):
			return lc.expense_entry_query(doctype, "", "name", 0, 20, filters), seen

	ROWS = [
		{"name": "PI-SUB", "posting_date": "2026-10-10", "supplier_name": "Agent", "base_grand_total": 80.0, "docstatus": 1},
		{"name": "PI-DRAFT", "posting_date": "2026-10-17", "supplier_name": "Agent", "base_grand_total": 10.0, "docstatus": 0},
	]

	def test_submitted_entries_only_by_default(self):
		_out, seen = self.query("Purchase Invoice", {"company": "SF"}, self.ROWS[:1])
		self.assertEqual(seen["filters"]["docstatus"], 1)

	def test_a_draft_voucher_is_offered_drafts_of_a_bill_or_journal_marked_as_draft(self):
		out, seen = self.query("Purchase Invoice", {"company": "SF", "allow_draft": 1}, self.ROWS)
		self.assertEqual(seen["filters"]["docstatus"], ["in", [0, 1]])
		self.assertEqual(out[0][0], "PI-SUB")
		self.assertNotIn("Draft", out[0])
		self.assertEqual(out[1][:2], ["PI-DRAFT", "Draft"])
		out, seen = self.query("Journal Entry", {"company": "SF", "allow_draft": 1}, [])
		self.assertEqual(seen["filters"]["docstatus"], ["in", [0, 1]])

	def test_a_payment_is_never_offered_as_a_draft(self):
		_out, seen = self.query("Payment Entry", {"company": "SF", "allow_draft": 1}, [])
		self.assertEqual(seen["filters"]["docstatus"], 1)


class TestSubmittedWithTheInvoice(FrappeTestCase):
	def make(self, names):
		made = {}
		order = []
		for n in names:
			v = voucher(n)
			v["submit"] = MagicMock(side_effect=lambda n=n: order.append(n))
			made[n] = v
		return made, order

	def test_every_draft_voucher_is_submitted_after_the_cache_is_cleared(self):
		made, order = self.make(["A", "B"])
		with Seams(drafts=[draft("A"), draft("B")]) as s:
			s.frappe.get_doc.side_effect = lambda dt, name: made[name]
			lcv.submit_vouchers(invoice(docstatus=1))
			s.frappe.clear_document_cache.assert_called_once_with("Purchase Invoice", "PINV-1")
		self.assertEqual(order, ["A", "B"])

	def test_the_system_flag_and_permission_flag_are_set(self):
		made, _order = self.make(["A"])
		with Seams(drafts=[draft("A")]) as s:
			s.frappe.get_doc.side_effect = lambda dt, name: made[name]
			lcv.submit_vouchers(invoice(docstatus=1))
		self.assertTrue(made["A"].flags.get(lcv.SYSTEM_FLAG))
		self.assertTrue(made["A"].flags.get("ignore_permissions"))

	def test_a_voucher_that_fails_stops_the_whole_submit(self):
		made, _order = self.make(["A"])
		made["A"]["submit"] = MagicMock(side_effect=frappe.ValidationError("expense booked less"))
		with Seams(drafts=[draft("A")]) as s:
			s.frappe.get_doc.side_effect = lambda dt, name: made[name]
			refused(self, lcv.submit_vouchers, invoice(docstatus=1), text="expense booked less")

	def test_nothing_to_submit_does_nothing(self):
		with Seams(drafts=[]) as s:
			lcv.submit_vouchers(invoice(docstatus=1))
			s.frappe.clear_document_cache.assert_not_called()
			s.frappe.get_doc.assert_not_called()


class TestCancelledAndDeletedWithTheInvoice(FrappeTestCase):
	def test_the_submitted_vouchers_are_cancelled_first(self):
		made = {"A": voucher("A", docstatus=1), "B": voucher("B", docstatus=1)}
		for v in made.values():
			v["cancel"] = MagicMock()
		with Seams(submitted=[frappe._dict(name="A"), frappe._dict(name="B")]) as s:
			s.frappe.get_doc.side_effect = lambda dt, name: made[name]
			lcv.cancel_vouchers(invoice(docstatus=1))
		for v in made.values():
			v.cancel.assert_called_once_with()
			self.assertTrue(v.flags.get(lcv.SYSTEM_FLAG))

	def test_a_voucher_shared_with_other_documents_is_left_to_core(self):
		made = {"A": voucher("A", docstatus=1)}
		made["A"]["cancel"] = MagicMock()
		with Seams(submitted=[frappe._dict(name="A")], one_document=False) as s:
			s.frappe.get_doc.side_effect = lambda dt, name: made[name]
			lcv.cancel_vouchers(invoice(docstatus=1))
		made["A"].cancel.assert_not_called()

	def test_a_deleted_draft_takes_its_draft_vouchers_with_it(self):
		with Seams(drafts=[draft("A")]) as s:
			seen = {}
			s.frappe.delete_doc.side_effect = lambda *a, **k: seen.update(flag=s.frappe.flags.get(lcv.DELETING_FLAG))
			lcv.delete_vouchers(invoice())
			s.frappe.delete_doc.assert_called_once_with("Landed Cost Voucher", "A", force=True, ignore_permissions=True)
		self.assertTrue(seen["flag"])
		self.assertFalse(frappe.flags.get(lcv.DELETING_FLAG))


class TestTheVoucherSide(FrappeTestCase):
	def draft_info(self, state="Draft"):
		return frappe._dict(docstatus=0, workflow_state=state, update_stock=1, company="Steel Force Trading WLL")

	def test_a_voucher_naming_a_draft_invoice_and_something_else_is_refused(self):
		doc = voucher(purchase_receipts=[
			frappe._dict(receipt_document_type="Purchase Invoice", receipt_document="PINV-1", idx=1),
			frappe._dict(receipt_document_type="Purchase Receipt", receipt_document="PR-1", idx=2)])
		with Seams(info=self.draft_info()):
			with patch.object(lcv.frappe.db, "get_value", return_value=0, create=True):
				refused(self, lcv.validate_voucher, doc, text="names that invoice alone")

	def test_a_voucher_of_an_invoice_out_for_approval_is_locked(self):
		with Seams(info=self.draft_info("Pending")):
			with patch.object(lcv.frappe.db, "get_value", return_value=0, create=True):
				refused(self, lcv.validate_voucher, voucher(), text="out for approval")

	def test_the_system_may_still_change_it(self):
		doc = voucher()
		doc.flags[lcv.SYSTEM_FLAG] = True
		with Seams(info=self.draft_info("Pending")):
			with patch.object(lcv.frappe.db, "get_value", return_value=0, create=True):
				lcv.validate_voucher(doc)

	def test_a_voucher_of_a_draft_invoice_in_draft_or_rejected_is_free(self):
		for state in ("Draft", "Rejected", ""):
			with Seams(info=self.draft_info(state)):
				with patch.object(lcv.frappe.db, "get_value", return_value=0, create=True):
					lcv.validate_voucher(voucher())

	def test_a_voucher_of_a_submitted_invoice_is_not_touched(self):
		with Seams(info=frappe._dict(docstatus=1, workflow_state="Approved")):
			with patch.object(lcv.frappe.db, "get_value", return_value=1, create=True):
				lcv.validate_voucher(voucher())

	def test_a_submitted_voucher_is_not_revalidated(self):
		with Seams():
			lcv.validate_voucher(voucher(docstatus=1))

	def test_deleting_the_voucher_of_an_invoice_out_for_approval_is_refused(self):
		with Seams(info=self.draft_info("Pending Accounts")):
			refused(self, lcv.guard_voucher_trash, voucher(), text="Reject the invoice first")

	def test_deleting_it_while_the_invoice_is_a_draft_is_fine(self):
		with Seams(info=self.draft_info("Draft")):
			lcv.guard_voucher_trash(voucher())

	def test_the_invoices_own_deletion_may_remove_it(self):
		frappe.flags[lcv.DELETING_FLAG] = True
		try:
			with Seams(info=self.draft_info("Pending")):
				lcv.guard_voucher_trash(voucher())
		finally:
			frappe.flags.pop(lcv.DELETING_FLAG, None)

	def test_cancelling_a_voucher_on_its_own_says_the_invoice_now_has_no_landed_cost(self):
		with Seams(info=frappe._dict(docstatus=1, workflow_state="Approved")) as s:
			lcv.remind_after_cancel(voucher(docstatus=2))
			s.frappe.msgprint.assert_called_once()

	def test_the_system_cancelling_with_the_invoice_says_nothing_more(self):
		doc = voucher(docstatus=2)
		doc.flags[lcv.SYSTEM_FLAG] = True
		with Seams(info=frappe._dict(docstatus=1, workflow_state="Approved")) as s:
			lcv.remind_after_cancel(doc)
			s.frappe.msgprint.assert_not_called()


class TestFollowingTheInvoice(FrappeTestCase):
	def drawn(self, **values):
		doc = voucher("A", docstatus=0, **values)
		doc["save"] = MagicMock()
		doc["add_comment"] = MagicMock()
		doc["get_items_from_purchase_receipts"] = MagicMock(
			side_effect=lambda: doc.__setitem__("items", [frappe._dict(purchase_receipt_item="r1", item_code="STEEL-1", qty=12, amount=100.0, applicable_charges=0)]))
		return doc

	def test_an_unchanged_invoice_leaves_the_voucher_alone(self):
		doc = self.drawn()
		with Seams(drafts=[draft("A")]) as s:
			s.frappe.get_doc.side_effect = lambda dt, name: doc
			lcv.follow_invoice(invoice())
		doc.save.assert_not_called()

	def test_a_changed_invoice_rebuilds_the_voucher_and_says_so(self):
		doc = self.drawn()
		with Seams(drafts=[draft("A")]) as s, patch.object(lcv.frappe, "session", frappe._dict(user="buyer@example.com"), create=True):
			s.frappe.get_doc.side_effect = lambda dt, name: doc
			lcv.follow_invoice(invoice(items=[row("r1", qty=12, amount=100.0)]))
			s.frappe.msgprint.assert_called_once()
		doc.get_items_from_purchase_receipts.assert_called_once_with()
		doc.save.assert_called_once_with()
		doc.add_comment.assert_called_once()
		self.assertTrue(doc.flags.get(lcv.SYSTEM_FLAG))

	def test_a_voucher_distributed_by_hand_cannot_follow_a_changed_row_set(self):
		doc = self.drawn(distribute_charges_based_on="Distribute Manually")
		doc["get_items_from_purchase_receipts"] = MagicMock(
			side_effect=lambda: doc.__setitem__("items", [frappe._dict(purchase_receipt_item="r2", item_code="STEEL-1", qty=1, amount=1.0)]))
		with Seams(drafts=[draft("A")]) as s:
			s.frappe.get_doc.side_effect = lambda dt, name: doc
			refused(self, lcv.follow_invoice, invoice(items=[row("r2", qty=1, amount=1.0)]), text="spreads its charges by hand")
		doc.save.assert_not_called()

	def test_a_voucher_that_cannot_follow_stops_the_invoice_save_and_names_it(self):
		doc = self.drawn()
		doc["save"] = MagicMock(side_effect=frappe.ValidationError("Total is zero"))
		with Seams(drafts=[draft("A")]) as s:
			s.frappe.get_doc.side_effect = lambda dt, name: doc
			refused(self, lcv.follow_invoice, invoice(items=[row("r1", qty=12, amount=100.0)]), text="could not follow")

	def test_a_submitted_or_non_stock_invoice_is_not_followed(self):
		with Seams(drafts=[draft("A")]) as s:
			lcv.follow_invoice(invoice(docstatus=1))
			lcv.follow_invoice(invoice(update_stock=0))
			s.frappe.get_doc.assert_not_called()


class TestMakingTheVoucher(FrappeTestCase):
	def fake_invoice(self, **values):
		doc = invoice(**values)
		doc["check_permission"] = MagicMock()
		return doc

	def go(self, doc):
		made = voucher()
		made.pop("purchase_receipts")
		made["purchase_receipts"] = []
		made["append"] = lambda table, values: made[table].append(frappe._dict(values))
		made["get_items_from_purchase_receipts"] = MagicMock()
		made["taxes"] = []
		with Seams(submitted=[]) as s:
			s.frappe.get_doc.side_effect = lambda dt, name: doc
			with patch.object(lcv.frappe, "new_doc", return_value=made, create=True):
				return lcv.make_landed_cost_voucher("PINV-1"), made

	def test_it_names_the_invoice_and_reads_its_rows(self):
		_result, made = self.go(self.fake_invoice())
		self.assertEqual(made["purchase_receipts"][0].receipt_document, "PINV-1")
		made.get_items_from_purchase_receipts.assert_called_once_with()
		self.assertEqual(made.distribute_charges_based_on, "Qty")

	def test_a_submitted_invoice_is_refused(self):
		with self.assertRaises(frappe.ValidationError):
			self.go(self.fake_invoice(docstatus=1))

	def test_an_invoice_that_does_not_update_stock_is_refused(self):
		with self.assertRaises(frappe.ValidationError):
			self.go(self.fake_invoice(update_stock=0))

	def test_a_return_is_refused(self):
		with self.assertRaises(frappe.ValidationError):
			self.go(self.fake_invoice(is_return=1))

	def test_fixed_asset_rows_are_refused(self):
		with self.assertRaises(frappe.ValidationError):
			self.go(self.fake_invoice(items=[row("r1", is_fixed_asset=1)]))

	def test_an_invoice_out_for_approval_is_refused(self):
		with self.assertRaises(frappe.ValidationError):
			self.go(self.fake_invoice(workflow_state="Pending"))


class TestTheOverride(FrappeTestCase):
	"""core's "must be submitted" rule is relaxed for a draft voucher naming one draft stock invoice, and for
	nothing else (sf_trading/overrides/landed_cost_voucher_class.py)."""

	def setUp(self):
		try:
			from sf_trading.overrides import landed_cost_voucher_class as module
		except ImportError:
			self.skipTest("erpnext is not importable here")
		self.module = module

	def make(self, docstatus=0, rows=None, items=None, company="Steel Force Trading WLL"):
		cls = self.module.CustomLandedCostVoucher
		obj = cls.__new__(cls)
		obj.docstatus = docstatus
		obj.company = company
		obj.purchase_receipts = rows if rows is not None else [
			frappe._dict(idx=1, receipt_document_type="Purchase Invoice", receipt_document="PINV-1")]
		obj.items = items if items is not None else [
			frappe._dict(idx=1, item_code="STEEL-1", receipt_document="PINV-1", receipt_document_type="Purchase Invoice", cost_center="Main - SF")]
		return obj

	def values(self, docstatus=0, company="Steel Force Trading WLL", update_stock=1):
		def get_value(doctype, name, fields, *args, **kwargs):
			if fields == "docstatus":
				return docstatus
			return (company, update_stock)
		return patch.object(lcv.frappe.db, "get_value", side_effect=get_value, create=True)

	def test_a_draft_voucher_for_a_draft_stock_invoice_passes_without_core(self):
		with self.values(), patch.object(self.module.LandedCostVoucher, "validate_receipt_documents") as core:
			self.make().validate_receipt_documents()
			core.assert_not_called()

	def test_a_submitted_invoice_goes_to_core(self):
		with self.values(docstatus=1), patch.object(self.module.LandedCostVoucher, "validate_receipt_documents") as core:
			self.make().validate_receipt_documents()
			core.assert_called_once()

	def test_a_purchase_receipt_goes_to_core(self):
		rows = [frappe._dict(idx=1, receipt_document_type="Purchase Receipt", receipt_document="PR-1")]
		with self.values(), patch.object(self.module.LandedCostVoucher, "validate_receipt_documents") as core:
			self.make(rows=rows).validate_receipt_documents()
			core.assert_called_once()

	def test_submitting_a_voucher_while_the_invoice_is_a_draft_is_refused(self):
		with self.values():
			refused(self, self.make(docstatus=1).validate_receipt_documents, text="submitted with the invoice")

	def test_two_documents_on_a_draft_voucher_are_refused(self):
		rows = [frappe._dict(idx=1, receipt_document_type="Purchase Invoice", receipt_document="PINV-1"),
			frappe._dict(idx=2, receipt_document_type="Purchase Invoice", receipt_document="PINV-2")]
		with self.values():
			refused(self, self.make(rows=rows).validate_receipt_documents, text="that invoice alone")

	def test_another_companys_invoice_is_refused(self):
		with self.values(company="Other Co"):
			with self.assertRaises(frappe.ValidationError):
				self.make().validate_receipt_documents()

	def test_an_invoice_that_does_not_update_stock_is_refused(self):
		with self.values(update_stock=0):
			refused(self, self.make().validate_receipt_documents, text="no stock impact")

	def test_an_item_of_another_document_is_refused(self):
		items = [frappe._dict(idx=1, item_code="STEEL-1", receipt_document="PINV-9", receipt_document_type="Purchase Invoice", cost_center="Main - SF")]
		with self.values():
			with self.assertRaises(frappe.ValidationError):
				self.make(items=items).validate_receipt_documents()

	def test_an_item_with_no_cost_centre_is_refused(self):
		items = [frappe._dict(idx=1, item_code="STEEL-1", receipt_document="PINV-1", receipt_document_type="Purchase Invoice", cost_center=None)]
		with self.values():
			refused(self, self.make(items=items).validate_receipt_documents, text="Cost center is required")

	def test_core_still_rests_on_the_wording_the_relaxation_copies(self):
		base = self.module.LandedCostVoucher
		if not str(getattr(base, "__module__", "")).startswith("erpnext"):
			self.skipTest("core's own controller is not loaded here")
		source = inspect.getsource(base.validate_receipt_documents)
		for phrase in ("must be submitted", '"update_stock"', "has no stock impact", "Cost center is required", "does not exist in above"):
			self.assertIn(phrase, source, "core changed its Landed Cost Voucher checks: review the override")


class TestWhatTheSystemShips(FrappeTestCase):
	"""The wiring is part of the control: a hook dropped from hooks.py, or a mark lost from the form, takes the
	rule away without a single test of the logic failing."""

	def hooks(self):
		return read("hooks.py")

	def test_the_invoice_hooks_are_registered_with_the_submit_first(self):
		text = self.hooks()
		on_submit = re.search(r'"on_submit": \[\s*(?:#[^\n]*\n\s*)*"([^"]+)"', text[text.index('"Purchase Invoice": {\n\t\t"before_validate"'):])
		self.assertEqual(on_submit.group(1), "sf_trading.landed_cost_with_invoice.submit_vouchers")
		for hook in ("validate_invoice", "follow_invoice", "require_voucher_at_submit", "cancel_vouchers", "delete_vouchers"):
			self.assertIn("sf_trading.landed_cost_with_invoice." + hook, text)

	def test_the_voucher_hooks_and_class_override_are_registered(self):
		text = self.hooks()
		for hook in ("validate_voucher", "guard_voucher_trash", "remind_after_cancel"):
			self.assertIn("sf_trading.landed_cost_with_invoice." + hook, text)
		self.assertIn('"Landed Cost Voucher": "sf_trading.overrides.landed_cost_voucher_class.CustomLandedCostVoucher"', text)

	def test_the_fields_and_the_form_script_are_registered(self):
		text = self.hooks()
		self.assertIn("sf_trading.landed_cost_with_invoice.ensure_custom_fields", text)
		self.assertIn("public/js/purchase_invoice_lcv.js", text)

	def test_every_hook_named_exists(self):
		tree = ast.parse(read("landed_cost_with_invoice.py"))
		defined = {n.name for n in tree.body if isinstance(n, ast.FunctionDef)}
		for name in re.findall(r"sf_trading\.landed_cost_with_invoice\.(\w+)", self.hooks()):
			self.assertIn(name, defined)

	def test_the_switch_is_on_by_default_in_settings(self):
		fields = {f["fieldname"]: f for f in json.loads(read("sf_trading", "doctype", "sf_trading_settings", "sf_trading_settings.json"))["fields"]}
		self.assertEqual(fields["landed_cost_require_with_import"]["default"], "1")
		self.assertEqual(fields["landed_cost_require_with_import"]["fieldtype"], "Check")

	def test_the_switch_name_in_code_and_in_settings_agree(self):
		self.assertEqual(lcv.SWITCH, "landed_cost_require_with_import")

	def test_the_report_exists(self):
		for ext in ("py", "js", "json"):
			self.assertTrue(os.path.exists(os.path.join(ROOT, "sf_trading", "report", "import_purchases_without_lcv", "import_purchases_without_lcv." + ext)))
