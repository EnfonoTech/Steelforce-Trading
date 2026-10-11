"""Sales Order Tracker: the state of an order, what is suggested, and the close action.

    bench --site <site> run-tests --module sf_trading.tests.test_sales_order_tracker
"""

from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import add_days, getdate, nowdate

from sf_trading.sf_trading.report.sales_order_tracker import sales_order_tracker as sot

TODAY = getdate(nowdate())


def order(**values):
	base = dict(name="SO-1", status="To Deliver and Bill", delivery_date=add_days(TODAY, -10), per_delivered=50,
		per_billed=0, advance_paid=0, skip_delivery_note=0, grand_total=100, transaction_date=add_days(TODAY, -30),
		customer="C", customer_name="C", branch=None)
	base.update(values)
	return frappe._dict(base)


def invoice(grand_total, outstanding):
	return frappe._dict(grand_total=grand_total, outstanding_amount=outstanding)


class TestClassify(FrappeTestCase):
	def test_an_overdue_order_with_nothing_settled_or_returned_is_plain_overdue(self):
		self.assertEqual(sot.classify("To Deliver", 10, sot.UNPAID, False, False, False), sot.OVERDUE)

	def test_billed_and_paid_in_full_but_short_of_delivery_is_overdue_settled(self):
		self.assertEqual(sot.classify("To Deliver", 10, sot.PAID, False, True, True), sot.OVERDUE_SETTLED)

	def test_paid_but_not_fully_billed_is_still_plain_overdue(self):
		self.assertEqual(sot.classify("To Deliver and Bill", 10, sot.PAID, False, True, False), sot.OVERDUE)

	def test_a_return_makes_it_overdue_returned_whatever_was_paid(self):
		self.assertEqual(sot.classify("To Deliver", 10, sot.PAID, True, True, True), sot.OVERDUE_RETURNED)
		self.assertEqual(sot.classify("To Deliver", 10, sot.UNPAID, True, True, False), sot.OVERDUE_RETURNED)

	def test_not_overdue_is_converted_or_not_due(self):
		self.assertEqual(sot.classify("To Bill", 0, sot.UNPAID, False, True, False), sot.CONVERTED)
		self.assertEqual(sot.classify("To Deliver and Bill", 0, sot.NOT_INVOICED, False, False, False), sot.NOT_DUE)

	def test_the_closing_statuses_of_erpnext_are_kept(self):
		for status in ("Closed", "Completed", "On Hold"):
			self.assertEqual(sot.classify(status, 99, sot.PAID, True, True, True), status)


class TestPayment(FrappeTestCase):
	def test_paid_part_paid_unpaid(self):
		self.assertEqual(sot.payment_state(order(), {"A": invoice(100, 0)})[0], sot.PAID)
		self.assertEqual(sot.payment_state(order(), {"A": invoice(100, 0.004)})[0], sot.PAID)
		self.assertEqual(sot.payment_state(order(), {"A": invoice(100, 40)})[0], sot.PART_PAID)
		self.assertEqual(sot.payment_state(order(), {"A": invoice(100, 100)})[0], sot.UNPAID)

	def test_several_invoices_are_summed(self):
		state, invoiced, outstanding = sot.payment_state(order(), {"A": invoice(60, 0), "B": invoice(40, 40)})
		self.assertEqual((state, invoiced, outstanding), (sot.PART_PAID, 100, 40))

	def test_no_invoice_reads_the_advance(self):
		self.assertEqual(sot.payment_state(order(advance_paid=50), {})[0], sot.ADVANCE_ONLY)
		self.assertEqual(sot.payment_state(order(), {})[0], sot.NOT_INVOICED)


class TestRow(FrappeTestCase):
	def facts(self, **kw):
		f = frappe._dict(open_qty={}, invoices={}, delivery_notes={}, si_returns={}, dn_returns={}, pending_return_qty={})
		f.update(kw)
		return f

	def test_the_paid_and_returned_order_the_client_described(self):
		# billed 100%, paid, a credit note recorded, delivered qty brought down by the return
		facts = self.facts(invoices={"SO-1": {"SI-1": invoice(100, 0)}},
			si_returns={"SO-1": {"SI-R1": frappe._dict(grand_total=-30)}})
		row = sot.build_row(order(per_billed=100, per_delivered=72), facts, TODAY)
		self.assertEqual(row.tracking_state, sot.OVERDUE_RETURNED)
		self.assertEqual(row.days_overdue, 10)
		self.assertEqual(row.returned_amount, 30)
		self.assertEqual(row.payment_state, sot.PAID)
		self.assertIn("Close", row.suggested_action)
		self.assertTrue(row.can_close)

	def test_returned_goods_still_to_come_back_say_so(self):
		facts = self.facts(invoices={"SO-1": {"SI-1": invoice(100, 0)}},
			si_returns={"SO-1": {"SI-R1": frappe._dict(grand_total=-30)}}, pending_return_qty={"SO-1": 4})
		row = sot.build_row(order(per_billed=100), facts, TODAY)
		self.assertIn("Delivery Note return", row.suggested_action)
		self.assertNotIn("Close", row.suggested_action)

	def test_an_unpaid_returned_order_is_not_offered_for_closing(self):
		facts = self.facts(invoices={"SO-1": {"SI-1": invoice(100, 100)}},
			si_returns={"SO-1": {"SI-R1": frappe._dict(grand_total=-30)}})
		row = sot.build_row(order(per_billed=100), facts, TODAY)
		self.assertIn("collect or credit", row.suggested_action)

	def test_skip_delivery_note_orders_are_never_overdue(self):
		row = sot.build_row(order(skip_delivery_note=1), self.facts(), TODAY)
		self.assertEqual(row.days_overdue, 0)
		self.assertEqual(row.tracking_state, sot.NOT_DUE)

	def test_a_fully_delivered_order_is_not_overdue(self):
		row = sot.build_row(order(per_delivered=100), self.facts(), TODAY)
		self.assertEqual(row.days_overdue, 0)

	def test_a_closed_order_cannot_be_closed_again(self):
		row = sot.build_row(order(status="Closed"), self.facts(), TODAY)
		self.assertFalse(row.can_close)
		self.assertEqual(row.tracking_state, sot.CLOSED)


class TestFilters(FrappeTestCase):
	def row(self, **values):
		base = dict(tracking_state=sot.OVERDUE, needs_attention=True, payment_state=sot.UNPAID, has_return=0)
		base.update(values)
		return frappe._dict(base)

	def test_needs_attention_groups_the_three_overdue_states(self):
		f = frappe._dict(tracking_state="Needs Attention")
		self.assertTrue(sot.wanted(self.row(), f))
		self.assertFalse(sot.wanted(self.row(tracking_state=sot.CONVERTED, needs_attention=False), f))

	def test_completed_and_closed_are_hidden_until_asked_for(self):
		done = self.row(tracking_state=sot.CLOSED, needs_attention=False)
		self.assertFalse(sot.wanted(done, frappe._dict()))
		self.assertTrue(sot.wanted(done, frappe._dict(include_closed=1)))
		self.assertTrue(sot.wanted(done, frappe._dict(tracking_state=sot.CLOSED)))

	def test_payment_and_return_filters(self):
		self.assertFalse(sot.wanted(self.row(), frappe._dict(payment_state=sot.PAID)))
		self.assertFalse(sot.wanted(self.row(), frappe._dict(has_return=1)))
		self.assertTrue(sot.wanted(self.row(has_return=1), frappe._dict(has_return=1)))


class TestReport(FrappeTestCase):
	def test_it_runs_on_real_data_and_every_row_has_a_known_state(self):
		company = frappe.db.get_value("Company", {}, "name")
		columns, rows, _msg, _chart, summary = sot.execute({"company": company, "include_closed": 1})
		self.assertTrue(columns)
		known = {sot.OVERDUE, sot.OVERDUE_SETTLED, sot.OVERDUE_RETURNED, sot.CONVERTED, sot.NOT_DUE, sot.ON_HOLD,
			sot.COMPLETED, sot.CLOSED}
		self.assertTrue(all(r.tracking_state in known for r in rows))
		self.assertEqual(summary[0]["value"], len(rows))

	def test_a_company_is_required(self):
		with self.assertRaises(frappe.ValidationError):
			sot.execute({})


class FakeOrder(frappe._dict):
	def check_permission(self, ptype):
		pass

	def add_comment(self, *args, **kwargs):
		self.commented = True


class TestClose(FrappeTestCase):
	def test_a_reason_is_required(self):
		with self.assertRaises(frappe.ValidationError):
			sot.close_orders(["SO-1"], "  ")

	def test_nothing_selected_is_refused(self):
		with self.assertRaises(frappe.ValidationError):
			sot.close_orders([], "why")

	def test_only_open_orders_are_closed_and_the_rest_are_named(self):
		docs = {"SO-OPEN": FakeOrder(name="SO-OPEN", docstatus=1, status="To Deliver and Bill"),
			"SO-DONE": FakeOrder(name="SO-DONE", docstatus=1, status="Completed")}
		with patch.object(frappe, "get_doc", side_effect=lambda dt, name: docs[name]), \
				patch("erpnext.selling.doctype.sales_order.sales_order.update_status") as update:
			out = sot.close_orders(["SO-OPEN", "SO-DONE"], "Paid and returned")
		update.assert_called_once_with("Closed", "SO-OPEN")
		self.assertEqual(out["closed"], ["SO-OPEN"])
		self.assertEqual(out["refused"], [{"name": "SO-DONE", "status": "Completed"}])
		self.assertTrue(docs["SO-OPEN"].commented)


class TestCloseCheck(FrappeTestCase):
	def check(self, status="To Deliver and Bill", state=sot.OVERDUE_RETURNED, billed_in_full=True, paid=sot.PAID,
			outstanding=0, returned=True, pending=0, per_delivered=72, per_billed=100):
		return sot.close_check(status, state, billed_in_full, paid, outstanding, returned, pending, per_delivered,
			per_billed)

	def test_billed_paid_and_returned_with_the_goods_back_is_ready(self):
		verdict, reason = self.check()
		self.assertEqual(verdict, sot.READY_TO_CLOSE)
		self.assertIn("back in stock", reason)

	def test_goods_still_to_come_back_means_review(self):
		verdict, reason = self.check(pending=4)
		self.assertEqual(verdict, sot.REVIEW_BEFORE_CLOSING)
		self.assertIn("Delivery Note return", reason)

	def test_an_unpaid_or_part_billed_returned_order_means_review(self):
		verdict, reason = self.check(paid=sot.UNPAID, outstanding=100, billed_in_full=False, per_billed=60)
		self.assertEqual(verdict, sot.REVIEW_BEFORE_CLOSING)
		self.assertIn("unpaid", reason)
		self.assertIn("billed 60%", reason)

	def test_paid_but_short_of_delivery_with_no_return_is_a_judgement_call(self):
		verdict, reason = self.check(state=sot.OVERDUE_SETTLED, returned=False)
		self.assertEqual(verdict, sot.REVIEW_BEFORE_CLOSING)
		self.assertIn("72% delivered", reason)

	def test_goods_or_money_owed_means_keep_open(self):
		verdict, reason = self.check(state=sot.OVERDUE, returned=False, paid=sot.NOT_INVOICED, billed_in_full=False,
			per_delivered=50, per_billed=0)
		self.assertEqual(verdict, sot.KEEP_OPEN)
		self.assertIn("delivered 50%", reason)

	def test_closed_completed_and_on_hold(self):
		self.assertEqual(self.check(status="Closed")[0], sot.ALREADY_CLOSED)
		self.assertEqual(self.check(status="Completed")[0], sot.ALREADY_CLOSED)
		self.assertEqual(self.check(status="On Hold")[0], sot.KEEP_OPEN)

	def test_the_row_carries_the_check_and_it_can_be_filtered_on(self):
		facts = frappe._dict(open_qty={}, invoices={"SO-1": {"SI-1": invoice(100, 0)}}, delivery_notes={},
			si_returns={"SO-1": {"SI-R1": frappe._dict(grand_total=-30)}}, dn_returns={}, pending_return_qty={})
		row = sot.build_row(order(per_billed=100, per_delivered=72), facts, TODAY)
		self.assertEqual(row.close_check, sot.READY_TO_CLOSE)
		self.assertTrue(sot.wanted(row, frappe._dict(close_check=sot.READY_TO_CLOSE)))
		self.assertFalse(sot.wanted(row, frappe._dict(close_check=sot.KEEP_OPEN)))


class TestDetailAndFilters(FrappeTestCase):
	def test_converted_to(self):
		self.assertEqual(sot.converted_to({"A": 1}, {"D": 1}), "Both")
		self.assertEqual(sot.converted_to({"A": 1}, {}), "Invoice")
		self.assertEqual(sot.converted_to({}, {"D": 1}), "Delivery Note")
		self.assertEqual(sot.converted_to({}, {}), "Neither")

	def test_return_stock_status(self):
		self.assertEqual(sot.return_stock_status({}, {}, 0), sot.NO_RETURN)
		self.assertEqual(sot.return_stock_status({"R": 1}, {}, 0), sot.STOCK_RESTORED)
		self.assertEqual(sot.return_stock_status({"R": 1}, {}, 3), sot.AWAITING_DN_RETURN)

	def test_converted_and_return_stock_filters(self):
		row = frappe._dict(tracking_state=sot.OVERDUE, needs_attention=True, payment_state=sot.UNPAID, has_return=1,
			converted_to="Invoice", return_stock=sot.AWAITING_DN_RETURN, close_check=sot.REVIEW_BEFORE_CLOSING)
		self.assertTrue(sot.wanted(row, frappe._dict(converted_to="Invoice", return_stock=sot.AWAITING_DN_RETURN)))
		self.assertFalse(sot.wanted(row, frappe._dict(converted_to="Both")))
		self.assertFalse(sot.wanted(row, frappe._dict(return_stock=sot.STOCK_RESTORED)))

	def test_item_rows_expand_each_order_into_its_lines(self):
		parent = frappe._dict(sales_order="SO-1", needs_attention=True, days_overdue=5, tracking_state=sot.OVERDUE,
			customer_name="C")
		lines = [
			frappe._dict(parent="SO-1", idx=2, item_code="B", item_name="Item B", item_group="G", uom="Nos", qty=10,
				delivered_qty=4, returned_qty=1, rate=5, amount=50, billed_amt=20, warehouse="W"),
			frappe._dict(parent="SO-1", idx=1, item_code="A", item_name="Item A", item_group="G", uom="Nos", qty=3,
				delivered_qty=3, returned_qty=0, rate=2, amount=6, billed_amt=6, warehouse="W"),
		]
		with patch.object(frappe, "get_all", return_value=lines):
			rows = sot.item_rows([parent], frappe._dict())
		self.assertEqual([r.item_code for r in rows], ["A", "B"])
		self.assertEqual(rows[1].pending_qty, 6)
		self.assertEqual(rows[1].returned_qty, 1)
		self.assertEqual(rows[1].tracking_state, sot.OVERDUE)  # the order's state rides on every line

	def test_item_rows_without_orders_is_empty(self):
		self.assertEqual(sot.item_rows([], frappe._dict()), [])

	def test_no_item_filter_means_no_restriction(self):
		self.assertIsNone(sot.orders_with_item(frappe._dict()))
		with patch.object(frappe, "get_all", return_value=["SO-1", "SO-1", "SO-2"]):
			self.assertEqual(sot.orders_with_item(frappe._dict(item_code="X")), {"SO-1", "SO-2"})

	def test_the_real_report_runs_item_wise_and_with_the_new_filters(self):
		company = frappe.db.get_value("Company", {}, "name")
		columns, rows, *_ = sot.execute({"company": company, "include_closed": 1, "view": "Item Wise"})
		self.assertIn("item_code", [c["fieldname"] for c in columns])
		self.assertTrue(all("item_code" in r for r in rows))
		columns, rows, *_ = sot.execute({"company": company, "include_closed": 1, "converted_to": "Neither",
			"close_check": "Keep Open"})
		self.assertIn("close_check", [c["fieldname"] for c in columns])
