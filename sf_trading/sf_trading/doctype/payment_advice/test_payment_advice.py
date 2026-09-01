# apps/sf_trading/sf_trading/sf_trading/doctype/payment_advice/test_payment_advice.py
"""Tests for Payment Advice — allocation, totals, guards and the Payment Entry builder.

These cover the defects carried over from the original payment_advice app, so a regression
would fail here rather than in production: allocation stopping at the authorised amount,
flt() safety on nulls, company coming from the document, and the exchange-rate call shape.
"""

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import flt, nowdate

from sf_trading.sf_trading.doctype.payment_advice.payment_advice import (
    ALLOWED_REFERENCE_DOCTYPES,
    STATUS_APPROVED,
    STATUS_DRAFT,
    STATUS_PENDING,
    get_company_account,
    get_payment_type,
    shape_reference_rows,
)


class TestPaymentAdvice(FrappeTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = frappe.db.get_single_value("Global Defaults", "default_company") or frappe.db.get_value(
            "Company", {}, "name"
        )

    # ── pure helpers ─────────────────────────────────────────────────────────────
    def test_payment_type_by_party(self):
        self.assertEqual(get_payment_type("Supplier"), "Pay")
        self.assertEqual(get_payment_type("Customer"), "Receive")

    def test_payment_type_rejects_employee(self):
        # party_type offers only Supplier and Customer; Employee was never wired up
        with self.assertRaises(frappe.ValidationError):
            get_payment_type("Employee")

    def test_payment_type_rejects_unknown_party(self):
        with self.assertRaises(frappe.ValidationError):
            get_payment_type("Shareholder")

    def test_company_account_falls_back(self):
        # never raises; either an account or None, but always company-scoped
        account = get_company_account(self.company, None)
        if account:
            self.assertEqual(frappe.db.get_value("Account", account, "company"), self.company)

    def test_approver_links_to_a_user_not_an_employee(self):
        # an Employee with no User ID could be picked and then nobody could submit the advice
        for doctype in ("Payment Advice", "Payment Automation Settings"):
            self.assertEqual(frappe.get_meta(doctype).get_field("approver").options, "User")

    def test_party_name_is_filled_from_the_party(self):
        for party_type, title_field in (("Supplier", "supplier_name"), ("Customer", "customer_name")):
            party = frappe.db.get_value(party_type, {"disabled": 0}, ["name", title_field], as_dict=True)
            if not party:
                continue
            advice = frappe.new_doc("Payment Advice")
            advice.party_type, advice.party = party_type, party.name
            advice.set_party_name()
            self.assertEqual(advice.party_name, party.get(title_field) or party.name)

    def test_party_name_left_alone_without_a_party(self):
        advice = frappe.new_doc("Payment Advice")
        advice.party_type = "Supplier"
        advice.set_party_name()
        self.assertFalse(advice.party_name)

    # ── allocation ───────────────────────────────────────────────────────────────
    def _advice(self, payables, payment_amount):
        """Build an unsaved advice with synthetic reference rows."""
        advice = frappe.new_doc("Payment Advice")
        advice.update(
            {
                "company": self.company,
                "party_type": "Supplier",
                "transaction_date": nowdate(),
                "payment_amount": payment_amount,
            }
        )
        for payable in payables:
            advice.append("payment_advice_reference", {"net_payable_amount": payable})
        return advice

    def test_allocation_stops_at_authorised_amount(self):
        advice = self._advice([100, 200, 300], 250)
        advice.allocate_payment()
        allocations = [flt(r.allocated_amount) for r in advice.payment_advice_reference]
        self.assertEqual(allocations, [100.0, 150.0, 0.0])
        self.assertEqual(sum(allocations), 250.0)

    def test_allocation_never_exceeds_a_row(self):
        advice = self._advice([50, 50], 500)
        advice.allocate_payment()
        for row in advice.payment_advice_reference:
            self.assertLessEqual(flt(row.allocated_amount), flt(row.net_payable_amount))

    def test_allocation_handles_null_amounts(self):
        """The original app raised TypeError here — nulls must simply allocate zero."""
        advice = self._advice([None, 100], 60)
        advice.allocate_payment()
        allocations = [flt(r.allocated_amount) for r in advice.payment_advice_reference]
        self.assertEqual(allocations, [0.0, 60.0])

    def test_allocation_zero_payment(self):
        advice = self._advice([100], 0)
        advice.allocate_payment()
        self.assertEqual(flt(advice.payment_advice_reference[0].allocated_amount), 0.0)

    # ── totals + guards ──────────────────────────────────────────────────────────
    def test_totals_from_rows(self):
        advice = self._advice([], 10)
        for amount, settled in ((100, 40), (50, 0)):
            advice.append(
                "payment_advice_reference",
                {"amount": amount, "settled_amount": settled, "net_payable_amount": amount - settled},
            )
        advice.compute_totals()
        self.assertEqual(flt(advice.amount), 150.0)
        self.assertEqual(flt(advice.amount_paid), 40.0)
        self.assertEqual(flt(advice.amount_to_be_settled), 110.0)
        self.assertEqual(flt(advice.pending_amount), 100.0)

    def test_payment_amount_must_be_positive(self):
        advice = self._advice([100], 0)
        advice.compute_totals()
        with self.assertRaises(frappe.ValidationError):
            advice.validate_payment_amount()

    def test_payment_amount_cannot_exceed_payable(self):
        advice = self._advice([100], 250)
        advice.compute_totals()
        with self.assertRaises(frappe.ValidationError):
            advice.validate_payment_amount()

    def test_status_lifecycle(self):
        advice = self._advice([100], 100)
        advice.docstatus = 0
        advice.set_status()
        self.assertEqual(advice.status, STATUS_DRAFT)

        advice.approver = "Administrator"
        advice.set_status()
        self.assertEqual(advice.status, STATUS_PENDING)

        advice.docstatus = 1
        advice.payment_entry = None
        advice.set_status()
        self.assertEqual(advice.status, STATUS_APPROVED)

    # ── schema expectations the automation layer depends on ──────────────────────
    def test_fields_the_automation_needs_exist(self):
        meta = frappe.get_meta("Payment Advice")
        for fieldname in ("status", "auto_generated", "payment_entry", "bank_account", "company"):
            self.assertTrue(meta.has_field(fieldname), fieldname)

    def test_payment_entry_link_is_a_link_not_data(self):
        """The original stored the PE as free text, so nothing could be joined to it."""
        df = frappe.get_meta("Payment Advice").get_field("payment_entry")
        self.assertEqual(df.fieldtype, "Link")
        self.assertEqual(df.options, "Payment Entry")
        self.assertTrue(df.allow_on_submit)

    def test_reference_child_has_allocated_amount(self):
        meta = frappe.get_meta("Payment Advice Reference")
        self.assertTrue(meta.has_field("allocated_amount"))
        self.assertTrue(meta.has_field("ageing"))  # was misspelled "aeging" upstream


class TestOutstandingMapping(FrappeTestCase):
    """shape_reference_rows() maps ERPNext's outstanding rows onto advice references.

    Key shapes verified against live data: the engine returns voucher_type, voucher_no,
    bill_no, currency, due_date, exchange_rate, invoice_amount, outstanding_amount,
    posting_date — but NOT payment_term (unless term-allocated) and NOT total_amount.
    """

    def _voucher(self, **kw):
        row = {
            "voucher_type": "Purchase Invoice",
            "voucher_no": "PINV-TEST-001",
            "invoice_amount": 1000.0,
            "outstanding_amount": 400.0,
            "posting_date": "2026-01-01",
            "due_date": "2026-01-31",
            "currency": "BHD",
            "exchange_rate": 1.0,
            "bill_no": "SUP-77",
        }
        row.update(kw)
        return row

    def test_maps_core_fields(self):
        rows = shape_reference_rows([self._voucher()])
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row["reference_doctype"], "Purchase Invoice")
        self.assertEqual(row["reference_record"], "PINV-TEST-001")
        self.assertEqual(row["net_payable_amount"], 400.0)
        self.assertEqual(row["amount"], 1000.0)
        self.assertEqual(row["settled_amount"], 600.0)
        self.assertEqual(row["bill_no"], "SUP-77")
        self.assertEqual(row["currency"], "BHD")

    def test_missing_payment_term_and_total_amount_are_safe(self):
        """The engine omits both for ordinary invoices — mapping must not raise."""
        voucher = self._voucher()
        voucher.pop("bill_no")
        rows = shape_reference_rows([voucher])
        self.assertIsNone(rows[0]["payment_term"])
        self.assertEqual(rows[0]["amount"], 1000.0)

    def test_total_amount_used_when_invoice_amount_absent(self):
        voucher = self._voucher(invoice_amount=None, total_amount=750.0)
        rows = shape_reference_rows([voucher])
        self.assertEqual(rows[0]["amount"], 750.0)

    def test_zero_outstanding_dropped(self):
        rows = shape_reference_rows([self._voucher(outstanding_amount=0)])
        self.assertEqual(rows, [])

    def test_disallowed_doctype_dropped(self):
        rows = shape_reference_rows([self._voucher(voucher_type="Delivery Note")])
        self.assertEqual(rows, [])
        self.assertNotIn("Delivery Note", ALLOWED_REFERENCE_DOCTYPES)

    def test_amount_window(self):
        vouchers = [
            self._voucher(voucher_no="A", outstanding_amount=50),
            self._voucher(voucher_no="B", outstanding_amount=300),
            self._voucher(voucher_no="C", outstanding_amount=900),
        ]
        rows = shape_reference_rows(vouchers, from_amount=100, to_amount=500)
        self.assertEqual([r["reference_record"] for r in rows], ["B"])

    def test_sorted_worst_first(self):
        vouchers = [
            self._voucher(voucher_no="NEW", due_date="2026-07-01", outstanding_amount=100),
            self._voucher(voucher_no="OLD", due_date="2024-01-01", outstanding_amount=100),
        ]
        rows = shape_reference_rows(vouchers)
        self.assertEqual(rows[0]["reference_record"], "OLD")
        self.assertGreater(rows[0]["ageing"], rows[1]["ageing"])

    def test_ageing_never_negative_for_future_due_dates(self):
        rows = shape_reference_rows([self._voucher(due_date="2099-01-01")])
        self.assertEqual(rows[0]["ageing"], 0)


class TestReferenceIntegrity(FrappeTestCase):
    """Dynamic Link guards: the form filters the pickers, the server enforces the rules."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = frappe.db.get_single_value("Global Defaults", "default_company") or frappe.db.get_value(
            "Company", {}, "name"
        )

    def _advice_with(self, rows):
        advice = frappe.new_doc("Payment Advice")
        advice.update(
            {
                "company": self.company,
                "party_type": "Supplier",
                "party": frappe.db.get_value("Supplier", {}, "name"),
                "transaction_date": nowdate(),
                "payment_amount": 1,
            }
        )
        for row in rows:
            advice.append("payment_advice_reference", row)
        return advice

    def test_rejects_unknown_reference_doctype(self):
        advice = self._advice_with(
            [{"reference_doctype": "Delivery Note", "reference_record": "DN-0001"}]
        )
        with self.assertRaises(frappe.ValidationError):
            advice.validate_references()

    def test_rejects_blank_reference(self):
        advice = self._advice_with([{"reference_doctype": "Purchase Invoice"}])
        with self.assertRaises(frappe.ValidationError):
            advice.validate_references()

    def test_rejects_duplicate_reference_rows(self):
        pinv = frappe.db.get_value(
            "Purchase Invoice", {"docstatus": 1, "outstanding_amount": [">", 0]}, "name"
        )
        if not pinv:
            self.skipTest("no outstanding Purchase Invoice on this site")
        supplier, company = frappe.db.get_value("Purchase Invoice", pinv, ["supplier", "company"])
        advice = self._advice_with(
            [
                {"reference_doctype": "Purchase Invoice", "reference_record": pinv},
                {"reference_doctype": "Purchase Invoice", "reference_record": pinv},
            ]
        )
        advice.party = supplier
        advice.company = company
        with self.assertRaises(frappe.ValidationError):
            advice.validate_references()

    def test_rejects_reference_of_another_party(self):
        rows = frappe.get_all(
            "Purchase Invoice",
            filters={"docstatus": 1, "outstanding_amount": [">", 0]},
            fields=["name", "supplier", "company"],
            limit=20,
        )
        pair = None
        for row in rows:
            other = next((r for r in rows if r.supplier != row.supplier), None)
            if other:
                pair = (row, other)
                break
        if not pair:
            self.skipTest("need invoices from two different suppliers")

        mine, theirs = pair
        advice = self._advice_with(
            [{"reference_doctype": "Purchase Invoice", "reference_record": theirs.name}]
        )
        advice.party = mine.supplier
        advice.company = mine.company
        with self.assertRaises(frappe.ValidationError):
            advice.validate_references()


class TestCreateAdvicesFromDocuments(FrappeTestCase):
    """The endpoint behind the "Payment Advice" entry in the Create menu of Purchase Order and
    Purchase Invoice (public/js/payment_advice_form_action.js) and behind the list-view action.

    Note: create_advices_from_documents() commits per advice, so anything it creates outlives the
    test transaction. Every test that lets it succeed removes what it made — and it has to cope with
    the advice having been submitted in the meantime, because the site's own
    payment_automation.run_due_automations tick stamps an approver and approves fresh drafts. A
    submitted document cannot be deleted even with force=True, so cancel comes first.
    """

    def _call(self, documents, doctype):
        from sf_trading.api.payment_advice_builder import create_advices_from_documents

        return create_advices_from_documents(documents, {"doctype": doctype})

    def _cleanup(self, result):
        for row in (result or {}).get("created", []):
            name = row["advice"]
            if not frappe.db.exists("Payment Advice", name):
                continue
            if frappe.db.get_value("Payment Advice", name, "docstatus") == 1:
                doc = frappe.get_doc("Payment Advice", name)
                doc.flags.ignore_permissions = True
                doc.cancel()
            frappe.delete_doc("Payment Advice", name, force=True, ignore_permissions=True)
        frappe.db.commit()

    # ── contract guards ──────────────────────────────────────────────────────────
    def test_empty_selection_is_rejected(self):
        with self.assertRaises(frappe.ValidationError):
            self._call([], "Purchase Invoice")

    def test_doctype_without_a_party_field_is_rejected(self):
        # UOM carries neither supplier nor customer, so it can never be paid. The guard has to fire
        # before the query is built, otherwise the missing columns surface as a raw 1054.
        with self.assertRaises(frappe.ValidationError):
            self._call(["Nos"], "UOM")

    def test_unsubmitted_or_missing_documents_are_rejected(self):
        with self.assertRaises(frappe.ValidationError):
            self._call(["PI-does-not-exist-0001"], "Purchase Invoice")

    # ── the two Create-menu sources ──────────────────────────────────────────────
    def test_purchase_order_raises_a_supplier_advice(self):
        from sf_trading.sf_trading.doctype.payment_advice.payment_advice import get_reference_amounts

        po = frappe.db.get_value(
            "Purchase Order",
            {"docstatus": 1, "status": ["not in", ["On Hold", "Closed"]], "per_billed": ["<", 100]},
            ["name", "supplier", "company"],
            as_dict=True,
        )
        if not po:
            self.skipTest("need a submitted Purchase Order with per_billed < 100")

        _total, payable = get_reference_amounts("Purchase Order", po.name)
        if payable <= 0:
            self.skipTest("the candidate Purchase Order has nothing left to pay")

        result = self._call([po.name], "Purchase Order")
        self.addCleanup(self._cleanup, result)

        self.assertEqual(len(result["created"]), 1)
        self.assertFalse(result["failed"])
        advice = frappe.get_doc("Payment Advice", result["created"][0]["advice"])
        self.assertEqual(advice.docstatus, 0)
        self.assertEqual(advice.party_type, "Supplier")
        self.assertEqual(advice.party, po.supplier)
        self.assertEqual(advice.company, po.company)
        rows = advice.payment_advice_reference
        self.assertEqual([r.reference_doctype for r in rows], ["Purchase Order"])
        self.assertEqual(rows[0].reference_record, po.name)
        self.assertEqual(flt(rows[0].net_payable_amount, 3), flt(payable, 3))

        # a document already on a live advice cannot be advised twice
        with self.assertRaises(frappe.ValidationError):
            self._call([po.name], "Purchase Order")

    def test_purchase_invoice_raises_a_supplier_advice(self):
        pi = frappe.db.get_value(
            "Purchase Invoice",
            {"docstatus": 1, "outstanding_amount": [">", 0], "is_return": 0, "on_hold": 0},
            ["name", "supplier", "company"],
            as_dict=True,
        )
        if not pi:
            self.skipTest("need a submitted Purchase Invoice with an outstanding amount")

        result = self._call([pi.name], "Purchase Invoice")
        self.addCleanup(self._cleanup, result)

        self.assertEqual(len(result["created"]), 1)
        advice = frappe.get_doc("Payment Advice", result["created"][0]["advice"])
        self.assertEqual(advice.party, pi.supplier)
        self.assertEqual(
            [r.reference_doctype for r in advice.payment_advice_reference], ["Purchase Invoice"]
        )
        self.assertGreater(flt(advice.payment_amount), 0)

    # ── the amount rules live in the workflow conditions, not in Python ─────────────

    def _sends(self):
        from sf_trading.api import payment_advice_workflow as wf

        return [
            t for t in wf._transitions("Steel Force Trading WLL")
            if t["action"] == "Send for Approval" and t["state"] == "Draft"
        ]

    def test_the_accountant_can_raise_an_advice(self):
        """The role appears on both sides of the chain."""
        from sf_trading.api import payment_advice_workflow as wf

        self.assertIn(wf.ROLE_APPROVER, wf.PREPARER_ROLES)
        raisers = {t["allowed"] for t in self._sends()}
        self.assertIn(wf.ROLE_APPROVER, raisers)

    def test_the_accountants_direct_route_is_capped_at_the_limit(self):
        """Their own straight-through row only applies at or below the limit."""
        from sf_trading.api import payment_advice_workflow as wf

        rows = [
            t for t in self._sends()
            if t["allowed"] == wf.ROLE_APPROVER and t["next_state"] == "Pending Accountant"
        ]
        self.assertEqual(len(rows), 1)
        self.assertIn("<= 500", rows[0]["condition"])
        self.assertIn('doc.approval_route == "Accountant"', rows[0]["condition"])

    def test_the_accountants_large_direct_advice_goes_to_finance(self):
        """Over the limit somebody else approves before it reaches their own desk."""
        from sf_trading.api import payment_advice_workflow as wf

        rows = [
            t for t in self._sends()
            if t["allowed"] == wf.ROLE_APPROVER
            and t["next_state"] == "Pending Finance"
            and "Accountant" in (t["condition"] or "")
        ]
        self.assertEqual(len(rows), 1)
        self.assertIn("> 500", rows[0]["condition"])

    def test_the_other_preparers_are_not_capped(self):
        """The limit is about who raised it; Branch Head and Purchase Assistant are unaffected."""
        from sf_trading.api import payment_advice_workflow as wf

        for role in (wf.ROLE_BRANCH_HEAD, wf.ROLE_PURCHASE_ASSISTANT):
            rows = [
                t for t in self._sends()
                if t["allowed"] == role and t["next_state"] == "Pending Accountant"
            ]
            self.assertEqual(len(rows), 1, role)
            self.assertNotIn("payment_amount", rows[0]["condition"], role)

    def test_the_limit_comes_from_one_place(self):
        """The conditions are generated from the controller constant, not retyped."""
        from sf_trading.api import payment_advice_workflow as wf
        from sf_trading.sf_trading.doctype.payment_advice.payment_advice import (
            FINANCE_APPROVAL_LIMIT,
        )

        self.assertEqual(wf.FINANCE_APPROVAL_LIMIT, FINANCE_APPROVAL_LIMIT)

    # ── the amount decides for orders too, not just invoices ───────────────────

    def _route(self, rows, amount):
        import sf_trading.sf_trading.doctype.payment_advice.payment_advice as pa

        advice = frappe._dict({
            "payment_amount": amount,
            "payment_advice_reference": [frappe._dict(r) for r in rows],
        })
        return pa.compute_approval_route(advice)

    def test_single_order_within_the_limit_goes_direct(self):
        rows = [{"reference_doctype": "Purchase Order", "reference_record": "PO-1"}]
        self.assertEqual(self._route(rows, 500), "Accountant")

    def test_single_order_over_the_limit_goes_to_the_purchase_manager(self):
        """A single large order takes the same route as several: Purchase Manager first.

        It used to bypass the limit entirely, which is how BHD 6,364 was released on one
        signature.
        """
        rows = [{"reference_doctype": "Purchase Order", "reference_record": "PO-1"}]
        self.assertEqual(self._route(rows, 6364.458), "Purchase Manager")

    def test_a_large_invoice_still_goes_to_finance_not_purchase(self):
        """Orders and invoices part company above the limit: the order was already approved."""
        rows = [{"reference_doctype": "Purchase Invoice", "reference_record": "PI-1"}]
        self.assertEqual(self._route(rows, 6364.458), "Finance")

    def test_single_invoice_is_unchanged(self):
        rows = [{"reference_doctype": "Purchase Invoice", "reference_record": "PI-1"}]
        self.assertEqual(self._route(rows, 500), "Accountant")
        self.assertEqual(self._route(rows, 500.001), "Finance")

    def test_several_orders_still_go_to_the_purchase_manager(self):
        """The order count is tested before the amount, so a small multi-order advice still
        takes the longer route."""
        rows = [
            {"reference_doctype": "Purchase Order", "reference_record": "PO-1"},
            {"reference_doctype": "Purchase Order", "reference_record": "PO-2"},
        ]
        self.assertEqual(self._route(rows, 10), "Purchase Manager")


class TestPaymentAdviceStaleReferences(FrappeTestCase):
	"""A reference settled after the advice was raised must be caught, and named."""

	def test_refresh_names_the_row_that_moved(self):
		from sf_trading.sf_trading.doctype.payment_advice.payment_advice import PaymentAdvice

		advice = frappe.get_doc({"doctype": "Payment Advice"})
		advice.docstatus = 0
		advice._payable_changes = [
			{"idx": 3, "reference": "PINV-0009", "was": 656.691, "now": 653.890}
		]
		messages = []
		original = frappe.msgprint
		frappe.msgprint = lambda msg, **kwargs: messages.append(msg)
		try:
			PaymentAdvice.report_payable_changes(advice)
		finally:
			frappe.msgprint = original

		self.assertTrue(messages, "a moved reference must be announced")
		body = messages[0]
		self.assertIn("Row #3", body)
		self.assertIn("PINV-0009", body)

	def test_over_payable_amount_is_trimmed_and_the_row_is_named(self):
		"""Refusing the save was replaced by trimming — validate_payment_amount's docstring says
		why: the refusal landed during the very save that was refreshing the references, so the
		accountant was told a number was wrong without being shown the right one.

		The reader must still see WHICH reference moved, and that is the notice's job.
		"""
		from sf_trading.sf_trading.doctype.payment_advice.payment_advice import PaymentAdvice

		advice = frappe.get_doc({"doctype": "Payment Advice"})
		advice.docstatus = 0
		advice.payment_amount = 656.691
		advice.amount_to_be_settled = 653.890
		advice._payable_changes = [
			{"idx": 2, "reference": "PINV-0042", "was": 656.691, "now": 653.890}
		]
		advice.append("payment_advice_reference",
			{"reference_doctype": "Purchase Invoice", "reference_record": "PINV-0042",
			 "net_payable_amount": 653.890})

		PaymentAdvice.validate_payment_amount(advice)
		self.assertEqual(flt(advice.payment_amount, 3), 653.890, "too high is trimmed, not refused")
		self.assertEqual(advice._payment_amount_trim, {"was": 656.691, "now": 653.890})

		messages = []
		original = frappe.msgprint
		frappe.msgprint = lambda msg, **kwargs: messages.append(msg)
		try:
			PaymentAdvice.report_payable_changes(advice)
		finally:
			frappe.msgprint = original
		text = frappe.utils.strip_html(messages[0])
		self.assertIn("Row #2", text)
		self.assertIn("PINV-0042", text)
		self.assertIn("653.89", text)

	def test_a_nonsensical_payment_amount_is_still_refused(self):
		from sf_trading.sf_trading.doctype.payment_advice.payment_advice import PaymentAdvice

		advice = frappe.get_doc({"doctype": "Payment Advice"})
		advice.amount_to_be_settled = 653.890
		for amount in (0, -5):
			advice.payment_amount = amount
			with self.assertRaises(frappe.ValidationError):
				PaymentAdvice.validate_payment_amount(advice)

	# ── the order that got billed while the advice waited ────────────────────────
	#
	# ERPNext refuses a payment against a billed order and reports it as
	# "{0} {1} has already been fully paid." — which is false whenever the bill is still
	# unpaid, which is the normal case. PA-26-0048 on production said that about
	# PUR-ORD-2026-00227 while Purchase Invoice 20020000196 held 715.000 outstanding.
	# These fix the wording, so the negative assertions matter as much as the positive ones.

	def message(self, idx, block, currency="BHD", order="PUR-ORD-2026-00227"):
		from sf_trading.sf_trading.doctype.payment_advice.payment_advice import order_block_message

		return frappe.utils.strip_html(
			order_block_message(idx, "Purchase Order", order, block, currency)
		)

	def test_billed_order_message_names_the_invoice_and_its_outstanding(self):
		body = self.message(
			1,
			{
				"reason": "billed",
				"invoices": [frappe._dict(name="20020000196", outstanding_amount=715.0)],
			},
		)
		self.assertIn("Row #1", body)
		self.assertIn("PUR-ORD-2026-00227", body)
		self.assertIn("20020000196", body)
		self.assertIn("715", body)
		self.assertIn("fully billed", body)
		# the whole point of the change
		self.assertNotIn("fully paid", body)

	def test_billed_order_with_a_settled_invoice_says_nothing_is_left(self):
		body = self.message(
			2,
			{"reason": "billed", "invoices": [frappe._dict(name="20020000196", outstanding_amount=0)]},
		)
		self.assertIn("nothing left to pay", body)
		self.assertIn("20020000196", body)
		self.assertNotIn("fully paid", body)

	def test_billed_order_with_no_invoice_found_is_still_truthful(self):
		# a bill keyed in against the supplier rather than against the order
		body = self.message(3, {"reason": "billed", "invoices": []})
		self.assertIn("fully billed", body)
		self.assertIn("Purchase Invoice", body)
		self.assertNotIn("fully paid", body)
		self.assertNotIn("outstanding)", body)

	def test_closed_and_fully_advanced_orders_state_their_own_reason(self):
		closed = self.message(1, {"reason": "closed"})
		self.assertIn("is Closed", closed)
		self.assertIn("PUR-ORD-2026-00227", closed)
		self.assertNotIn("fully paid", closed)

		advanced = self.message(1, {"reason": "advanced", "advance": 715.0})
		self.assertIn("already fully advanced", advanced)
		self.assertNotIn("fully paid", advanced)

	def test_pick_time_message_carries_no_row_number(self):
		body = self.message(None, {"reason": "billed", "invoices": []})
		self.assertNotIn("Row #", body)

	def test_reference_row_never_carries_an_empty_payment_term(self):
		"""ERPNext arms its "partly paid" throw on a payment_term that is literally ""."""
		from sf_trading.sf_trading.doctype.payment_advice.payment_advice import pe_reference_row

		blank = pe_reference_row(
			frappe._dict(reference_doctype="Purchase Invoice", reference_record="PINV-1",
			             allocated_amount=10, payment_term="")
		)
		self.assertNotIn("payment_term", blank)

		missing = pe_reference_row(
			frappe._dict(reference_doctype="Purchase Invoice", reference_record="PINV-1",
			             allocated_amount=10, payment_term=None)
		)
		self.assertNotIn("payment_term", missing)

		carried = pe_reference_row(
			frappe._dict(reference_doctype="Purchase Invoice", reference_record="PINV-1",
			             allocated_amount=10, payment_term="30 Days")
		)
		self.assertEqual(carried["payment_term"], "30 Days")
		self.assertEqual(carried["reference_name"], "PINV-1")
		self.assertEqual(carried["allocated_amount"], 10)
		# writing these is theatre: set_missing_ref_details(force=True) overwrites both
		self.assertNotIn("outstanding_amount", carried)
		self.assertNotIn("total_amount", carried)

	def test_draft_notice_lists_the_row_that_can_no_longer_be_paid(self):
		from sf_trading.sf_trading.doctype.payment_advice.payment_advice import PaymentAdvice

		advice = frappe.get_doc({"doctype": "Payment Advice"})
		advice.docstatus = 0
		advice._unpayable_orders = [
			"Row #1: Purchase Order PUR-ORD-2026-00227 is fully billed"
		]
		messages = []
		original = frappe.msgprint
		frappe.msgprint = lambda msg, **kwargs: messages.append(msg)
		try:
			PaymentAdvice.report_payable_changes(advice)
		finally:
			frappe.msgprint = original

		self.assertTrue(messages, "a row that went unpayable must be announced")
		body = frappe.utils.strip_html(messages[0])
		self.assertIn("can no longer be paid", body)
		self.assertIn("Row #1", body)


class TestOrderReferenceGuard(FrappeTestCase):
    """order_payment_block against real documents on this site, and the guard it feeds.

    Read-only throughout: nothing is created, and build_payment_entry() is called without
    insert(), which is exactly the point — the guard has to fire before ERPNext ever sees the
    Payment Entry.
    """

    def order(self, **filters):
        return frappe.db.get_value(
            "Purchase Order", dict({"docstatus": 1}, **filters),
            ["name", "supplier", "company", "per_billed"], as_dict=True
        )

    def test_a_fully_billed_order_is_blocked_and_names_its_invoice(self):
        from sf_trading.sf_trading.doctype.payment_advice.payment_advice import (
            get_invoices_against_order,
            order_payment_block,
        )

        po = self.order(per_billed=[">=", 100], status=["!=", "Closed"])
        if not po:
            self.skipTest("no fully billed Purchase Order on this site")

        block = order_payment_block("Purchase Order", po.name)
        self.assertIsNotNone(block, "a fully billed order cannot be paid as an order")
        self.assertEqual(block["reason"], "billed")
        self.assertEqual(block["invoices"], get_invoices_against_order("Purchase Order", po.name))

    def test_a_partly_billed_order_is_not_blocked(self):
        """The regression net: ERPNext accepts these, so the guard must stay out of the way."""
        from sf_trading.sf_trading.doctype.payment_advice.payment_advice import order_payment_block

        po = self.order(per_billed=[">", 0.02], status=["!=", "Closed"])
        if po and abs(100 - flt(po.per_billed)) <= 0.01:
            po = None
        po = po or self.order(per_billed=[">", 0.02])
        if not po or abs(100 - flt(po.per_billed)) <= 0.01:
            self.skipTest("no partly billed Purchase Order on this site")

        block = order_payment_block("Purchase Order", po.name)
        if block:
            # only legitimately blocked when it is Closed or already fully advanced
            self.assertIn(block["reason"], ("closed", "advanced"))

    def test_an_unbilled_order_with_something_left_is_not_blocked(self):
        from sf_trading.sf_trading.doctype.payment_advice.payment_advice import order_payment_block

        po = self.order(per_billed=0, status=["not in", ["Closed", "On Hold"]])
        if not po:
            self.skipTest("no unbilled Purchase Order on this site")
        block = order_payment_block("Purchase Order", po.name)
        if block:
            self.assertEqual(block["reason"], "advanced")

    def test_get_reference_details_refuses_a_billed_order_with_the_reason(self):
        from sf_trading.sf_trading.doctype.payment_advice.payment_advice import get_reference_details

        po = self.order(per_billed=[">=", 100], status=["!=", "Closed"])
        if not po:
            self.skipTest("no fully billed Purchase Order on this site")

        with self.assertRaises(frappe.ValidationError) as caught:
            get_reference_details("Purchase Order", po.name, po.company, "Supplier", po.supplier)
        text = frappe.utils.strip_html(str(caught.exception))
        self.assertIn(po.name, text)
        self.assertNotIn("fully paid", text)

    def test_build_payment_entry_refuses_before_erpnext_does(self):
        """The PA-26-0048 reproduction: an advice on an order that has since been billed."""
        from sf_trading.sf_trading.doctype.payment_advice.payment_advice import build_payment_entry

        row = frappe.db.sql(
            """select r.parent from `tabPayment Advice Reference` r
               join `tabPurchase Order` po on po.name = r.reference_record
               join `tabPayment Advice` pa on pa.name = r.parent
               where r.reference_doctype = 'Purchase Order' and pa.docstatus = 1
                 and coalesce(pa.payment_entry, '') = '' and abs(100 - po.per_billed) <= 0.01
               limit 1""",
            as_dict=True,
        )
        if not row:
            self.skipTest("no submitted advice pointing at a fully billed order on this site")

        advice = frappe.get_doc("Payment Advice", row[0].parent)
        before = frappe.db.count("Payment Entry")
        with self.assertRaises(frappe.ValidationError) as caught:
            build_payment_entry(advice)
        text = frappe.utils.strip_html(str(caught.exception))
        self.assertIn("fully billed", text)
        self.assertNotIn("fully paid", text)
        # the guard has to beat pe.insert(), or a Payment Entry exists by the time it throws
        self.assertEqual(frappe.db.count("Payment Entry"), before)

    def test_an_invoice_reference_still_builds_its_row(self):
        from sf_trading.sf_trading.doctype.payment_advice.payment_advice import build_payment_entry

        row = frappe.db.sql(
            """select r.parent, r.reference_record from `tabPayment Advice Reference` r
               join `tabPurchase Invoice` pi on pi.name = r.reference_record
               join `tabPayment Advice` pa on pa.name = r.parent
               where r.reference_doctype = 'Purchase Invoice' and pa.docstatus = 1
                 and r.allocated_amount > 0 and pi.outstanding_amount > 0
               limit 1""",
            as_dict=True,
        )
        if not row:
            self.skipTest("no submitted advice against an outstanding Purchase Invoice")

        advice = frappe.get_doc("Payment Advice", row[0].parent)
        pe = build_payment_entry(advice)
        self.assertTrue(pe.references)
        self.assertIn(row[0].reference_record, [r.reference_name for r in pe.references])
        self.assertTrue(all(r.reference_doctype for r in pe.references))
        self.assertFalse(pe.get("name") and frappe.db.exists("Payment Entry", pe.name))
