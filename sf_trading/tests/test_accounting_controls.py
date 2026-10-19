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
		real = frappe.db.get_value

		def get_value(doctype, *args, **kwargs):
			# answer only for the expense entry; everything else (system settings, meta) is real --
			# a blanket patch here poisons cached settings for every test that follows
			return expense if doctype in lc.EXPENSE_DOCTYPES else real(doctype, *args, **kwargs)

		with patch.object(lc.frappe.db, "get_value", side_effect=get_value), patch.object(
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

	def test_a_draft_or_cancelled_expense_is_refused_with_the_reason_whatever_its_kind(self):
		# a draft has booked nothing, so a landed cost cannot absorb it: not on a voucher, however far along, and
		# not on a bill's own charge row
		for expense_dt in ("Journal Entry", "Purchase Invoice", "Payment Entry"):
			for docstatus, word in ((0, "draft"), (2, "cancelled")):
				for voucher_docstatus in (0, 1):
					doc = self._doc((FREIGHT, 10, expense_dt, "X-1"))
					doc.docstatus = voucher_docstatus
					with self.assertRaises(frappe.ValidationError) as caught:
						self._validate(doc, expense=frappe._dict(docstatus=docstatus, company="SF"))
					self.assertIn(word, str(caught.exception))

	def test_the_reason_for_an_expense_that_booked_nothing(self):
		self.assertIn("still a draft", lc.not_booked_reason("Purchase Invoice", "PI-1", 0))
		self.assertIn("Submit it first", lc.not_booked_reason("Purchase Invoice", "PI-1", 0))
		self.assertIn("cancelled", lc.not_booked_reason("Journal Entry", "JV-1", 2))

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


class TestLandedCostStockDocuments(FrappeTestCase):
	"""Only a receipt, or an invoice with Update Stock, absorbs a landed cost; an invoice that does
	not is read with its receipt (sf_trading.landed_cost.classify)."""

	def _row(self, parenttype, parent, entry="JV-1"):
		return frappe._dict(parenttype=parenttype, parent=parent, account_head=FREIGHT,
			**{lc.DOCTYPE_FIELD: "Journal Entry", lc.ENTRY_FIELD: entry})

	def _classify(self, rows, stocked=(), links=None):
		key = lambda r: (r.get(lc.DOCTYPE_FIELD), r.get(lc.ENTRY_FIELD), r.get("account_head"))
		with patch.object(lc, "stock_invoices", return_value=set(stocked)), patch.object(
			lc, "linked_receipts", side_effect=lambda invoices: {i: set((links or {}).get(i, ())) for i in invoices}
		):
			return lc.classify(rows, key)

	def test_an_invoice_repeating_its_receipts_charge_is_on_receipt(self):
		rows = [self._row("Purchase Receipt", "PR-1"), self._row("Purchase Invoice", "PI-1")]
		self.assertEqual(self._classify(rows, links={"PI-1": ["PR-1"]}), [lc.STOCK, lc.ON_RECEIPT])

	def test_an_invoice_with_no_receipt_yet_is_awaiting(self):
		self.assertEqual(self._classify([self._row("Purchase Invoice", "PI-2")]), [lc.AWAITING_RECEIPT])

	def test_a_charge_the_receipt_never_carried_is_not_into_stock(self):
		rows = [self._row("Purchase Receipt", "PR-2", entry="JV-OTHER"), self._row("Purchase Invoice", "PI-3")]
		self.assertEqual(self._classify(rows, links={"PI-3": ["PR-2"]}), [lc.STOCK, lc.NOT_INTO_STOCK])

	def test_an_invoice_billing_two_receipts_is_on_receipt_only_if_both_carry_it(self):
		# ERPNext decides per invoice row: the receipt without the charge gets it booked by the invoice
		rows = [self._row("Purchase Receipt", "PR-1"), self._row("Purchase Invoice", "PI-5")]
		self.assertEqual(self._classify(rows, links={"PI-5": ["PR-1", "PR-2"]}), [lc.STOCK, lc.NOT_INTO_STOCK])
		rows.append(self._row("Purchase Receipt", "PR-2"))
		self.assertEqual(self._classify(rows, links={"PI-5": ["PR-1", "PR-2"]}), [lc.STOCK, lc.ON_RECEIPT, lc.STOCK])

	def _links(self, tables):
		"""linked_receipts against a fake get_all answering by doctype."""

		def get_all(doctype, filters=None, fields=None, pluck=None, **kwargs):
			rows = [frappe._dict(r) for r in tables.get(doctype, [])]
			filters = filters or {}
			for field, cond in filters.items():
				if isinstance(cond, list) and cond[0] == "in":
					rows = [r for r in rows if r.get(field) in cond[1]]
				elif isinstance(cond, list) and cond[0] == "is":
					rows = [r for r in rows if r.get(field)]
				elif isinstance(cond, list) and cond[0] == "<":
					rows = [r for r in rows if (r.get(field) or 0) < cond[1]]
				elif not isinstance(cond, list):
					rows = [r for r in rows if r.get(field) == cond]
			return [r.get(pluck) for r in rows] if pluck else rows

		return patch.object(lc.frappe, "get_all", side_effect=get_all)

	def test_a_receipt_and_invoice_made_from_one_order_are_tied(self):
		tables = {
			"Purchase Invoice Item": [{"parent": "PI-6", "parenttype": "Purchase Invoice", "po_detail": "POI-1", "docstatus": 1}],
			"Purchase Receipt Item": [{"parent": "PR-6", "purchase_order_item": "POI-1", "docstatus": 1}],
			"Purchase Receipt": [{"name": "PR-6", "is_return": 0}],
			"Purchase Invoice": [{"name": "PI-6", "is_return": 0}],
		}
		with self._links(tables):
			self.assertEqual(lc.linked_receipts(["PI-6"]), {"PI-6": {"PR-6"}})
			self.assertEqual(lc.linked_invoices(["PR-6"]), {"PR-6": {"PI-6"}})

	def test_a_debit_note_reads_the_receipts_of_the_invoice_it_returns(self):
		tables = {
			"Purchase Invoice Item": [],
			"Purchase Receipt Item": [{"parent": "PR-7", "purchase_invoice": "PI-7", "docstatus": 1}],
			"Purchase Receipt": [{"name": "PR-7", "is_return": 0}],
			"Purchase Invoice": [{"name": "PI-7R", "is_return": 1, "return_against": "PI-7"},
				{"name": "PI-7", "is_return": 0}],
		}
		with self._links(tables):
			self.assertEqual(lc.linked_receipts(["PI-7R"]), {"PI-7R": {"PR-7"}})

	def test_a_submitted_not_into_stock_row_takes_only_what_its_ledger_credited(self):
		rows = [frappe._dict(parent="PI-8", parenttype="Purchase Invoice", docstatus=1, base_tax_amount=50)]
		with patch.object(lc.frappe.db, "has_column", return_value=True), \
				patch.object(lc.frappe, "get_all", side_effect=lambda dt, **k: rows if dt == lc.PURCHASE_CHARGES else []), \
				patch.object(lc, "classify", return_value=[lc.NOT_INTO_STOCK]), \
				patch.object(lc, "credited", return_value={("PI-8", FREIGHT): 20.0}):
			taken = lc.absorbed("Journal Entry", "JV-1", FREIGHT)
		self.assertEqual((taken.submitted, taken.draft), (20.0, 0.0))

	def test_the_picker_keeps_a_receipts_charge_for_the_invoice_repeating_it(self):
		ties = frappe._dict(repeats=set(), carried={("Journal Entry", "JV-1", FREIGHT)}, receipts={"PR-1"})
		with patch.object(lc.frappe, "has_permission", return_value=True), \
				patch.object(lc.frappe.db, "get_value", return_value=1), \
				patch.object(lc, "booked", return_value={FREIGHT: 100}), \
				patch.object(lc, "_receipt_links", return_value=ties), \
				patch.object(lc, "absorbed", return_value=frappe._dict(submitted=0.0, draft=0.0, documents=[])) as absorbed:
			lines = lc.get_expense_entry_lines("Journal Entry", "JV-1", "Purchase Invoice", None, 0,
				[{"purchase_receipt": "PR-1"}])
		self.assertEqual(lines[0]["available"], 100)
		self.assertIn(("Purchase Receipt", "PR-1"), absorbed.call_args.kwargs["skip_parents"])

	def _lines(self, docstatus, expense_doctype="Journal Entry", parenttype="Landed Cost Voucher", goods=None, parent=None):
		with patch.object(lc.frappe, "has_permission", return_value=True), \
				patch.object(lc.frappe.db, "get_value", return_value=docstatus), \
				patch.object(lc, "booked", return_value={FREIGHT: 100}), \
				patch.object(lc, "absorbed", return_value=frappe._dict(submitted=0.0, draft=20.0, documents=[("Landed Cost Voucher", "LCV-2")])):
			return lc.get_expense_entry_lines(expense_doctype, "X-1", parenttype, parent, 0, None, None, goods)

	def test_the_goods_bill_is_never_offered_as_its_own_expense(self):
		# the invoice a voucher spreads its cost over cannot also be the expense that booked it
		with self.assertRaises(frappe.ValidationError) as caught:
			self._lines(1, "Purchase Invoice", goods=[["Purchase Invoice", "X-1"]])
		self.assertIn("spread over", str(caught.exception))
		with self.assertRaises(frappe.ValidationError):
			self._lines(1, "Purchase Invoice", parenttype="Purchase Invoice", parent="X-1")
		# a different bill is fine
		self.assertEqual(len(self._lines(1, "Purchase Invoice", goods=[["Purchase Invoice", "OTHER-1"]])), 1)
		# and so is the same name under another doctype
		self.assertEqual(len(self._lines(1, "Journal Entry", goods=[["Purchase Invoice", "X-1"]])), 1)

	def test_the_picker_says_why_when_the_entry_is_a_draft_or_cancelled(self):
		for docstatus, word in ((0, "draft"), (2, "cancelled")):
			for parenttype in ("Landed Cost Voucher", "Purchase Invoice"):
				with self.assertRaises(frappe.ValidationError) as caught:
					self._lines(docstatus, "Purchase Invoice", parenttype=parenttype)
				self.assertIn(word, str(caught.exception))

	def test_the_expense_list_offers_submitted_entries_only(self):
		seen = {}

		def get_list(dt, filters=None, fields=None, **kwargs):
			seen["filters"] = filters
			return [frappe._dict(name="PI-1", posting_date="2026-10-10", supplier_name="Agent", base_grand_total=80.0)]

		with patch.object(lc.frappe, "get_list", side_effect=get_list):
			out = lc.expense_entry_query("Purchase Invoice", "", "name", 0, 20, {"company": "SF", "allow_draft": 1})
		self.assertEqual(seen["filters"]["docstatus"], 1)
		self.assertEqual(out, [["PI-1", "2026-10-10", "Agent", "80.0"]])

	def test_a_submitted_entry_gives_its_booked_and_available_lines(self):
		lines = self._lines(1)
		self.assertEqual([(l["account"], l["booked"], l["available"]) for l in lines], [(FREIGHT, 100, 80)])

	def test_an_invoice_that_updates_stock_absorbs_itself(self):
		self.assertEqual(self._classify([self._row("Purchase Invoice", "PI-4")], stocked=["PI-4"]), [lc.STOCK])

	def _bill(self, doctype, update_stock=0):
		doc = frappe._dict(doctype=doctype, name="DOC-1", company="SF", update_stock=update_stock, items=[], taxes=[
			frappe._dict(idx=1, category="Valuation", add_deduct_tax="Add", account_head=FREIGHT, base_tax_amount=60,
				**{lc.DOCTYPE_FIELD: "Journal Entry", lc.ENTRY_FIELD: "JV-1"})])
		doc.taxes[0].set = doc.taxes[0].__setitem__
		return doc

	def _validate(self, doc, links, taken):
		real = frappe.db.get_value
		expense = frappe._dict(docstatus=1, company="SF")
		with patch.object(lc.frappe.db, "get_value",
				side_effect=lambda dt, *a, **k: expense if dt in lc.EXPENSE_DOCTYPES else real(dt, *a, **k)), \
				patch.object(lc, "booked", side_effect=lambda dt, name, account=None: {FREIGHT: 100}), \
				patch.object(lc, "_receipt_links", return_value=links), \
				patch.object(lc, "absorbed", return_value=taken) as absorbed:
			lc.validate_charges(doc)
		return absorbed

	def test_an_invoice_made_from_its_receipt_is_not_refused(self):
		# the receipt already took 60 of the 100; the invoice's copy of that row takes nothing more
		taken = frappe._dict(submitted=60.0, draft=0.0, documents=[("Purchase Receipt", "PR-1")])
		absorbed = self._validate(self._bill("Purchase Invoice"), frappe._dict(repeats=set(),
			carried={("Journal Entry", "JV-1", FREIGHT)}, receipts={"PR-1"}), taken)
		absorbed.assert_not_called()

	def test_a_receipt_skips_the_invoices_that_repeat_it(self):
		skip = {("Purchase Invoice", "PI-1")}
		absorbed = self._validate(self._bill("Purchase Receipt"), frappe._dict(repeats=skip, carried=set(), receipts=set()),
			frappe._dict(submitted=0.0, draft=0.0, documents=[]))
		self.assertEqual(absorbed.call_args.kwargs.get("skip_parents"), skip)

	def test_a_charge_beyond_the_expense_is_still_refused(self):
		taken = frappe._dict(submitted=60.0, draft=0.0, documents=[("Purchase Receipt", "PR-9")])
		with self.assertRaises(frappe.ValidationError):
			self._validate(self._bill("Purchase Invoice", update_stock=1), frappe._dict(repeats=set(), carried=set(),
				receipts=set()), taken)

	def test_a_non_stock_invoice_is_not_asked_for_the_link(self):
		doc = self._bill("Purchase Invoice")
		doc.taxes[0][lc.ENTRY_FIELD] = None
		with patch.object(lc, "settings", return_value=frappe._dict(require_on_lcv=1, require_on_purchase=1)):
			lc.require_links(doc)
			doc.update_stock = 1
			with self.assertRaises(frappe.ValidationError):
				lc.require_links(doc)

	def test_the_charges_view_names_the_stock_document(self):
		company = _busiest_company()
		result = _run("Landed Cost Reconciliation", {"company": company, "view": "Landed Cost Charges",
			"from_date": str(add_months(nowdate(), -24)), "to_date": nowdate()})
		fields = {c["fieldname"] if isinstance(c, dict) else c for c in result["columns"]}
		self.assertIn("stock_document", fields)
		self.assertIn("linked_documents", fields)
