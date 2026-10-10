"""Client tracker batch 2: every new or rebuilt report runs on real data and adds up, and the two
approval routes decide what they should.

Read-only against the site's own data (FrappeTestCase rolls back), so it doubles as a smoke test
after a deploy:

    bench --site <site> run-tests --module sf_trading.tests.test_oct10_batch2
"""

from unittest.mock import MagicMock, patch

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import add_months, flt, getdate, nowdate

from sf_trading import approval_routing as routing

VIEWS = ("Transactions", "Category", "Category and Party", "Party", "Voucher Type", "Account",
	"Mode of Payment", "Branch", "Day", "Month", "Receivables and Payables")


def _busiest_company():
	row = frappe.get_all("GL Entry", fields=["company"], filters={"is_cancelled": 0},
		order_by="posting_date desc", limit=1)
	return row[0].company if row else frappe.db.get_value("Company", {}, "name")


def _run(report, filters):
	from frappe.desk.query_report import run

	return run(report, filters=filters, ignore_prepared_report=True)


def _totals(result):
	rows = [r for r in result["result"] if isinstance(r, dict) and r.get("is_total")]
	return rows[-1] if rows else None


class TestCashFlowReports(FrappeTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		cls.company = _busiest_company()
		to = getdate(nowdate())
		cls.filters = {"company": cls.company, "from_date": str(add_months(to, -2)), "to_date": str(to)}

	def test_every_cash_flow_detail_view_runs(self):
		for view in VIEWS:
			with self.subTest(view=view):
				result = _run("Cash Flow Detail", dict(self.filters, group_by=view))
				self.assertIsInstance(result["result"], list)

	def test_grouped_views_add_up_to_the_transactions(self):
		base = _totals(_run("Cash Flow Detail", dict(self.filters, group_by="Transactions")))
		if not base:
			self.skipTest("no money movement in the window")
		for view in ("Category", "Party", "Voucher Type", "Account", "Branch", "Month"):
			with self.subTest(view=view):
				total = _totals(_run("Cash Flow Detail", dict(self.filters, group_by=view)))
				self.assertAlmostEqual(flt(total["money_in"]), flt(base["money_in"]), places=2)
				self.assertAlmostEqual(flt(total["money_out"]), flt(base["money_out"]), places=2)

	def test_movement_filters_drop_the_running_balance(self):
		result = _run("Cash Flow Detail", dict(self.filters, group_by="Transactions", direction="Money In"))
		total = _totals(result)
		if total:
			self.assertIsNone(total.get("running"))
			self.assertAlmostEqual(flt(total["money_out"]), 0, places=3)

	def test_money_in_vs_out_matches_the_detail(self):
		base = _totals(_run("Cash Flow Detail", dict(self.filters, group_by="Transactions")))
		summary = _run("Cash Flow Money In vs Money Out", dict(self.filters))
		rows = [r for r in summary["result"] if isinstance(r, dict) and r.get("label") == "Total"]
		if not (base and rows):
			self.skipTest("no money movement in the window")
		self.assertAlmostEqual(flt(rows[-1]["money_in"]), flt(base["money_in"]), places=2)
		self.assertAlmostEqual(flt(rows[-1]["money_out"]), flt(base["money_out"]), places=2)

	def test_item_wise_cash_flow_runs_and_shares_add_up(self):
		# the report's own execute: the desk's total row would add a second 100% to the sum
		from sf_trading.sf_trading.report.item_wise_cash_flow.item_wise_cash_flow import execute

		self.assertIsInstance(_run("Item-wise Cash Flow", dict(self.filters))["result"], list)
		for group_by in ("Item", "Item Group"):
			with self.subTest(group_by=group_by):
				rows = execute(frappe._dict(self.filters, group_by=group_by))[1]
				shares = [flt(r.get("sales_share")) for r in rows if r.get("sales_share") is not None]
				if shares:
					# each share is rounded to 2 places
					self.assertAlmostEqual(sum(shares), 100, delta=0.5 + 0.005 * len(shares))


class TestReceivableReports(FrappeTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		cls.company = _busiest_company()

	def test_branch_wise_credit_outstanding_views(self):
		for view in ("Customer and Branch", "Branch Summary", "Document Detail"):
			with self.subTest(view=view):
				result = _run("Branch-wise Credit Outstanding",
					{"company": self.company, "as_on_date": nowdate(), "view": view})
				self.assertIsInstance(result["result"], list)

	def test_delivery_person_payment_pending_views(self):
		for view in ("Invoice Wise", "Delivery Person Summary"):
			with self.subTest(view=view):
				result = _run("Delivery Person Payment Pending",
					{"company": self.company, "view": view, "status": "All"})
				self.assertIsInstance(result["result"], list)

	def test_mode_of_payment_report_runs_and_never_settles_a_credit_note_by_cash(self):
		to = getdate(nowdate())
		result = _run("Mode of Payment Invoice Wise",
			{"company": self.company, "from_date": str(add_months(to, -1)), "to_date": str(to)})
		for row in result["result"]:
			if isinstance(row, dict) and row.get("payment_class") in ("Credit Adjusted", "Returned"):
				self.assertFalse(row.get("mode_mismatch"))

	def test_pdc_report_runs_with_the_new_statuses(self):
		for status in ("", "Pending", "Partly Cleared", "Cleared", "Returned", "Rejected"):
			with self.subTest(status=status):
				result = _run("PDC Report", {"company": self.company, "status": status})
				for row in result["result"]:
					if isinstance(row, dict) and row.get("status") not in ("Cancelled", "Draft", "Transfer"):
						self.assertGreaterEqual(flt(row.get("remaining_amount")), 0)
						self.assertAlmostEqual(
							flt(row["amount"]),
							flt(row["cleared_amount"]) + flt(row["returned_amount"]) + flt(row["remaining_amount"]),
							places=2,
						)


class TestApprovalRouting(FrappeTestCase):
	def test_sales_order_workflow_governs_submitted_orders_only(self):
		self.assertFalse(routing.workflow_applicability("Sales Order", frappe._dict(docstatus=0))["applies"])
		self.assertTrue(routing.workflow_applicability("Sales Order", frappe._dict(docstatus=1))["applies"])
		self.assertIsNone(routing.workflow_applicability("Sales Invoice", frappe._dict(docstatus=1)))

	def test_opening_stock_is_not_routed(self):
		with patch.object(routing, "_active", return_value=True):
			opening = routing.workflow_applicability("Stock Reconciliation", frappe._dict(purpose="Opening Stock"))
			count = routing.workflow_applicability("Stock Reconciliation", frappe._dict(purpose="Stock Reconciliation"))
		self.assertFalse(opening["applies"])
		self.assertTrue(count["applies"] and count["guard_submit"])

	def _order(self, was, now, comment=None, remark=None):
		doc = MagicMock()
		doc.flags = frappe._dict(pm_workflow_comment=comment)
		values = {"workflow_state": now, routing.REMARK_FIELD: remark}
		doc.get = lambda key, default=None: values.get(key, default)
		doc.set = lambda key, value: values.__setitem__(key, value)
		doc.get_doc_before_save = lambda: frappe._dict(workflow_state=was)
		return doc, values

	def test_a_cancellation_request_carries_its_reason(self):
		doc, values = self._order(routing.SUBMITTED, routing.REQUESTED, comment="wrong customer")
		routing.capture_cancellation_reason(doc)
		self.assertEqual(values[routing.REMARK_FIELD], "wrong customer")

	def test_a_request_without_a_reason_is_refused(self):
		doc, _values = self._order(routing.SUBMITTED, routing.REQUESTED)
		with self.assertRaises(frappe.ValidationError):
			routing.capture_cancellation_reason(doc)

	def test_a_rejected_request_clears_the_reason(self):
		doc, values = self._order(routing.REQUESTED, routing.SUBMITTED, remark="old reason")
		routing.capture_cancellation_reason(doc)
		self.assertIsNone(values[routing.REMARK_FIELD])

	def test_a_count_needs_its_sheet_attached(self):
		doc = frappe._dict(doctype="Stock Reconciliation", name="MAT-RECO-TEST-NONE", purpose="Stock Reconciliation")
		with patch.object(frappe.session, "user", "someone@example.com"):
			with self.assertRaises(frappe.ValidationError):
				routing.require_attachment(doc)
		routing.require_attachment(frappe._dict(doc, purpose="Opening Stock"))

	def test_workflow_definitions_are_well_formed(self):
		for definition in (routing.sales_order_workflow(), routing.stock_reconciliation_workflow()):
			states = {s["state"] for s in definition["states"]}
			for t in definition["transitions"]:
				self.assertIn(t["state"], states)
				self.assertIn(t["next_state"], states)


class TestColleagueApprovals(FrappeTestCase):
	def test_colleague_feed_answers(self):
		try:
			from permission_manager.api import approvals
		except ImportError:
			self.skipTest("permission_manager not installed")
		if not hasattr(approvals, "get_colleague_approvals"):
			self.skipTest("permission_manager without the colleague feed")
		rows = approvals.get_colleague_approvals(limit=5)
		rows = rows.get("rows", rows) if isinstance(rows, dict) else rows
		self.assertIsInstance(rows, list)
		for row in rows:
			self.assertNotEqual(row.get("completed_by"), frappe.session.user)
