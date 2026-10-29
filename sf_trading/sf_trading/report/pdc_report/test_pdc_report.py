# sf_trading/sf_trading/report/pdc_report/test_pdc_report.py
"""Tests for the PDC Report.

Read-only against live data: the report never writes. The point of these tests is the
reminder anchor — the report's Reminder Date column and the "PDC Cheque Date Reminder"
notification must count back from the same field (`posting_date`) by the same number of
days, or an accountant reads one date in the report and gets the alert on another.
"""

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import add_days, date_diff, getdate, nowdate

from sf_trading.sf_trading.report.pdc_report.pdc_report import (
    REMINDER_LEAD_DAYS,
    execute,
    get_cheque_modes,
)

NOTIFICATION = "PDC Cheque Date Reminder"


class TestPDCReport(FrappeTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = frappe.db.get_single_value("Global Defaults", "default_company") or frappe.db.get_value(
            "Company", {}, "name"
        )

    def _run(self, **filters):
        base = {"company": self.company, "include_cancelled": 1}
        base.update(filters)
        return execute(base)

    def test_columns_expose_the_posting_date_and_its_reminder(self):
        columns, _rows = self._run()
        fieldnames = [c["fieldname"] for c in columns]
        for expected in ("posting_date", "reminder_date", "days_to_posting_date", "cheque_date"):
            self.assertIn(expected, fieldnames)
        # posting date sits with the reminder it drives, not at the far right of the report
        self.assertLess(fieldnames.index("posting_date"), fieldnames.index("cheque_date"))

    def test_reminder_date_counts_back_from_the_posting_date(self):
        _columns, rows = self._run()
        for row in rows:
            if not row["posting_date"]:
                self.assertIsNone(row["reminder_date"])
                continue
            self.assertEqual(
                getdate(row["reminder_date"]),
                add_days(getdate(row["posting_date"]), -REMINDER_LEAD_DAYS),
            )

    def test_days_to_posting_date_is_measured_from_today(self):
        _columns, rows = self._run()
        today = getdate(nowdate())
        for row in rows:
            if row["posting_date"]:
                self.assertEqual(row["days_to_posting_date"], date_diff(getdate(row["posting_date"]), today))

    def test_only_cheque_modes_are_reported(self):
        modes = get_cheque_modes()
        _columns, rows = self._run()
        for row in rows:
            self.assertIn(row["mode_of_payment"], modes)

    def test_notification_anchors_on_the_same_field_and_lead(self):
        """The report column exists to predict this notification — keep them in step."""
        if not frappe.db.exists("Notification", NOTIFICATION):
            self.skipTest(f"{NOTIFICATION} is not installed on this site")

        notification = frappe.db.get_value(
            "Notification", NOTIFICATION, ["event", "date_changed", "days_in_advance"], as_dict=True
        )
        self.assertEqual(notification.event, "Days Before")
        self.assertEqual(notification.date_changed, "posting_date")
        self.assertEqual(notification.days_in_advance, REMINDER_LEAD_DAYS)

    def test_status_filter_splits_cleared_from_pending(self):
        _columns, pending = self._run(status="Pending", include_cancelled=0)
        for row in pending:
            self.assertIsNone(row["clearance_date"])
            self.assertEqual(row["status"], "Pending")

        _columns, cleared = self._run(status="Cleared", include_cancelled=0)
        for row in cleared:
            self.assertTrue(row["clearance_date"])
            self.assertEqual(row["status"], "Cleared")


class TestBankedBy(FrappeTestCase):
    """The cross-check: a cleared cheque shows the document that banked it. Pure logic, no database."""

    HOLDING = "13020100027 - PDC Account - SFB"

    def _cheque(self, amount=100.0):
        return frappe._dict(
            name="ACC-PAY-X", payment_type="Receive", received_amount=amount, paid_amount=amount,
            paid_to=self.HOLDING, paid_from="Debtors", docstatus=1, clearance_date=None,
        )

    def _journal(self, *credits):
        return frappe._dict(name="ACC-JV-X", docstatus=1, posting_date="2026-09-09", credits={self.HOLDING: list(credits)})

    def test_columns_carry_the_banking_document(self):
        from sf_trading.sf_trading.report.pdc_report.pdc_report import get_columns

        fieldnames = [c["fieldname"] for c in get_columns()]
        for expected in ("banked_by", "banking_document", "banking_date", "banking_amount", "cross_check"):
            self.assertIn(expected, fieldnames)

    def test_a_cheque_naming_a_journal_is_cleared_by_it(self):
        from sf_trading.pdc_transfer import position_of

        pe = self._cheque(866.25)
        pos = position_of(pe, transfers=[], returns=[], journals={pe.name: self._journal(138.6, 866.25)})
        self.assertEqual(pos.status, "Cleared")
        self.assertEqual(pos.cleared, 866.25)
        self.assertTrue(pos.by_journal)

    def test_a_journal_does_not_clear_a_cheque_that_has_a_transfer(self):
        from sf_trading.pdc_transfer import position_of

        pe = self._cheque(100)
        transfer = frappe._dict(name="T", docstatus=1, paid_amount=40, posting_date="2026-09-01")
        pos = position_of(pe, transfers=[transfer], returns=[], journals={pe.name: self._journal(100)})
        self.assertFalse(pos.by_journal)
        self.assertEqual(pos.status, "Partly Cleared")

    def test_cross_check_reads_matches_when_the_journal_has_a_line_of_the_cheques_size(self):
        from sf_trading.pdc_transfer import position_of
        from sf_trading.sf_trading.report.pdc_report.pdc_report import banked_by_of

        pe = self._cheque(866.25)
        journals = {pe.name: self._journal(138.6, 866.25)}
        pos = position_of(pe, transfers=[], returns=[], journals=journals)
        self.assertEqual(banked_by_of(pe, pos, [], journals[pe.name])[0::2], ("Journal Entry", "Matches"))

    def test_cross_check_flags_a_journal_with_no_line_of_the_cheques_size(self):
        from sf_trading.pdc_transfer import position_of
        from sf_trading.sf_trading.report.pdc_report.pdc_report import banked_by_of

        pe = self._cheque(108.9)
        journals = {pe.name: self._journal(108.0)}
        pos = position_of(pe, transfers=[], returns=[], journals=journals)
        by, _journal, check = banked_by_of(pe, pos, [], journals[pe.name])
        self.assertEqual(by, "Journal Entry")
        self.assertTrue(check.startswith("Check"))

    def test_a_cheque_cleared_by_a_date_alone_says_no_document_is_linked(self):
        from sf_trading.pdc_transfer import position_of
        from sf_trading.sf_trading.report.pdc_report.pdc_report import banked_by_of

        pe = self._cheque()
        pe.clearance_date = "2026-09-09"
        pos = position_of(pe, transfers=[], returns=[], journals={})
        by, journal, check = banked_by_of(pe, pos, [], None)
        self.assertEqual((by, journal), ("Clearance Date Only", None))
        self.assertTrue(check.startswith("No document"))

    def test_a_transfer_that_banks_the_whole_cheque_matches(self):
        from sf_trading.pdc_transfer import position_of
        from sf_trading.sf_trading.report.pdc_report.pdc_report import banked_by_of

        pe = self._cheque(100)
        transfer = frappe._dict(name="T", docstatus=1, paid_amount=100, posting_date="2026-09-01")
        pos = position_of(pe, transfers=[transfer], returns=[], journals={})
        self.assertEqual(banked_by_of(pe, pos, [transfer], None)[0::2], ("Internal Transfer", "Matches"))

    def test_a_pending_cheque_has_no_banking_document(self):
        from sf_trading.pdc_transfer import position_of
        from sf_trading.sf_trading.report.pdc_report.pdc_report import banked_by_of

        pe = self._cheque()
        pos = position_of(pe, transfers=[], returns=[], journals={})
        self.assertEqual(banked_by_of(pe, pos, [], None), ("", None, ""))

    def test_one_column_names_the_transfer_or_the_journal(self):
        from sf_trading.sf_trading.report.pdc_report.pdc_report import banking_document, get_columns

        link = next(c for c in get_columns() if c["fieldname"] == "banking_document")
        self.assertEqual((link["fieldtype"], link["options"]), ("Dynamic Link", "banking_doctype"))

        pe = self._cheque(100)
        first = frappe._dict(name="T1", docstatus=1, paid_amount=40, posting_date="2026-09-01")
        second = frappe._dict(name="T2", docstatus=1, paid_amount=60, posting_date="2026-09-05")
        doc = banking_document(pe, "Internal Transfer", [first, second], None)
        self.assertEqual((doc.doctype, doc.name, doc.amount), ("Payment Entry", "T2", 100.0))
        self.assertEqual(str(doc.date), "2026-09-05")

        doc = banking_document(pe, "Journal Entry", [], self._journal(100.0))
        self.assertEqual((doc.doctype, doc.name, doc.amount), ("Journal Entry", "ACC-JV-X", 100.0))
        self.assertEqual(str(doc.date), "2026-09-09")

        none = banking_document(pe, "", [], None)
        self.assertEqual((none.doctype, none.name), (None, None))
