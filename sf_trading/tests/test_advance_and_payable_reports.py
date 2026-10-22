"""Tests for the Supplier and Customer Advance Report and the Branch-wise Payable Outstanding report.

These exercise the rules, not the database: how an advance is classed, what a filter keeps, how a
payable ages and rolls up. Read-only against a real site is covered by running the reports themselves.

    bench --site <scratch-site> run-tests --module sf_trading.tests.test_advance_and_payable_reports
"""

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import add_days, today

from sf_trading.sf_trading.report.branch_wise_payable_outstanding import branch_wise_payable_outstanding as pay
from sf_trading.sf_trading.report.supplier_and_customer_advance_report import supplier_and_customer_advance_report as adv


def entry(**kw):
	base = dict(
		kind=adv.KIND_ORDER, party_type="Supplier", party="S1", party_name="Supplier One", voucher_type="Payment Entry",
		voucher="PAY-1", date=add_days(today(), -10), mode_of_payment="Cash", reference_no=None, branch="SFSB",
		party_currency="BHD", order_type="Purchase Order", order_no="PO-1", order_date=add_days(today(), -20),
		order_status="To Receive and Bill", order_amount=1000.0, per_billed=0.0, progress=0.0, advance=400.0,
		adjusted=0.0, invoices="", why="",
	)
	base.update(kw)
	return frappe._dict(base)


class TestAdvanceClassing(FrappeTestCase):
	def test_order_advance_with_nothing_adjusted_is_unapplied(self):
		e = entry()
		adv.finish(e)
		self.assertEqual((e.status, e.unapplied, e.flagged), (adv.ST_UNAPPLIED, 400.0, False))
		self.assertEqual(e.supporting, "PO-1")

	def test_part_and_full_adjustment(self):
		part = entry(adjusted=150.0)
		adv.finish(part)
		self.assertEqual((part.status, part.unapplied), (adv.ST_PART, 250.0))
		full = entry(adjusted=400.0)
		adv.finish(full)
		self.assertEqual((full.status, full.unapplied), (adv.ST_ADJUSTED, 0.0))

	def test_payment_on_account_has_no_supporting_voucher(self):
		e = entry(kind=adv.KIND_ACCOUNT, order_type=None, order_no=None, order_date=None, order_amount=None)
		adv.finish(e)
		self.assertEqual((e.status, e.flagged, e.unapplied), (adv.ST_NO_VOUCHER, True, 400.0))
		self.assertEqual(e.supporting, frappe._("NONE"))

	def test_order_field_only_is_flagged_and_explained(self):
		e = entry(kind=adv.KIND_FIELD, voucher=None, voucher_type=None, date=None)
		adv.finish(e)
		self.assertEqual(e.status, adv.ST_NO_VOUCHER)
		self.assertIn("advance ledger", e.why)

	def test_fully_billed_order_with_unapplied_advance_is_called_out(self):
		e = entry(per_billed=100.0)
		adv.finish(e)
		self.assertIn("fully billed", e.why)
		taken = entry(per_billed=100.0, adjusted=400.0)
		adv.finish(taken)
		self.assertEqual(taken.why, "")

	def test_closed_order_with_unapplied_advance_is_called_out(self):
		e = entry(order_status="Closed")
		adv.finish(e)
		self.assertIn("closed", e.why)

	def test_age_counts_from_the_advance_date(self):
		e = entry(date=add_days(today(), -10))
		adv.finish(e)
		self.assertEqual(e.age, 10)


class TestAdvanceFilters(FrappeTestCase):
	def keep(self, e, **filters):
		return adv.keep(e, frappe._dict(filters), {})

	def test_side_and_party(self):
		e = entry()
		self.assertTrue(self.keep(e, party_type="Supplier"))
		self.assertFalse(self.keep(e, party_type="Customer"))
		self.assertTrue(self.keep(e, supplier="S1"))
		self.assertFalse(self.keep(e, supplier="S2"))
		# a supplier filter does not hide customers
		c = entry(party_type="Customer", party="C1")
		self.assertTrue(self.keep(c, supplier="S2"))

	def test_branch_and_mode(self):
		e = entry()
		self.assertTrue(self.keep(e, branch=["SFSB", "SFSS"]))
		self.assertFalse(self.keep(e, branch=["SFSS"]))
		self.assertFalse(self.keep(e, mode_of_payment="Bank"))

	def test_advance_and_order_dates(self):
		e = entry()
		self.assertTrue(self.keep(e, from_date=add_days(today(), -15), to_date=today()))
		self.assertFalse(self.keep(e, from_date=add_days(today(), -5)))
		self.assertFalse(self.keep(e, order_to_date=add_days(today(), -30)))

	def test_order_number_matches_any_part(self):
		self.assertTrue(self.keep(entry(), order_no="po-1"))
		self.assertFalse(self.keep(entry(), order_no="SO-9"))
		# an entry that names no order cannot match an order number
		self.assertFalse(self.keep(entry(order_no=None), order_no="PO"))

	def test_user_permissions_hide_other_parties_and_branches(self):
		e = entry()
		self.assertFalse(adv.keep(e, frappe._dict(), {"Supplier": {"S2"}}))
		self.assertFalse(adv.keep(e, frappe._dict(), {"Branch": {"SFSS"}}))
		self.assertTrue(adv.keep(entry(branch=adv.NO_BRANCH), frappe._dict(), {"Branch": {"SFSS"}}))

	def test_status_and_age_filters(self):
		e = entry(date=add_days(today(), -90))
		adv.finish(e)
		self.assertTrue(adv.wanted_status(e, frappe._dict(status="Unapplied")))
		self.assertFalse(adv.wanted_status(e, frappe._dict(status="Adjusted")))
		self.assertTrue(adv.old_enough(e, frappe._dict(older_than_days=60)))
		self.assertFalse(adv.old_enough(e, frappe._dict(older_than_days=120)))
		done = entry(adjusted=400.0, date=add_days(today(), -90))
		adv.finish(done)
		self.assertFalse(adv.old_enough(done, frappe._dict(older_than_days=60)))


class TestAdvanceViews(FrappeTestCase):
	def entries(self):
		a = entry(voucher="PAY-1", advance=400.0, adjusted=400.0)
		b = entry(voucher="PAY-2", advance=100.0, adjusted=0.0, date=add_days(today(), -3))
		c = entry(kind=adv.KIND_ACCOUNT, voucher="PAY-3", order_type=None, order_no=None, order_date=None, advance=50.0)
		d = entry(party_type="Customer", party="C1", party_name="Customer One", order_type="Sales Order", order_no="SO-1",
		          voucher="PAY-4", advance=200.0)
		for e in (a, b, c, d):
			adv.finish(e)
		return [a, b, c, d]

	def test_order_summary_adds_payments_per_order(self):
		rows = {r["order_no"]: r for r in adv.order_rows(self.entries(), "BHD")}
		self.assertEqual(set(rows), {"PO-1", "SO-1"})
		self.assertEqual((rows["PO-1"]["advance"], rows["PO-1"]["adjusted"], rows["PO-1"]["unapplied"]), (500.0, 400.0, 100.0))
		self.assertEqual(rows["PO-1"]["payments"], 2)
		self.assertEqual(rows["PO-1"]["status"], adv.ST_PART)

	def test_party_summary_counts_the_voucherless_money(self):
		rows = {r["party"]: r for r in adv.party_rows(self.entries(), "BHD")}
		self.assertEqual(rows["S1"]["no_voucher"], 50.0)
		self.assertEqual(rows["S1"]["no_voucher_entries"], 1)
		self.assertEqual(rows["S1"]["flagged"], 1)
		self.assertEqual(rows["C1"]["flagged"], 0)

	def test_cards_split_by_side(self):
		cards = {c["label"]: c["value"] for c in adv.get_summary(self.entries(), "BHD")}
		self.assertEqual(cards["Supplier Advances"], 550.0)
		self.assertEqual(cards["Customer Advances"], 200.0)
		self.assertEqual(cards["No Supporting Voucher"], 50.0)
		self.assertEqual(cards["Entries Without Voucher"], 1)

	def test_rows_carry_the_flag_for_the_highlight(self):
		rows = adv.entry_rows(self.entries(), "BHD")
		self.assertEqual(sorted(r["flagged"] for r in rows), [0, 0, 0, 1])

	def test_company_currency_conversion(self):
		pe = frappe._dict(received_amount=1000.0, base_received_amount=102.5, paid_amount=102.5, base_paid_amount=102.5)
		self.assertEqual(adv.to_company(200.0, pe, "Supplier", "SAR", "BHD"), 20.5)
		self.assertEqual(adv.to_company(200.0, pe, "Supplier", "BHD", "BHD"), 200.0)


class TestPayableAgeing(FrappeTestCase):
	def test_ranges(self):
		self.assertEqual(pay.parse_ranges("30, 60, 90"), [30, 60, 90])
		self.assertEqual(pay.parse_ranges("nonsense"), [30, 60, 90, 120])
		self.assertEqual(pay.bucket_labels([30, 60]), ["0-30", "31-60", "61+"])

	def doc(self, outstanding, due_in_days, posted_days_ago=40):
		return frappe._dict(
			outstanding=outstanding, posting_date=add_days(today(), -posted_days_ago),
			due_date=add_days(today(), due_in_days),
		)

	def test_buckets_on_due_date(self):
		now = frappe.utils.getdate(today())
		overdue = self.doc(100.0, -45)
		pay.age_doc(overdue, now, [30, 60], "Due Date")
		self.assertEqual((overdue.age, overdue.bucket), (45, "range2"))
		future = self.doc(100.0, 10)
		pay.age_doc(future, now, [30, 60], "Due Date")
		self.assertEqual(future.bucket, "not_due")
		advance = self.doc(-50.0, -45)
		pay.age_doc(advance, now, [30, 60], "Due Date")
		self.assertIsNone(advance.bucket)

	def test_roll_up_and_status(self):
		now = frappe.utils.getdate(today())
		docs = [self.doc(100.0, -45), self.doc(40.0, 10), self.doc(-30.0, -5)]
		total = pay.blank_amounts()
		for d in docs:
			pay.age_doc(d, now, [30, 60], "Due Date")
			pay.add_document(total, d)
		self.assertEqual(round(total.outstanding, 3), 110.0)
		self.assertEqual(round(total.invoiced, 3), 140.0)
		self.assertEqual(round(total.unallocated, 3), -30.0)
		self.assertEqual(round(total.overdue, 3), 100.0)
		self.assertEqual(total.open_documents, 2)
		self.assertEqual(pay.status_of(total), pay.STATUS_OVERDUE)

	def test_status_without_overdue(self):
		a = pay.blank_amounts()
		a.invoiced = 50.0
		self.assertEqual(pay.status_of(a), pay.STATUS_NOT_DUE)
		b = pay.blank_amounts()
		b.unallocated = -20.0
		self.assertEqual(pay.status_of(b), pay.STATUS_ADVANCE)
		self.assertEqual(pay.status_of(pay.blank_amounts()), pay.STATUS_SETTLED)


class TestPayableAssembly(FrappeTestCase):
	def test_suppliers_roll_up_their_branches_and_orders(self):
		now = frappe.utils.getdate(today())
		docs = [
			frappe._dict(supplier="S1", branch="SFSB", outstanding=100.0, posting_date=add_days(today(), -50), due_date=add_days(today(), -40)),
			frappe._dict(supplier="S1", branch="SFSS", outstanding=60.0, posting_date=add_days(today(), -5), due_date=add_days(today(), 25)),
		]
		for d in docs:
			pay.age_doc(d, now, [30, 60], "Due Date")
		info = {"S1": frappe._dict(supplier_name="Supplier One", supplier_group="Local", payment_terms=None, origin="Local", country="Bahrain", disabled=0)}
		built = pay.build_suppliers(docs, {("S1", "SFSB"): 25.0}, info, frappe._dict())
		self.assertEqual(len(built), 1)
		s = built[0]
		self.assertEqual((round(s.outstanding, 3), round(s.overdue, 3), round(s.orders, 3), round(s.exposure, 3)), (160.0, 100.0, 25.0, 185.0))
		self.assertEqual([b.branch for b in s.branches], ["SFSB", "SFSS"])
		rows = pay.tree_rows(built, "BHD")
		self.assertEqual([r["indent"] for r in rows], [0, 1, 1])
		branch_rows = {r["branch"]: r for r in pay.branch_rows(built, "BHD")}
		self.assertEqual(branch_rows["SFSB"]["overdue"], 100.0)
		self.assertEqual(branch_rows["SFSS"]["overdue"], 0.0)

	def test_filters(self):
		a = pay.blank_amounts()
		a.update(invoiced=500.0, exposure=500.0, unallocated=0.0, status=pay.STATUS_OVERDUE)
		self.assertTrue(pay.wanted(a, frappe._dict(status="Overdue")))
		self.assertFalse(pay.wanted(a, frappe._dict(status="Advance Held")))
		self.assertFalse(pay.wanted(a, frappe._dict(min_outstanding=1000)))
		empty = pay.blank_amounts()
		empty.update(exposure=0.0, status=pay.STATUS_SETTLED)
		self.assertFalse(pay.wanted(empty, frappe._dict()))
		self.assertTrue(pay.wanted(empty, frappe._dict(show_zero=1)))

	def test_user_permissions(self):
		row = frappe._dict(supplier="S1", branch="SFSB")
		self.assertFalse(pay.is_permitted(row, {"Supplier": {"S2"}}))
		self.assertFalse(pay.is_permitted(row, {"Branch": {"SFSS"}}))
		self.assertTrue(pay.is_permitted(frappe._dict(supplier="S1", branch=pay.NO_BRANCH), {"Branch": {"SFSS"}}))
