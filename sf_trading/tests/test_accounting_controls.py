"""Pending supporting documents and landed costs tied to their expense entry.

    bench --site <site> run-tests --module sf_trading.tests.test_accounting_controls
"""

from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import add_months, flt, nowdate

from sf_trading import landed_cost as lc
from sf_trading import supporting_documents as sd

FREIGHT = "Freight - SF"


def _busiest_company():
	row = frappe.get_all("GL Entry", fields=["company"], filters={"is_cancelled": 0}, order_by="posting_date desc", limit=1)
	return row[0].company if row else frappe.db.get_value("Company", {}, "name")


def _run(report, filters):
	from frappe.desk.query_report import run

	return run(report, filters=filters, ignore_prepared_report=True)


class TestSupportingDocumentRules(FrappeTestCase):
	def setUp(self):
		patch.object(sd, "rules", return_value=[
			frappe._dict(document_type="Journal Entry", enabled=1),
			frappe._dict(document_type="Payment Entry", payment_type="Pay", enabled=1),
			frappe._dict(document_type="Purchase Invoice", minimum_amount=100, enabled=1),
		]).start()
		self.addCleanup(patch.stopall)

	def test_a_journal_needs_one_unless_the_system_raised_it(self):
		self.assertTrue(sd.rule_applies(frappe._dict(doctype="Journal Entry", voucher_type="Journal Entry")))
		self.assertFalse(sd.rule_applies(frappe._dict(doctype="Journal Entry", voucher_type="Depreciation Entry")))
		self.assertFalse(sd.rule_applies(frappe._dict(doctype="Journal Entry", voucher_type="Journal Entry",
			is_system_generated=1)))

	def test_payment_direction_and_amount_narrow_the_rule(self):
		self.assertTrue(sd.rule_applies(frappe._dict(doctype="Payment Entry", payment_type="Pay")))
		self.assertFalse(sd.rule_applies(frappe._dict(doctype="Payment Entry", payment_type="Receive")))
		self.assertTrue(sd.rule_applies(frappe._dict(doctype="Purchase Invoice", base_grand_total=150)))
		self.assertFalse(sd.rule_applies(frappe._dict(doctype="Purchase Invoice", base_grand_total=50)))
		self.assertFalse(sd.rule_applies(frappe._dict(doctype="Sales Invoice", base_grand_total=500)))


class TestPendingSupportingDocuments(FrappeTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		cls.company = _busiest_company()
		cls.filters = {"company": cls.company, "from_date": str(add_months(nowdate(), -12)), "to_date": nowdate()}

	def test_every_view_runs(self):
		for view in ("Detail", "By Document Type", "By Posted By", "By Branch", "By Party", "By Month"):
			with self.subTest(view=view):
				self.assertIsInstance(_run("Pending Supporting Documents", dict(self.filters, view=view))["result"], list)

	def test_attaching_a_file_takes_the_entry_off_the_list(self):
		rows = [r for r in sd.pending_entries(frappe._dict(self.filters, document_type=["Journal Entry"]))
			if r.status == sd.PENDING]
		if not rows:
			self.skipTest("no journal is pending a document")
		entry = rows[0]
		frappe.get_doc({"doctype": "File", "file_name": "voucher.txt", "content": "supporting document",
			"attached_to_doctype": entry.document_type, "attached_to_name": entry.document, "is_private": 1}).insert(
			ignore_permissions=True)
		after = {r.document: r.status for r in sd.pending_entries(frappe._dict(self.filters, document_type=["Journal Entry"]))}
		self.assertEqual(after.get(entry.document), sd.ATTACHED)


class TestLandedCostControls(FrappeTestCase):
	def _doc(self, *rows, doctype="Landed Cost Voucher", receipts=()):
		doc = frappe._dict(doctype=doctype, name="LCV-TEST", company="SF", taxes=[], purchase_receipts=[
			frappe._dict(receipt_document_type=t, receipt_document=n) for t, n in receipts])
		for i, (account, amount, expense_dt, expense) in enumerate(rows, start=1):
			row = frappe._dict(idx=i, expense_account=account, base_amount=amount, amount=amount)
			row[lc.DOCTYPE_FIELD], row[lc.ENTRY_FIELD] = expense_dt, expense
			row.set = row.__setitem__
			doc.taxes.append(row)
		return doc

	def _validate(self, doc, booked=None, taken=None, expense=None):
		expense = expense or frappe._dict(docstatus=1, company="SF")
		booked = booked if booked is not None else {FREIGHT: 100}
		with patch.object(lc.frappe.db, "get_value", return_value=expense), patch.object(
			lc, "booked", side_effect=lambda dt, name, account=None: {a: v for a, v in booked.items() if not account or a == account}
		), patch.object(lc, "absorbed", return_value=taken or frappe._dict(submitted=0.0, draft=0.0, documents=[])):
			lc.validate_charges(doc)

	def test_a_charge_within_the_expense_passes(self):
		self._validate(self._doc((FREIGHT, 60, "Journal Entry", "JV-1")))

	def test_the_ledger_must_match(self):
		with self.assertRaises(frappe.ValidationError):
			self._validate(self._doc(("Customs - SF", 60, "Journal Entry", "JV-1")))

	def test_never_more_than_the_expense_booked(self):
		with self.assertRaises(frappe.ValidationError):
			self._validate(self._doc((FREIGHT, 60, "Journal Entry", "JV-1")),
				taken=frappe._dict(submitted=30.0, draft=20.0, documents=[("Landed Cost Voucher", "LCV-OTHER")]))

	def test_two_rows_on_one_expense_share_it(self):
		with self.assertRaises(frappe.ValidationError):
			self._validate(self._doc((FREIGHT, 60, "Journal Entry", "JV-1"), (FREIGHT, 50, "Journal Entry", "JV-1")))

	def test_the_expense_must_be_submitted_and_of_the_company(self):
		with self.assertRaises(frappe.ValidationError):
			self._validate(self._doc((FREIGHT, 10, "Journal Entry", "JV-1")), expense=frappe._dict(docstatus=0, company="SF"))
		with self.assertRaises(frappe.ValidationError):
			self._validate(self._doc((FREIGHT, 10, "Journal Entry", "JV-1")), expense=frappe._dict(docstatus=1, company="X"))

	def test_the_goods_bill_is_not_its_own_expense(self):
		with self.assertRaises(frappe.ValidationError):
			self._validate(self._doc((FREIGHT, 10, "Purchase Invoice", "PI-GOODS"), receipts=[("Purchase Invoice", "PI-GOODS")]))

	def test_the_link_is_required_on_submit_when_the_settings_say(self):
		doc = self._doc((FREIGHT, 10, None, None))
		with patch.object(lc, "settings", return_value=frappe._dict(require_on_lcv=1, require_on_purchase=0)):
			with self.assertRaises(frappe.ValidationError):
				lc.require_links(doc)
		with patch.object(lc, "settings", return_value=frappe._dict(require_on_lcv=0, require_on_purchase=0)):
			lc.require_links(doc)

	def test_only_valuation_charges_on_a_bill_are_landed_costs(self):
		bill = frappe._dict(doctype="Purchase Invoice", taxes=[
			frappe._dict(idx=1, category="Total", add_deduct_tax="Add", account_head="VAT - SF", base_tax_amount=10),
			frappe._dict(idx=2, category="Valuation", add_deduct_tax="Add", account_head=FREIGHT, base_tax_amount=20),
			frappe._dict(idx=3, category="Valuation and Total", add_deduct_tax="Deduct", account_head=FREIGHT, base_tax_amount=5),
		])
		self.assertEqual([(r.idx, a, v) for r, a, v in lc.charge_rows(bill)], [(2, FREIGHT, 20)])


class TestLandedCostReconciliation(FrappeTestCase):
	def test_every_view_runs_and_adds_up(self):
		company = _busiest_company()
		filters = {"company": company, "from_date": str(add_months(nowdate(), -24)), "to_date": nowdate()}
		for view in ("Expense Entries", "Landed Cost Charges", "Account Summary"):
			with self.subTest(view=view):
				result = _run("Landed Cost Reconciliation", dict(filters, view=view))
				self.assertIsInstance(result["result"], list)
				if view == "Expense Entries":
					for r in result["result"]:
						self.assertAlmostEqual(flt(r["booked"]), flt(r["allocated"]) + flt(r["pending"]) + flt(r["unallocated"]),
							places=2)
