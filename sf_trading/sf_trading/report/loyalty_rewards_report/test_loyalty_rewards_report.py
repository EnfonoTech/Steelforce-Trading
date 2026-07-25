# sf_trading/sf_trading/report/loyalty_rewards_report/test_loyalty_rewards_report.py
"""Tests for the Loyalty Rewards Report.

Read-only against live data: the report never writes. Loyalty journals are few, so the
tests assert filter behaviour, the linked/unlinked split, summary arithmetic and column
shape rather than fabricating journals on a client site.
"""

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import add_months, nowdate

from sf_trading.sf_trading.report.loyalty_rewards_report.loyalty_rewards_report import (
    TEMPLATE,
    UNLINKED_LABEL,
    execute,
    summarise,
)


class TestLoyaltyRewardsReport(FrappeTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = frappe.db.get_single_value("Global Defaults", "default_company") or frappe.db.get_value(
            "Company", {}, "name"
        )

    def _run(self, **filters):
        base = {
            "company": self.company,
            "from_date": add_months(nowdate(), -24),
            "to_date": nowdate(),
            "journal_template": TEMPLATE,
            "status": "All",
        }
        base.update(filters)
        return execute(base)

    def test_detail_columns(self):
        columns, _rows = self._run()
        fieldnames = [c["fieldname"] for c in columns]
        for expected in (
            "journal_entry",
            "reward_amount",
            "sales_invoice",
            "customer",
            "invoice_total",
            "reward_pct",
        ):
            self.assertIn(expected, fieldnames)

    def test_rows_come_from_the_template(self):
        _columns, rows = self._run()
        for row in rows:
            self.assertEqual(
                frappe.db.get_value("Journal Entry", row["journal_entry"], "from_template"), TEMPLATE
            )
            self.assertGreaterEqual(row["reward_amount"], 0)

    def test_reward_amount_matches_journal_debit(self):
        _columns, rows = self._run()
        for row in rows[:20]:
            total_debit = frappe.db.get_value("Journal Entry", row["journal_entry"], "total_debit")
            self.assertAlmostEqual(row["reward_amount"], total_debit, places=2)

    def test_only_unlinked_filter(self):
        _columns, rows = self._run(only_unlinked=1)
        for row in rows:
            self.assertFalse(row["sales_invoice"])
            self.assertIsNone(row["customer"])

    def test_linked_rows_carry_customer(self):
        _columns, rows = self._run()
        for row in rows:
            if row["sales_invoice"]:
                self.assertEqual(
                    row["customer"],
                    frappe.db.get_value("Sales Invoice", row["sales_invoice"], "customer"),
                )

    def test_min_amount_filter(self):
        _columns, rows = self._run(min_amount=1)
        for row in rows:
            self.assertGreaterEqual(row["reward_amount"], 1)

    def test_status_filter_draft_only(self):
        _columns, rows = self._run(status="Draft")
        for row in rows:
            self.assertEqual(row["status"], "Draft")

    def test_summary_totals_match_detail(self):
        _columns, rows = self._run()
        summary = summarise(rows)
        self.assertAlmostEqual(
            sum(s["reward_amount"] for s in summary),
            sum(r["reward_amount"] for r in rows),
            places=2,
        )
        self.assertEqual(sum(s["journals"] for s in summary), len(rows))

    def test_summary_groups_unlinked(self):
        rows = [
            {"customer": None, "customer_name": None, "sales_invoice": None, "reward_amount": 5.0, "invoice_total": 0.0},
            {"customer": None, "customer_name": None, "sales_invoice": None, "reward_amount": 2.5, "invoice_total": 0.0},
        ]
        summary = summarise(rows)
        self.assertEqual(len(summary), 1)
        self.assertEqual(summary[0]["customer_name"], UNLINKED_LABEL)
        self.assertEqual(summary[0]["journals"], 2)
        self.assertAlmostEqual(summary[0]["reward_amount"], 7.5, places=2)
        self.assertEqual(summary[0]["invoices"], 0)

    def test_summary_counts_each_invoice_once(self):
        """Two journals against the same invoice must not double the invoice value."""
        rows = [
            {"customer": "CUST-1", "customer_name": "One", "sales_invoice": "SI-1",
             "reward_amount": 3.0, "invoice_total": 100.0},
            {"customer": "CUST-1", "customer_name": "One", "sales_invoice": "SI-1",
             "reward_amount": 2.0, "invoice_total": 100.0},
        ]
        summary = summarise(rows)
        self.assertEqual(summary[0]["invoices"], 1)
        self.assertAlmostEqual(summary[0]["invoice_total"], 100.0, places=2)
        self.assertAlmostEqual(summary[0]["reward_amount"], 5.0, places=2)
        self.assertAlmostEqual(summary[0]["reward_pct"], 5.0, places=2)

    def test_summary_columns_shape(self):
        columns, _rows = self._run(summarise_by_customer=1)
        fieldnames = [c["fieldname"] for c in columns]
        self.assertEqual(
            fieldnames,
            ["customer", "customer_name", "journals", "invoices", "reward_amount", "invoice_total", "reward_pct"],
        )
