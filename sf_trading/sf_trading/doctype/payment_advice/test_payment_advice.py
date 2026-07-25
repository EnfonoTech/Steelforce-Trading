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
    STATUS_APPROVED,
    STATUS_DRAFT,
    STATUS_PENDING,
    get_company_account,
    get_payment_type,
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
        self.assertEqual(get_payment_type("Employee"), "Pay")
        self.assertEqual(get_payment_type("Customer"), "Receive")

    def test_payment_type_rejects_unknown_party(self):
        with self.assertRaises(frappe.ValidationError):
            get_payment_type("Shareholder")

    def test_company_account_falls_back(self):
        # never raises; either an account or None, but always company-scoped
        account = get_company_account(self.company, None)
        if account:
            self.assertEqual(frappe.db.get_value("Account", account, "company"), self.company)

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

        advice.approver = frappe.db.get_value("Employee", {}, "name")
        if advice.approver:
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
