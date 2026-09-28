# sf_trading/tests/test_sales_return_reason.py
"""Tests for the Sales Return Reason master: the seed patch and the Sales Invoice custom field.

    bench --site <scratch-site> run-tests --module sf_trading.tests.test_sales_return_reason
"""

import frappe
from frappe.tests.utils import FrappeTestCase

from sf_trading.patches.v0_1.seed_sales_return_reasons import REASONS
from sf_trading.patches.v0_1.seed_sales_return_reasons import execute as seed_sales_return_reasons
from sf_trading.sales_return_reason import OTHER_REASON, validate_return_reason


class TestSalesReturnReason(FrappeTestCase):
	def test_seed_creates_every_reason(self):
		seed_sales_return_reasons()
		for reason, _description in REASONS:
			self.assertTrue(
				frappe.db.exists("Sales Return Reason", reason), f"{reason} was not seeded"
			)

	def test_seed_is_idempotent(self):
		seed_sales_return_reasons()
		before = frappe.db.count("Sales Return Reason")
		seed_sales_return_reasons()  # running it again must not duplicate or raise
		self.assertEqual(frappe.db.count("Sales Return Reason"), before)

	def test_custom_field_is_present_on_sales_invoice(self):
		meta = frappe.get_meta("Sales Invoice")
		field = meta.get_field("custom_return_reason")
		self.assertIsNotNone(field, "custom_return_reason is missing from Sales Invoice")
		self.assertEqual(field.fieldtype, "Link")
		self.assertEqual(field.options, "Sales Return Reason")

	# ── the "Other" -> Remarks-is-mandatory rule, checked server-side ─────────
	def make_return(self, reason=None, remarks=None):
		return frappe._dict(
			doctype="Sales Invoice",
			is_return=1,
			custom_return_reason=reason,
			remarks=remarks,
		)

	def test_other_without_a_remark_is_refused(self):
		doc = self.make_return(reason=OTHER_REASON, remarks="")
		self.assertRaises(frappe.ValidationError, validate_return_reason, doc)

	def test_other_with_a_remark_is_allowed(self):
		doc = self.make_return(reason=OTHER_REASON, remarks="Customer changed their mind")
		validate_return_reason(doc)

	def test_a_named_reason_needs_no_remark(self):
		doc = self.make_return(reason="Damaged", remarks="")
		validate_return_reason(doc)

	def test_an_ordinary_invoice_is_left_alone(self):
		doc = frappe._dict(doctype="Sales Invoice", is_return=0, custom_return_reason=OTHER_REASON, remarks="")
		validate_return_reason(doc)
