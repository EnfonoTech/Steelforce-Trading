"""Sales Order Tracker: the state of an order, what is suggested, and the close action.

    bench --site <site> run-tests --module sf_trading.tests.test_sales_order_tracker
"""

from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import add_days, getdate, nowdate

from sf_trading import order_stock
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
			si_returns={"SO-1": {"SI-R1": frappe._dict(grand_total=-30, reason="Over Supply")}})
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
			outstanding=0, returned=True, pending=0, per_delivered=72, per_billed=100, reasons=("Over Supply",),
			credited=True):
		return sot.close_check(status, state, billed_in_full, paid, outstanding, returned, pending, per_delivered,
			per_billed, reasons=reasons, credited=credited)

	def test_billed_paid_and_returned_with_the_goods_back_is_ready(self):
		verdict, reason = self.check()
		self.assertEqual(verdict, sot.READY_TO_CLOSE)
		self.assertIn("back in stock", reason)

	def test_a_return_reason_that_may_mean_a_replacement_needs_a_look(self):
		for reason in ("Damaged", "Defective", "Wrong Item", "Wrong Quantity", "Quality Issue", "Other"):
			verdict, text = self.check(reasons=(reason,))
			self.assertEqual(verdict, sot.REVIEW_BEFORE_CLOSING, reason)
			self.assertIn("replacement", text)

	def test_a_return_with_no_reason_recorded_needs_a_look(self):
		verdict, text = self.check(reasons=())
		self.assertEqual(verdict, sot.REVIEW_BEFORE_CLOSING)
		self.assertIn("not recorded", text)

	def test_reasons_that_owe_the_customer_nothing_are_ready(self):
		for reason in sot.NO_REPLACEMENT_REASONS:
			self.assertEqual(self.check(reasons=(reason,))[0], sot.READY_TO_CLOSE, reason)

	def test_goods_back_by_delivery_note_but_no_credit_note_means_review(self):
		verdict, text = self.check(credited=False)
		self.assertEqual(verdict, sot.REVIEW_BEFORE_CLOSING)
		self.assertIn("no credit note", text)

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
			si_returns={"SO-1": {"SI-R1": frappe._dict(grand_total=-30, reason="Over Supply")}}, dn_returns={},
			pending_return_qty={})
		row = sot.build_row(order(per_billed=100, per_delivered=72), facts, TODAY)
		self.assertEqual(row.close_check, sot.READY_TO_CLOSE)
		self.assertEqual(row.return_reasons, "Over Supply")
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


class TestWhyNotInvoiced(FrappeTestCase):
	"""The reason an open order has no invoice, beside its tracking state (sf_trading/order_stock.py)."""

	def stock_facts(self, lines=None, **kw):
		stock = frappe._dict(lines=lines or {}, draft_invoices={}, draft_notes={}, delivered_notes={}, blocks={})
		stock.update(kw)
		return frappe._dict(open_qty={}, invoices={}, delivery_notes={}, si_returns={}, dn_returns={}, pending_return_qty={},
			stock=stock)

	def held(self, item="ITEM-A", pending=10, actual=100, warehouse="SFSB - SFB", name="r1"):
		row = frappe._dict(name=name, item_code=item, pending=pending, pending_stock=pending, actual=actual, reserved=0,
			warehouse=warehouse, tracked=True, elsewhere={"SFSS - SFB": 7})
		row.state = order_stock.line_state(pending, actual)
		return row

	def test_stock_there_and_nothing_raised_is_flagged_and_counted_as_waiting(self):
		facts = self.stock_facts({"SO-1": [self.held()]})
		row = sot.build_row(order(per_delivered=0, delivery_date=TODAY), facts, TODAY)
		self.assertEqual(row.why_not_invoiced, order_stock.STOCK_READY)
		self.assertEqual(row.stock_check, order_stock.IN_STOCK)
		self.assertEqual(row.stock_there, 1)
		self.assertEqual(row.waiting_days, 30)
		self.assertEqual(row.stock_drilldown, "SO-1")
		self.assertIn("nothing has been raised", row.why_detail)

	def test_short_stock_is_the_reason_and_not_counted_as_stock_there(self):
		facts = self.stock_facts({"SO-1": [self.held(pending=40, actual=22)]})
		row = sot.build_row(order(per_delivered=0, delivery_date=TODAY), facts, TODAY)
		self.assertEqual((row.why_not_invoiced, row.stock_check, row.stock_there),
			(order_stock.PART_STOCK, order_stock.PART_STOCK, 0))
		self.assertIn("22 of 40 at SFSB - SFB", row.why_detail)
		self.assertIn("also 7 in SFSS - SFB", row.why_detail)

	def test_a_draft_invoice_is_the_reason_even_with_the_stock_there(self):
		facts = self.stock_facts({"SO-1": [self.held()]}, draft_invoices={"SO-1": {"20010008906"}})
		row = sot.build_row(order(per_delivered=0, delivery_date=TODAY), facts, TODAY)
		self.assertEqual(row.why_not_invoiced, order_stock.DRAFT_INVOICE)
		# the stock is there all the same: it is still one the report counts as "stock there, not invoiced"
		self.assertEqual(row.stock_there, 1)

	def test_an_order_on_hold_or_not_due_is_waiting_on_purpose_and_is_not_stock_there_not_invoiced(self):
		facts = self.stock_facts({"SO-1": [self.held()]})
		held = sot.build_row(order(status="On Hold", per_delivered=0), facts, TODAY)
		not_due = sot.build_row(order(per_delivered=0, delivery_date=add_days(TODAY, 4)), facts, TODAY)
		self.assertEqual((held.why_not_invoiced, held.stock_there), (order_stock.ON_HOLD, 0))
		self.assertEqual((not_due.why_not_invoiced, not_due.stock_there), (order_stock.NOT_DUE, 0))

	def test_the_customer_the_invoice_would_be_refused_for(self):
		facts = self.stock_facts({"SO-1": [self.held()]}, blocks={("C", ""): ["Customer is not approved"]})
		row = sot.build_row(order(per_delivered=0, delivery_date=TODAY), facts, TODAY)
		self.assertEqual(row.why_not_invoiced, order_stock.CUSTOMER_BLOCKED)

	def test_a_finished_or_billed_order_has_no_reason(self):
		facts = self.stock_facts({"SO-1": [self.held()]})
		for values in (dict(status="Completed"), dict(status="Closed"), dict(per_billed=100)):
			row = sot.build_row(order(**values), facts, TODAY)
			self.assertEqual((row.why_not_invoiced, row.why_detail, row.stock_there, row.waiting_days, row.stock_drilldown),
				("", "", 0, 0, ""), values)

	def test_without_the_stock_facts_nothing_changes(self):
		facts = frappe._dict(open_qty={}, invoices={}, delivery_notes={}, si_returns={}, dn_returns={}, pending_return_qty={})
		row = sot.build_row(order(), facts, TODAY)
		self.assertEqual((row.why_not_invoiced, row.stock_check, row.stock_there), ("", "", 0))

	def test_filters_by_the_reason_and_by_the_stock(self):
		row = frappe._dict(tracking_state=sot.CONVERTED, needs_attention=False, payment_state=sot.NOT_INVOICED, has_return=0,
			why_not_invoiced=order_stock.STOCK_READY, stock_check=order_stock.IN_STOCK)
		self.assertTrue(sot.wanted(row, frappe._dict(why_not_invoiced=order_stock.STOCK_READY)))
		self.assertFalse(sot.wanted(row, frappe._dict(why_not_invoiced=order_stock.NO_STOCK)))
		self.assertTrue(sot.wanted(row, frappe._dict(stock_check=order_stock.IN_STOCK)))
		self.assertFalse(sot.wanted(row, frappe._dict(stock_check=order_stock.NO_STOCK)))

	def test_the_summary_counts_stock_there_and_waiting_for_stock(self):
		def row(reason, check):
			return frappe._dict(tracking_state=sot.CONVERTED, close_check=sot.KEEP_OPEN, outstanding_amount=0, why_not_invoiced=reason,
				stock_there=1 if reason and check == order_stock.IN_STOCK else 0)
		rows = [row(order_stock.STOCK_READY, order_stock.IN_STOCK), row(order_stock.DRAFT_INVOICE, order_stock.IN_STOCK),
			row(order_stock.NO_STOCK, order_stock.NO_STOCK), row(order_stock.PART_STOCK, order_stock.PART_STOCK), row("", "")]
		by_label = {card["label"]: card["value"] for card in sot.get_summary(rows, "BHD")}
		self.assertEqual(by_label["Stock There, Not Invoiced"], 2)
		self.assertEqual(by_label["Waiting For Stock"], 2)

	def test_the_item_wise_view_carries_the_stock_behind_each_line(self):
		parent = frappe._dict(sales_order="SO-1", needs_attention=False, days_overdue=0, tracking_state=sot.CONVERTED,
			customer_name="C")
		lines = [
			frappe._dict(name="r1", parent="SO-1", idx=1, item_code="A", item_name="A", item_group="G", uom="Nos", qty=40,
				delivered_qty=0, returned_qty=0, rate=1, amount=40, billed_amt=0, warehouse="SFSB - SFB"),
			frappe._dict(name="r2", parent="SO-1", idx=2, item_code="B", item_name="B", item_group="G", uom="Nos", qty=3,
				delivered_qty=3, returned_qty=0, rate=1, amount=3, billed_amt=3, warehouse="SFSB - SFB"),
		]
		facts = self.stock_facts({"SO-1": [self.held("A", pending=40, actual=22, name="r1")]})
		with patch.object(frappe, "get_all", return_value=lines):
			rows = sot.item_rows([parent], frappe._dict(), facts)
		first, second = rows
		self.assertEqual((first.line_stock, first.stock_at_warehouse, first.reserved_total), (order_stock.PART_STOCK, 22, 0))
		self.assertEqual(first.stock_elsewhere, "SFSS - SFB 7")
		# a line with nothing left to deliver has no stock to speak of
		self.assertEqual((second.line_stock, second.stock_at_warehouse, second.stock_elsewhere), ("", None, ""))

	def test_the_columns_are_there_in_both_views(self):
		order_wise = [c["fieldname"] for c in sot.get_columns()]
		for name in ("why_not_invoiced", "stock_check", "waiting_days", "why_detail", "stock_drilldown"):
			self.assertIn(name, order_wise)
		item_wise = [c["fieldname"] for c in sot.get_item_columns()]
		for name in ("why_not_invoiced", "line_stock", "stock_at_warehouse", "reserved_total", "stock_elsewhere"):
			self.assertIn(name, item_wise)

	def test_the_close_checks_reason_is_not_confused_with_it(self):
		labels = {c["fieldname"]: c["label"] for c in sot.get_columns()}
		self.assertNotEqual(labels["close_reason"], "Why")
		self.assertIn("Why Not Invoiced", labels["why_not_invoiced"])


class TestWhatTheTeamActsOn(FrappeTestCase):
	"""The reason, what to do about it and where, up front, and the orders to act on first."""

	def stock_facts(self, lines, **kw):
		stock = frappe._dict(lines=lines, draft_invoices={}, draft_notes={}, delivered_notes={}, blocks={})
		stock.update(kw)
		return frappe._dict(open_qty={}, invoices={}, delivery_notes={}, si_returns={}, dn_returns={}, pending_return_qty={}, stock=stock)

	def held(self, item="A", pending=10, actual=100, warehouse="SFSB - SFB", elsewhere=None):
		row = frappe._dict(name=item + "-row", item_code=item, pending=pending, pending_stock=pending, actual=actual, reserved=0,
			warehouse=warehouse, tracked=True, elsewhere=elsewhere or {})
		row.state = order_stock.line_state(pending, actual)
		return row

	def row(self, lines, **values):
		return sot.build_row(order(per_delivered=0, delivery_date=TODAY, **values), self.stock_facts({"SO-1": lines}), TODAY)

	def test_stock_there_says_raise_the_invoice_and_names_the_warehouse(self):
		row = self.row([self.held(warehouse="SFSS - SFB")])
		self.assertEqual((row.why_not_invoiced, row.needs_action, row.warehouses), (order_stock.STOCK_READY, 1, "SFSS - SFB"))
		self.assertIn("Raise the Sales Invoice", row.next_step)

	def test_no_stock_is_not_something_to_invoice_but_says_where_it_sits(self):
		row = self.row([self.held(pending=10, actual=0, elsewhere={"SFWH - SFB": 82})])
		self.assertEqual((row.why_not_invoiced, row.needs_action), (order_stock.NO_STOCK, 0))
		self.assertEqual(row.next_step, "Transfer the stock from SFWH - SFB (82 there) or buy it")

	def test_an_order_with_lines_in_two_warehouses_names_both(self):
		row = self.row([self.held("A", warehouse="SFSS - SFB"), self.held("B", warehouse="SFSB - SFB")])
		self.assertEqual(row.warehouses, "SFSB - SFB, SFSS - SFB")

	def test_a_finished_order_has_no_step_and_nothing_to_act_on(self):
		row = sot.build_row(order(status="Completed"), self.stock_facts({"SO-1": [self.held()]}), TODAY)
		self.assertEqual((row.next_step, row.needs_action, row.warehouses), ("", 0, ""))

	def test_the_orders_to_act_on_come_first_longest_waiting_first(self):
		def r(name, **v):
			base = dict(sales_order=name, needs_action=0, needs_attention=False, days_overdue=0, waiting_days=0)
			base.update(v)
			return frappe._dict(base)
		rows = [r("OVERDUE-NOT-ACTIONABLE", needs_attention=True, days_overdue=30), r("ACT-1-DAY", needs_action=1, waiting_days=1),
			r("QUIET"), r("ACT-9-DAYS", needs_action=1, waiting_days=9), r("ACT-3-DAYS", needs_action=1, waiting_days=3)]
		rows.sort(key=sot.row_order)
		self.assertEqual([x.sales_order for x in rows], ["ACT-9-DAYS", "ACT-3-DAYS", "ACT-1-DAY", "OVERDUE-NOT-ACTIONABLE", "QUIET"])

	def test_needs_action_now_shows_only_those(self):
		row = frappe._dict(tracking_state=sot.CONVERTED, needs_attention=False, payment_state=sot.NOT_INVOICED, has_return=0,
			needs_action=1, why_not_invoiced=order_stock.STOCK_READY, stock_check=order_stock.IN_STOCK)
		self.assertTrue(sot.wanted(row, frappe._dict(needs_action=1)))
		self.assertTrue(sot.wanted(row, frappe._dict(needs_action=0)))
		self.assertFalse(sot.wanted(frappe._dict(row, needs_action=0), frappe._dict(needs_action=1)))

	def test_a_card_counts_the_orders_to_act_on(self):
		def r(action):
			return frappe._dict(tracking_state=sot.CONVERTED, close_check=sot.KEEP_OPEN, outstanding_amount=0, why_not_invoiced="x",
				stock_there=0, needs_action=action)
		by_label = {card["label"]: card["value"] for card in sot.get_summary([r(1), r(1), r(0)], "BHD")}
		self.assertEqual(by_label["Needs Action Now"], 2)

	def test_the_reason_the_step_and_the_warehouse_are_the_first_columns_after_the_order_and_customer(self):
		names = [c["fieldname"] for c in sot.get_columns()]
		self.assertEqual(names[:6], ["sales_order", "customer_name", "why_not_invoiced", "next_step", "stock_check", "warehouses"])
		self.assertEqual(len(names), len(set(names)), "a column is listed twice")

	def test_item_wise_has_the_step_beside_the_reason(self):
		names = [c["fieldname"] for c in sot.get_item_columns()]
		self.assertEqual(names.index("next_step"), names.index("why_not_invoiced") + 1)
		self.assertEqual(len(names), len(set(names)))
