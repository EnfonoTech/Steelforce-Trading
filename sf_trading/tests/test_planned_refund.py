"""Tests for the planned-refund guard, and for what it measures the refund against.

The decision under test is `refundable_amount`: a return only owes the customer money to the
extent that money was ever taken on the invoice it returns. Production 20010004598 (436.700,
mode Cash) was refused entry to the approval chain for a refund that could not exist — its
original 20010004589 had paid_amount 0, no Payment Entry and no POS payment row.

    bench --site <scratch-site> run-tests --module sf_trading.tests.test_planned_refund
"""

from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase

from sf_trading import planned_payment


class TestRefundableAmount(FrappeTestCase):
	"""Plain dicts: what is under test is the arithmetic, not a document."""

	def refundable(self, collected, **fields):
		doc = frappe._dict(fields)
		with patch.object(planned_payment, "collected_against", return_value=collected):
			return planned_payment.refundable_amount(doc)

	def test_nothing_collected_means_nothing_refundable(self):
		self.assertEqual(
			self.refundable(0.0, grand_total=-436.7, return_against="20010004589"), 0.0
		)

	def test_a_collected_invoice_is_refundable_up_to_the_return(self):
		self.assertEqual(
			self.refundable(436.7, grand_total=-436.7, return_against="20010004589"), 436.7
		)

	def test_a_part_paid_invoice_caps_the_refund_at_what_was_paid(self):
		# 436.700 returned, only 200 ever taken — 200 is all that can go back
		self.assertEqual(
			self.refundable(200.0, grand_total=-436.7, return_against="20010004589"), 200.0
		)

	def test_a_return_bigger_than_the_return_itself_is_capped_by_the_return(self):
		# the whole invoice was paid but only part of it is coming back
		self.assertEqual(
			self.refundable(436.7, grand_total=-100.0, return_against="20010004589"), 100.0
		)

	def test_a_return_naming_no_invoice_keeps_the_old_behaviour(self):
		# nothing to measure against, so it is treated as fully refundable
		self.assertEqual(self.refundable(0.0, grand_total=-50.0), 50.0)


class TestRequirePlanBeforeApproval(FrappeTestCase):
	"""The guard itself: which saves it refuses, and which it must leave alone."""

	def advice_doc(self, **fields):
		doc = frappe._dict(
			{
				"doctype": "Sales Invoice",
				"is_return": 1,
				"docstatus": 0,
				"custom_payment_mode": "Cash",
				"workflow_state": "Pending Approval",
				"currency": "BHD",
				"grand_total": -436.7,
				"return_against": "20010004589",
				"custom_planned_payments": [],
			}
		)
		doc.update(fields)
		doc.get_doc_before_save = lambda: fields.get("_before")
		return doc

	def guard(self, doc, collected=0.0):
		with patch.object(planned_payment, "collected_against", return_value=collected):
			planned_payment.require_plan_before_approval(doc)

	def test_an_uncollected_original_is_let_through(self):
		# the production case: no cash was ever taken, so there is no refund to plan
		self.guard(self.advice_doc(), collected=0.0)

	def test_a_collected_original_with_no_plan_is_refused(self):
		with self.assertRaises(frappe.ValidationError) as caught:
			self.guard(self.advice_doc(), collected=436.7)
		text = frappe.utils.strip_html(str(caught.exception))
		self.assertIn("Plan the refund", text)
		self.assertIn("436.7", text)

	def test_the_message_quotes_what_can_actually_be_refunded(self):
		# 436.700 returned, 200 collected — the counter is asked about 200, not 436.700
		with self.assertRaises(frappe.ValidationError) as caught:
			self.guard(self.advice_doc(), collected=200.0)
		text = frappe.utils.strip_html(str(caught.exception))
		self.assertIn("200", text)
		self.assertNotIn("436.7", text)

	def test_a_planned_refund_passes(self):
		doc = self.advice_doc(
			custom_planned_payments=[frappe._dict(mode_of_payment="Cash", amount=436.7)]
		)
		self.guard(doc, collected=436.7)

	def test_credit_mode_is_exempt(self):
		self.guard(self.advice_doc(custom_payment_mode="Credit"), collected=436.7)

	def test_a_save_that_is_not_entering_the_chain_is_left_alone(self):
		doc = self.advice_doc(_before=frappe._dict(workflow_state="Pending Approval"))
		self.guard(doc, collected=436.7)

	def test_a_draft_not_yet_sent_for_approval_is_left_alone(self):
		self.guard(self.advice_doc(workflow_state="Draft"), collected=436.7)

	def test_a_submitted_return_is_left_alone(self):
		self.guard(self.advice_doc(docstatus=1), collected=436.7)


class TestCollectedAgainst(FrappeTestCase):
	"""What counts as money taken, read off real documents on this site."""

	def test_an_unpaid_invoice_reads_zero(self):
		name = frappe.db.get_value(
			"Sales Invoice",
			{"docstatus": 1, "is_return": 0, "status": "Unpaid", "paid_amount": 0},
			"name",
		)
		if not name:
			self.skipTest("no unpaid Sales Invoice on this site")
		self.assertEqual(planned_payment.collected_against(name), 0.0)

	def test_an_invoice_settled_by_a_payment_entry_reads_the_allocation(self):
		row = frappe.db.sql(
			"""select r.reference_name as name, sum(r.allocated_amount) as allocated
			   from `tabPayment Entry Reference` r
			   join `tabPayment Entry` pe on pe.name = r.parent
			   join `tabSales Invoice` si on si.name = r.reference_name
			   where pe.docstatus = 1 and r.reference_doctype = 'Sales Invoice'
			     and si.is_return = 0 and si.paid_amount = 0
			   group by r.reference_name limit 1""",
			as_dict=True,
		)
		if not row:
			self.skipTest("no Sales Invoice settled by a Payment Entry on this site")
		self.assertAlmostEqual(
			planned_payment.collected_against(row[0].name), row[0].allocated, places=2
		)

	def test_a_credit_note_is_not_mistaken_for_a_collection(self):
		"""grand_total - outstanding would read a return as a payment; this must not."""
		row = frappe.db.sql(
			"""select r.return_against as name from `tabSales Invoice` r
			   join `tabSales Invoice` o on o.name = r.return_against
			   where r.is_return = 1 and r.docstatus = 1 and o.paid_amount = 0
			     and not exists (select 1 from `tabPayment Entry Reference` x
			                     join `tabPayment Entry` pe on pe.name = x.parent
			                     where pe.docstatus = 1 and x.reference_name = o.name)
			     and o.outstanding_amount < o.grand_total
			   limit 1""",
			as_dict=True,
		)
		if not row:
			self.skipTest("no netted-but-never-paid invoice on this site")
		self.assertEqual(planned_payment.collected_against(row[0].name), 0.0)
