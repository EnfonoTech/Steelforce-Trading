"""Why an open Sales Order has no invoice, and what the stock says (sf_trading/order_stock.py).

    bench --site <site> run-tests --module sf_trading.tests.test_order_stock
"""

from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import add_days, getdate, nowdate

from sf_trading import order_stock as stock

TODAY = getdate(nowdate())


def order(**values):
	base = dict(name="SO-1", status="To Deliver and Bill", per_billed=0, delivery_date=TODAY, customer="C",
		customer_name="C", custom_payment_mode="", transaction_date=add_days(TODAY, -3))
	base.update(values)
	return frappe._dict(base)


def line(item="ITEM-A", pending=10, actual=100, warehouse="SFSB - SFB", tracked=True, elsewhere=None, name=None):
	row = frappe._dict(item_code=item, item_name=item, item_group="G", stock_uom="Nos", pending=pending,
		pending_stock=pending, actual=actual, reserved=0, warehouse=warehouse, tracked=tracked,
		elsewhere=elsewhere or {}, name=name or item + "-row", parent="SO-1")
	row.state = stock.line_state(pending, actual) if tracked else ""
	return row


class TestLineState(FrappeTestCase):
	def test_the_quantity_is_there(self):
		self.assertEqual(stock.line_state(10, 10), stock.IN_STOCK)
		self.assertEqual(stock.line_state(10, 250), stock.IN_STOCK)

	def test_part_of_it_is_there(self):
		self.assertEqual(stock.line_state(40, 22), stock.PART_STOCK)

	def test_none_of_it_is_there(self):
		self.assertEqual(stock.line_state(10, 0), stock.NO_STOCK)
		self.assertEqual(stock.line_state(10, -4), stock.NO_STOCK)

	def test_nothing_to_deliver_has_no_state(self):
		self.assertEqual(stock.line_state(0, 50), "")
		self.assertEqual(stock.line_state(None, 50), "")

	def test_a_rounding_sliver_does_not_make_it_part_stock(self):
		self.assertEqual(stock.line_state(10, 9.9996), stock.IN_STOCK)


class TestStockCheck(FrappeTestCase):
	def test_every_line_in_stock(self):
		self.assertEqual(stock.stock_check([stock.IN_STOCK, stock.IN_STOCK]), stock.IN_STOCK)

	def test_no_line_in_stock(self):
		self.assertEqual(stock.stock_check([stock.NO_STOCK, stock.NO_STOCK]), stock.NO_STOCK)

	def test_anything_between_is_part_stock(self):
		self.assertEqual(stock.stock_check([stock.IN_STOCK, stock.NO_STOCK]), stock.PART_STOCK)
		self.assertEqual(stock.stock_check([stock.PART_STOCK, stock.PART_STOCK]), stock.PART_STOCK)
		self.assertEqual(stock.stock_check([stock.NO_STOCK, stock.PART_STOCK]), stock.PART_STOCK)

	def test_no_state_to_judge(self):
		self.assertEqual(stock.stock_check([]), "")
		self.assertEqual(stock.stock_check(["", ""]), "")


class TestQuantity(FrappeTestCase):
	def test_as_people_write_it(self):
		self.assertEqual(stock.qty(22.0), "22")
		self.assertEqual(stock.qty(0.5), "0.5")
		self.assertEqual(stock.qty(38.175), "38.175")
		self.assertEqual(stock.qty(100), "100")
		self.assertEqual(stock.qty(0), "0")
		self.assertEqual(stock.qty(None), "0")


class TestTheNote(FrappeTestCase):
	def test_a_short_line_says_how_much_of_what_is_where(self):
		note = stock.stock_note([line("FLAT BAR", pending=40, actual=22)])
		self.assertEqual(note, "FLAT BAR: 22 of 40 at SFSB - SFB")

	def test_where_the_rest_sits_is_named(self):
		note = stock.stock_note([line("FLAT BAR", pending=40, actual=22, elsewhere={"SFSS - SFB": 31, "SFWH - SFB": 4})])
		self.assertEqual(note, "FLAT BAR: 22 of 40 at SFSB - SFB (also 31 in SFSS - SFB, 4 in SFWH - SFB)")

	def test_a_line_in_stock_is_not_in_the_note(self):
		self.assertEqual(stock.stock_note([line(pending=5, actual=50)]), "")

	def test_a_service_line_is_not_in_the_note(self):
		self.assertEqual(stock.stock_note([line("SERVICE", pending=1, actual=0, tracked=False)]), "")

	def test_only_the_first_few_lines_are_spelled_out(self):
		lines = [line("I%s" % n, pending=10, actual=0) for n in range(5)]
		note = stock.stock_note(lines)
		self.assertEqual(note.count("of 10 at"), stock.NOTE_LINES)
		self.assertTrue(note.endswith("and 2 more line(s)"))


class TestNeedsAReason(FrappeTestCase):
	def test_an_order_with_something_left_to_invoice(self):
		self.assertTrue(stock.needs_reason(order(per_billed=0)))
		self.assertTrue(stock.needs_reason(order(per_billed=40)))
		self.assertTrue(stock.needs_reason(order(status="On Hold")))

	def test_a_billed_or_finished_order_does_not(self):
		self.assertFalse(stock.needs_reason(order(per_billed=100)))
		self.assertFalse(stock.needs_reason(order(per_billed=99.999)))
		for status in ("Closed", "Completed", "Cancelled", "Draft"):
			self.assertFalse(stock.needs_reason(order(status=status)), status)


class TestWhyNotInvoiced(FrappeTestCase):
	def setUp(self):
		# a date is written the way the site writes dates; the tests only need it to be there
		formatted = patch.object(stock.frappe, "format_value", side_effect=lambda value, *args, **kwargs: str(value))
		formatted.start()
		self.addCleanup(formatted.stop)

	def why(self, order_=None, **facts):
		return stock.why_not_invoiced(order_ or order(), TODAY, **facts)

	def test_stock_there_and_nothing_raised_is_the_one_to_chase(self):
		reason, detail = self.why(lines=[line(pending=10, actual=100), line("B", pending=2, actual=2)])
		self.assertEqual(reason, stock.STOCK_READY)
		self.assertIn("nothing has been raised", detail)

	def test_an_order_with_no_stock_line_is_simply_not_invoiced(self):
		reason, _detail = self.why(lines=[line("SERVICE", pending=1, actual=0, tracked=False)])
		self.assertEqual(reason, stock.NOT_INVOICED)

	def test_a_billed_in_full_or_finished_order_has_no_reason(self):
		self.assertEqual(self.why(order(per_billed=100), lines=[line()]), ("", ""))
		self.assertEqual(self.why(order(status="Completed"), lines=[line()]), ("", ""))

	def test_a_part_billed_order_still_has_one(self):
		reason, _detail = self.why(order(per_billed=40), lines=[line()])
		self.assertEqual(reason, stock.STOCK_READY)

	def test_an_invoice_saved_and_never_submitted(self):
		reason, detail = self.why(lines=[line()], draft_invoices={"20010008906"})
		self.assertEqual(reason, stock.DRAFT_INVOICE)
		self.assertIn("20010008906", detail)

	def test_a_delivery_note_saved_and_never_submitted(self):
		reason, detail = self.why(lines=[line()], draft_notes=["MAT-DN-2026-00427"])
		self.assertEqual(reason, stock.DRAFT_DELIVERY)
		self.assertIn("MAT-DN-2026-00427", detail)

	def test_a_draft_invoice_comes_before_the_stock_but_the_stock_is_in_the_detail(self):
		reason, detail = self.why(lines=[line("FLAT BAR", pending=40, actual=22)], draft_invoices=["SI-1"])
		self.assertEqual(reason, stock.DRAFT_INVOICE)
		self.assertIn("FLAT BAR: 22 of 40", detail)

	def test_no_stock(self):
		reason, detail = self.why(lines=[line("CHANNEL", pending=4, actual=0)])
		self.assertEqual(reason, stock.NO_STOCK)
		self.assertIn("CHANNEL: 0 of 4 at SFSB - SFB", detail)

	def test_part_stock(self):
		reason, _detail = self.why(lines=[line("A", pending=10, actual=100), line("B", pending=10, actual=3)])
		self.assertEqual(reason, stock.PART_STOCK)

	def test_the_customer_refused_with_the_stock_there(self):
		reason, detail = self.why(lines=[line()], blocks=["Customer is not approved for invoicing (Pending Verification)"])
		self.assertEqual(reason, stock.CUSTOMER_BLOCKED)
		self.assertIn("Pending Verification", detail)

	def test_the_stock_comes_before_the_customer_and_both_are_told(self):
		reason, detail = self.why(lines=[line(pending=5, actual=0)], blocks=["Customer has no phone number"])
		self.assertEqual(reason, stock.NO_STOCK)
		self.assertIn("Customer has no phone number", detail)

	def test_a_delivery_date_that_has_not_come(self):
		reason, detail = self.why(order(delivery_date=add_days(TODAY, 5)), lines=[line()])
		self.assertEqual(reason, stock.NOT_DUE)
		self.assertIn("has not come", detail)

	def test_an_order_on_hold(self):
		reason, _detail = self.why(order(status="On Hold"), lines=[line()], draft_invoices=["SI-1"])
		self.assertEqual(reason, stock.ON_HOLD)

	def test_goods_delivered_and_never_billed(self):
		reason, detail = self.why(lines=[], delivered_notes=["MAT-DN-1", "MAT-DN-2"])
		self.assertEqual(reason, stock.DELIVERED_NOT_BILLED)
		self.assertIn("MAT-DN-1, MAT-DN-2", detail)

	def test_delivered_goods_are_not_a_stock_question(self):
		reason, _detail = self.why(lines=[], delivered_notes=["MAT-DN-1"], blocks=[])
		self.assertNotIn(reason, (stock.NO_STOCK, stock.PART_STOCK))

	def test_every_fact_is_in_the_detail_in_priority_order(self):
		reason, detail = self.why(order(delivery_date=add_days(TODAY, 2)), lines=[line("X", pending=9, actual=1)],
			draft_invoices=["SI-1"], draft_notes=["DN-1"], blocks=["Customer is frozen"])
		self.assertEqual(reason, stock.DRAFT_INVOICE)
		positions = [detail.index(text) for text in ("SI-1", "DN-1", "has not come", "X: 1 of 9", "Customer is frozen")]
		self.assertEqual(positions, sorted(positions))

	def test_every_reason_is_one_the_report_filters_by(self):
		self.assertEqual(len(set(stock.REASONS)), len(stock.REASONS))
		for reason in (stock.STOCK_READY, stock.DRAFT_INVOICE, stock.DRAFT_DELIVERY, stock.NO_STOCK, stock.PART_STOCK,
				stock.CUSTOMER_BLOCKED, stock.DELIVERED_NOT_BILLED, stock.NOT_DUE, stock.ON_HOLD, stock.NOT_INVOICED):
			self.assertIn(reason, stock.REASONS)


class TestTheSequence(FrappeTestCase):
	def sequence(self, actual, *pending):
		return stock.demand_in_sequence(actual, [{"pending": p} for p in pending])

	def test_the_oldest_order_is_served_first(self):
		lines = self.sequence(15, 3, 10, 5, 1)
		self.assertEqual([line["stock_after"] for line in lines], [12, 2, -3, -4])
		self.assertEqual([line["covered"] for line in lines], ["Covered", "Covered", "Part", "Not Covered"])
		self.assertEqual([line["stock_before"] for line in lines], [15, 12, 2, -3])

	def test_an_exact_fit_is_covered(self):
		self.assertEqual(self.sequence(10, 10)[0]["covered"], "Covered")

	def test_no_stock_covers_nothing(self):
		self.assertEqual([line["covered"] for line in self.sequence(0, 1, 2)], ["Not Covered", "Not Covered"])

	def test_negative_stock_covers_nothing(self):
		self.assertEqual(self.sequence(-3, 1)[0]["covered"], "Not Covered")


class TestTheFacts(FrappeTestCase):
	"""What is read from the database, on canned rows."""

	def test_pending_lines_skip_what_is_delivered_or_shipped_by_the_supplier(self):
		rows = [
			frappe._dict(name="r1", parent="SO-1", idx=1, item_code="A", qty=10, delivered_qty=0, conversion_factor=1,
				warehouse="W", delivered_by_supplier=0),
			frappe._dict(name="r2", parent="SO-1", idx=2, item_code="B", qty=10, delivered_qty=10, conversion_factor=1,
				warehouse="W", delivered_by_supplier=0),
			frappe._dict(name="r3", parent="SO-1", idx=3, item_code="C", qty=5, delivered_qty=0, conversion_factor=1,
				warehouse="W", delivered_by_supplier=1),
			frappe._dict(name="r4", parent="SO-2", idx=1, item_code="D", qty=3, delivered_qty=1, conversion_factor=12,
				warehouse="W", delivered_by_supplier=0),
		]
		with patch.object(stock.frappe, "get_all", return_value=rows):
			lines = stock.pending_lines(["SO-1", "SO-2"])
		self.assertEqual([line.item_code for line in lines["SO-1"]], ["A"])
		self.assertEqual(lines["SO-1"][0].pending_stock, 10)
		# 2 boxes still to deliver, 12 pieces to a box
		self.assertEqual((lines["SO-2"][0].pending, lines["SO-2"][0].pending_stock), (2, 24))

	def test_annotate_puts_the_stock_on_each_line(self):
		lines = {"SO-1": [
			frappe._dict(item_code="A", warehouse="SFSB", pending_stock=40),
			frappe._dict(item_code="B", warehouse="SFSB", pending_stock=3),
			frappe._dict(item_code="SERVICE", warehouse="SFSB", pending_stock=1),
		]}
		by_pair = {("A", "SFSB"): frappe._dict(actual=22, reserved=40), ("B", "SFSB"): frappe._dict(actual=3, reserved=3)}
		by_item = {"A": [("SFSB", 22), ("SFSS", 31), ("SFWH", 0), ("OTHER CO", 9)], "B": [("SFSB", 3)]}
		stock.annotate(lines, by_pair, by_item, tracked={"A", "B"}, warehouses={"SFSB", "SFSS", "SFWH"})
		a, b, service = lines["SO-1"]
		self.assertEqual((a.actual, a.reserved, a.state), (22, 40, stock.PART_STOCK))
		# the other warehouses of the company that hold some, and nothing else
		self.assertEqual(a.elsewhere, {"SFSS": 31})
		self.assertEqual((b.state, b.elsewhere), (stock.IN_STOCK, {}))
		self.assertEqual((service.tracked, service.state), (False, ""))

	def test_a_line_with_no_bin_has_no_stock(self):
		lines = {"SO-1": [frappe._dict(item_code="A", warehouse="SFSB", pending_stock=5)]}
		stock.annotate(lines, {}, {}, tracked={"A"}, warehouses={"SFSB"})
		self.assertEqual((lines["SO-1"][0].actual, lines["SO-1"][0].state), (0.0, stock.NO_STOCK))

	def test_the_drafts_of_an_order(self):
		invoices = [frappe._dict(so="SO-1", name="SI-D1"), frappe._dict(so="SO-1", name="SI-D2")]
		notes = [frappe._dict(so="SO-2", name="DN-D1")]
		with patch.object(stock.frappe.db, "sql", side_effect=[invoices, notes]):
			drafts_si, drafts_dn = stock.drafts_of(["SO-1", "SO-2"])
		self.assertEqual(drafts_si["SO-1"], {"SI-D1", "SI-D2"})
		self.assertEqual(drafts_dn["SO-2"], {"DN-D1"})
		self.assertNotIn("SO-2", drafts_si)

	def test_the_delivery_notes_that_were_submitted(self):
		with patch.object(stock.frappe.db, "sql", return_value=[frappe._dict(so="SO-1", name="DN-1")]):
			self.assertEqual(stock.submitted_delivery_notes(["SO-1"])["SO-1"], {"DN-1"})


class TestTheCustomerGates(FrappeTestCase):
	def blocks(self, orders, problems=None, customer_row=(0, 0), error=None):
		asked = []

		def party_problems(doctype, header):
			asked.append((doctype, dict(header)))
			if error:
				raise error
			return list(problems or [])

		with patch("sf_trading.party_alert.party_problems", side_effect=party_problems), \
				patch.object(stock.frappe.db, "get_value", return_value=customer_row), patch.object(stock.frappe, "logger"):
			return stock.customer_blocks(orders, "Steel Force Trading WLL"), asked

	def test_the_sales_invoice_gates_are_asked_and_their_words_cleaned(self):
		out, asked = self.blocks([order()], problems=["<strong>Acme</strong> is not yet approved for invoicing"])
		self.assertEqual(out[("C", "")], ["Acme is not yet approved for invoicing"])
		self.assertEqual(asked[0][0], "Sales Invoice")

	def test_a_blank_payment_mode_is_asked_as_cash(self):
		_out, asked = self.blocks([order(custom_payment_mode="")])
		self.assertEqual(asked[0][1]["custom_payment_mode"], "Cash")

	def test_a_payment_mode_that_is_set_is_asked_as_it_is(self):
		_out, asked = self.blocks([order(custom_payment_mode="Credit")])
		self.assertEqual(asked[0][1]["custom_payment_mode"], "Credit")

	def test_the_header_is_a_new_voucher_of_the_customer_and_company(self):
		_out, asked = self.blocks([order(customer="ACME")])
		header = asked[0][1]
		self.assertEqual((header["customer"], header["company"], header["__islocal"]), ("ACME", "Steel Force Trading WLL", 1))

	def test_a_customer_is_asked_once_per_payment_mode(self):
		_out, asked = self.blocks([order(name="A"), order(name="B"), order(name="C", custom_payment_mode="Credit")])
		self.assertEqual(len(asked), 2)

	def test_a_disabled_or_frozen_customer(self):
		out, _asked = self.blocks([order()], customer_row=(1, 1))
		self.assertEqual(out[("C", "")], ["Customer is disabled", "Customer is frozen"])

	def test_a_gate_that_cannot_be_asked_does_not_stop_the_report(self):
		out, _asked = self.blocks([order()], error=RuntimeError("boom"))
		self.assertEqual(out[("C", "")], [])


class TestOneOrder(FrappeTestCase):
	def facts(self, **kw):
		base = dict(lines={}, draft_invoices={}, draft_notes={}, delivered_notes={}, blocks={})
		base.update(kw)
		return frappe._dict(base)

	def test_the_reason_the_detail_and_the_stock_check(self):
		facts = self.facts(lines={"SO-1": [line(pending=4, actual=0)]}, blocks={("C", ""): ["x"]})
		reason, detail, check = stock.reason_for(order(), facts, TODAY)
		self.assertEqual((reason, check), (stock.NO_STOCK, stock.NO_STOCK))
		self.assertIn("x", detail)

	def test_an_order_with_no_facts_is_not_invoiced_yet(self):
		reason, _detail, check = stock.reason_for(order(), self.facts(), TODAY)
		self.assertEqual((reason, check), (stock.NOT_INVOICED, ""))

	def test_the_blocks_are_the_customers_for_the_orders_payment_mode(self):
		facts = self.facts(lines={"SO-1": [line()]}, blocks={("C", "Credit"): ["no credit documents"], ("C", ""): []})
		self.assertEqual(stock.reason_for(order(custom_payment_mode="Credit"), facts, TODAY)[0], stock.CUSTOMER_BLOCKED)
		self.assertEqual(stock.reason_for(order(custom_payment_mode=""), facts, TODAY)[0], stock.STOCK_READY)


class TestNextStep(FrappeTestCase):
	def test_every_reason_says_what_to_do(self):
		for reason in stock.REASONS:
			self.assertTrue(stock.next_step(reason, [line(pending=4, actual=1)]), reason)

	def test_the_rows_somebody_invoices_say_so(self):
		self.assertIn("Raise the Sales Invoice", stock.next_step(stock.STOCK_READY))
		self.assertIn("submit", stock.next_step(stock.DRAFT_INVOICE))
		self.assertIn("Delivery Note", stock.next_step(stock.DRAFT_DELIVERY))
		self.assertIn("already delivered", stock.next_step(stock.DELIVERED_NOT_BILLED))
		self.assertIn("customer", stock.next_step(stock.CUSTOMER_BLOCKED).lower())

	def test_no_stock_names_where_the_rest_sits_when_it_sits_anywhere(self):
		short = line("FLAT BAR", pending=40, actual=0, elsewhere={"SFWH - SFB": 82, "SFSS - SFB": 4})
		self.assertEqual(stock.next_step(stock.NO_STOCK, [short]), "Transfer the stock from SFWH - SFB (82 there) or buy it")

	def test_no_stock_with_nothing_anywhere_says_to_get_it(self):
		self.assertEqual(stock.next_step(stock.NO_STOCK, [line(pending=5, actual=0)]), "Get the stock: buy it or transfer it in")

	def test_part_stock_with_nothing_elsewhere(self):
		self.assertIn("Invoice the part that is in stock", stock.next_step(stock.PART_STOCK, [line(pending=5, actual=2)]))

	def test_a_line_that_is_in_stock_is_not_where_the_transfer_comes_from(self):
		fine = line("A", pending=1, actual=9, elsewhere={"SFWH - SFB": 50})
		short = line("B", pending=9, actual=1, elsewhere={"SFSS - SFB": 20})
		self.assertEqual(stock.next_step(stock.PART_STOCK, [fine, short]), "Transfer the stock from SFSS - SFB (20 there) or buy it")

	def test_no_reason_no_step(self):
		self.assertEqual(stock.next_step("", []), "")
		self.assertEqual(stock.next_step("Something Else"), "")


class TestWhoHasToMove(FrappeTestCase):
	def test_what_somebody_acts_on_today(self):
		for reason in (stock.STOCK_READY, stock.DRAFT_INVOICE, stock.DRAFT_DELIVERY, stock.CUSTOMER_BLOCKED,
				stock.DELIVERED_NOT_BILLED, stock.NOT_INVOICED):
			self.assertIn(reason, stock.ACTIONABLE, reason)

	def test_what_waits_on_purpose_or_on_the_warehouse_is_not(self):
		for reason in (stock.NO_STOCK, stock.PART_STOCK, stock.NOT_DUE, stock.ON_HOLD):
			self.assertNotIn(reason, stock.ACTIONABLE, reason)

	def test_the_warehouses_of_an_order(self):
		lines = [line("A", warehouse="SFSS - SFB"), line("B", warehouse="SFSB - SFB"), line("C", warehouse="SFSS - SFB")]
		self.assertEqual(stock.warehouses_of(lines), "SFSB - SFB, SFSS - SFB")
		self.assertEqual(stock.warehouses_of([]), "")
