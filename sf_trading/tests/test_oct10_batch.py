# sf_trading/tests/test_oct10_batch.py
"""Purchase Order cancel remark, service lines without a Purchase Order, the Customer cash-sale
override, and the two new reports (pure logic plus a read-only run on live data).

    bench --site <scratch-site> run-tests --module sf_trading.tests.test_oct10_batch
"""

from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import add_days, getdate, today

from sf_trading import purchase_order_cancel as poc
from sf_trading import sales_order_governance as sog
from sf_trading.overrides import purchase_invoice_class as pic
from sf_trading.sf_trading.report.branch_wise_credit_outstanding import branch_wise_credit_outstanding as bwco
from sf_trading.sf_trading.report.delivery_person_payment_pending import delivery_person_payment_pending as dpp


def _company():
	return frappe.db.get_single_value("Global Defaults", "default_company") or frappe.get_all("Company", pluck="name")[0]


class TestPurchaseOrderCancelRemark(FrappeTestCase):
	def test_no_remark_no_cancel(self):
		with self.assertRaises(frappe.ValidationError):
			poc.before_cancel_require_remark(frappe._dict(custom_cancellation_remark="  "))

	def test_a_remark_lets_it_through(self):
		poc.before_cancel_require_remark(frappe._dict(custom_cancellation_remark="Supplier out of stock"))

	def test_the_field_exists_after_setup(self):
		poc.ensure_custom_fields()
		field = frappe.get_meta("Purchase Order").get_field(poc.REMARK_FIELD)
		self.assertIsNotNone(field)
		self.assertEqual(field.allow_on_submit, 1)


class TestServiceLinesNeedNoOrder(FrappeTestCase):
	def _invoice(self, *codes):
		doc = frappe.new_doc("Purchase Invoice")
		doc.supplier = "_any"
		for code in codes:
			doc.append("items", {"item_code": code, "qty": 1, "rate": 1})
		return doc

	def _run(self, doc, kinds):
		def cached_value(doctype, name, fields):
			return kinds[name]

		with patch.object(pic.frappe.db, "get_single_value", return_value="Yes"), patch.object(
			pic.frappe.db, "get_value", return_value=0
		), patch.object(pic.frappe, "get_cached_value", side_effect=cached_value), patch.object(
			type(doc), "is_internal_transfer", return_value=False
		):
			doc.po_required()

	def test_a_service_line_passes(self):
		doc = self._invoice("SVC")
		self._run(doc, {"SVC": (0, 0)})

	def test_a_stock_line_still_needs_its_order(self):
		doc = self._invoice("SVC", "GOODS")
		with self.assertRaises(frappe.ValidationError):
			self._run(doc, {"SVC": (0, 0), "GOODS": (1, 0)})

	def test_a_fixed_asset_still_needs_its_order(self):
		doc = self._invoice("ASSET")
		with self.assertRaises(frappe.ValidationError):
			self._run(doc, {"ASSET": (0, 1)})

	def test_a_line_with_its_order_passes(self):
		doc = self._invoice("GOODS")
		doc.items[0].purchase_order = "PO-1"
		self._run(doc, {"GOODS": (1, 0)})

	def test_the_invoice_class_is_the_override(self):
		self.assertIsInstance(frappe.new_doc("Purchase Invoice"), pic.CustomPurchaseInvoice)


class TestCashSaleOverride(FrappeTestCase):
	def test_cash_with_the_box_ticked_skips_the_document_rule(self):
		with patch.object(sog.frappe.db, "get_value", return_value=1):
			self.assertTrue(sog.cash_sale_overrides_credit_documents(frappe._dict(customer="C", custom_payment_mode="Cash")))

	def test_credit_and_cheque_never_skip(self):
		with patch.object(sog.frappe.db, "get_value", return_value=1):
			for mode in ("Credit", "Cheque", ""):
				self.assertFalse(sog.cash_sale_overrides_credit_documents(frappe._dict(customer="C", custom_payment_mode=mode)))

	def test_cash_without_the_box_does_not_skip(self):
		with patch.object(sog.frappe.db, "get_value", return_value=0):
			self.assertFalse(sog.cash_sale_overrides_credit_documents(frappe._dict(customer="C", custom_payment_mode="Cash")))

	def test_the_gate_lets_a_ticked_cash_sale_through(self):
		doc = frappe._dict(customer="C", customer_name="C", custom_payment_mode="Cash")
		with patch.object(sog, "cash_sale_overrides_credit_documents", return_value=True), patch.object(
			sog, "missing_credit_customer_requirements", return_value=["an email address"]
		):
			sog.validate_credit_customer_requirements_at_transaction(doc)  # must not raise
		with patch.object(sog, "cash_sale_overrides_credit_documents", return_value=False), patch.object(
			sog, "missing_credit_customer_requirements", return_value=["an email address"]
		):
			with self.assertRaises(frappe.ValidationError):
				sog.validate_credit_customer_requirements_at_transaction(doc)

	def test_only_credit_approvers_switch_it(self):
		class Doc(frappe._dict):
			def get_doc_before_save(self):
				return frappe._dict({sog.CASH_OVERRIDE_FIELD: 0})

		doc = Doc({sog.CASH_OVERRIDE_FIELD: 1})
		frappe.set_user("Guest")
		try:
			with patch.object(sog.frappe, "get_roles", return_value=["Sales User"]):
				with self.assertRaises(frappe.ValidationError):
					sog.validate_cash_override_change(doc)
			with patch.object(sog.frappe, "get_roles", return_value=["Accounts Manager"]):
				sog.validate_cash_override_change(doc)  # must not raise
		finally:
			frappe.set_user("Administrator")

	def test_the_customer_field_exists_after_setup(self):
		sog.ensure_custom_fields()
		self.assertIsNotNone(frappe.get_meta("Customer").get_field(sog.CASH_OVERRIDE_FIELD))


class TestBranchWiseCreditOutstanding(FrappeTestCase):
	def test_ranges(self):
		self.assertEqual(bwco.parse_ranges("30, 60, 90"), [30, 60, 90])
		self.assertEqual(bwco.parse_ranges("junk"), [30, 60, 90, 120])
		self.assertEqual(bwco.bucket_labels([30, 60]), ["0-30", "31-60", "61+"])

	def test_ageing(self):
		as_on = getdate("2026-10-10")
		doc = frappe._dict(outstanding=10, posting_date=getdate("2026-08-01"), due_date=getdate("2026-08-31"))
		bwco.age_doc(doc, as_on, [30, 60, 90, 120], "Due Date")
		self.assertEqual((doc.age, doc.bucket), (40, "range2"))
		future = frappe._dict(outstanding=10, posting_date=as_on, due_date=add_days(as_on, 5))
		bwco.age_doc(future, as_on, [30, 60, 90, 120], "Due Date")
		self.assertEqual(future.bucket, "not_due")
		credit = frappe._dict(outstanding=-5, posting_date=as_on, due_date=as_on)
		bwco.age_doc(credit, as_on, [30], "Due Date")
		self.assertIsNone(credit.bucket)

	def test_build_marks_a_branch_over_its_sub_limit(self):
		docs = [
			frappe._dict(customer="C1", branch="A", outstanding=80.0, bucket="range1", age=5,
				due_date=getdate("2026-10-01"), posting_date=getdate("2026-09-01"), customer_name="One"),
			frappe._dict(customer="C1", branch="B", outstanding=-10.0, bucket=None, age=0,
				due_date=None, posting_date=getdate("2026-09-01"), customer_name="One"),
		]
		limits = {"C1": frappe._dict(credit_limit=500, credit_days=30, bypass=0)}
		with patch.object(bwco, "branch_limits", return_value={("C1", "A"): 50}), patch.object(
			bwco.frappe, "get_all", return_value=[frappe._dict(name="C1", customer_name="One", customer_group="G")]
		):
			customers = bwco.build_customers(docs, {("C1", "A"): 20.0}, limits, frappe._dict())
		c = customers[0]
		self.assertAlmostEqual(c.outstanding, 70.0)
		self.assertAlmostEqual(c.unallocated, -10.0)
		self.assertAlmostEqual(c.exposure, 90.0)
		self.assertAlmostEqual(c.overdue, 80.0)
		self.assertTrue(c.over)  # branch A: 80 + 20 orders > 50
		self.assertEqual(c.status, bwco.STATUS_OVER)
		branch_a = [b for b in c.branches if b.branch == "A"][0]
		self.assertAlmostEqual(branch_a.available, -50.0)

	def test_every_view_runs_on_live_data(self):
		for view in (bwco.VIEW_TREE, bwco.VIEW_BRANCH, bwco.VIEW_DOCUMENT):
			result = bwco.execute({"company": _company(), "as_on_date": today(), "view": view, "credit_customers_only": 1})
			self.assertEqual(len(result), 5)
			self.assertTrue(result[0])


class TestDeliveryPersonPaymentPending(FrappeTestCase):
	def test_describe_follows_the_days_rule(self):
		as_on = getdate("2026-10-10")
		row = frappe._dict(posting_date=getdate("2026-10-05"), outstanding=10, base_grand_total=10, base_rounded_total=0, driver="D")
		dpp.describe(row, frappe._dict(full_name="D", custom_payment_days=5), as_on, {}, "BHD")
		self.assertEqual(row.status, dpp.WITHIN)  # 5 days old, 5 allowed
		row2 = frappe._dict(posting_date=getdate("2026-10-04"), outstanding=10, base_grand_total=10, base_rounded_total=0, driver="D")
		dpp.describe(row2, frappe._dict(full_name="D", custom_payment_days=5), as_on, {}, "BHD")
		self.assertEqual((row2.status, row2.days_overdue), (dpp.OVERDUE, 1))
		paid = frappe._dict(posting_date=getdate("2026-10-01"), outstanding=0, base_grand_total=10, base_rounded_total=0, driver="D")
		dpp.describe(paid, None, as_on, {}, "BHD")
		self.assertEqual(paid.status, dpp.PAID)

	def test_junk_rounded_total_is_ignored(self):
		self.assertEqual(dpp.document_total(frappe._dict(base_grand_total=12.345, base_rounded_total=999)), 12.345)

	def test_summary_flags_the_blocking_rules(self):
		rows = [
			frappe._dict(driver="D", branch="A", status=dpp.OVERDUE, outstanding=60.0, total=60.0, days_overdue=3, posting_date=getdate("2026-10-01")),
			frappe._dict(driver="D", branch="A", status=dpp.WITHIN, outstanding=50.0, total=50.0, days_overdue=0, posting_date=getdate("2026-10-09")),
		]
		people = dpp.summarise(rows, {"D": frappe._dict(full_name="D", custom_cash_limit=100, custom_payment_days=2)}, "BHD")
		self.assertEqual(people[0]["blocked"], "Days + Cash Limit")
		self.assertAlmostEqual(people[0]["pending"], 110.0)

	def test_every_view_runs_on_live_data(self):
		for view in (dpp.VIEW_INVOICE, dpp.VIEW_PERSON):
			for status in ("Pending", "Overdue", "All"):
				result = dpp.execute({"company": _company(), "view": view, "status": status})
				self.assertEqual(len(result), 5)
