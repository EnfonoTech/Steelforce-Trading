"""Tests for the rule that keeps a user permission gating dimensions, not stray link fields.

What is under test is which fields the rule claims and which it refuses to touch -- the refusals
matter more than the claims, because each one is a restriction somebody configured on purpose.

    bench --site <scratch-site> run-tests --module sf_trading.tests.test_user_permission_fields
"""

import frappe
from frappe.tests.utils import FrappeTestCase

from sf_trading import user_permission_fields as upf


class TestUserPermissionFields(FrappeTestCase):
	def claimed(self):
		return {(row.doctype, row.fieldname) for row in upf.secondary_fields()}

	def test_the_invoice_fields_that_caused_this_are_claimed(self):
		claimed = self.claimed()
		for field in ("write_off_cost_center", "loyalty_redemption_cost_center"):
			with self.subTest(field=field):
				self.assertTrue(
					("Sales Invoice", field) in claimed or upf.is_flagged("Sales Invoice", field)
				)

	def test_the_dimension_fields_are_never_claimed(self):
		"""A document whose own cost centre belongs elsewhere SHOULD stay hidden."""
		for doctype, field in (
			("Sales Invoice", "cost_center"),
			("Sales Invoice", "set_warehouse"),
			("Sales Order", "cost_center"),
			("Purchase Invoice", "cost_center"),
		):
			with self.subTest(doctype=doctype, field=field):
				self.assertNotIn((doctype, field), self.claimed())

	def test_only_cost_centre_and_warehouse_links_are_claimed(self):
		"""Company, Branch, Customer, Supplier, Sales Person keep enforcing as configured."""
		for row in upf.secondary_fields():
			self.assertIn(row.options, upf.GATED_DOCTYPES)

	def test_masters_are_never_claimed(self):
		"""A master's fields are chosen by hand, not inherited from whoever opened the form."""
		claimed = self.claimed()
		for doctype, field in (
			("Employee", "payroll_cost_center"),
			("Department", "payroll_cost_center"),
			("Company", "round_off_cost_center"),
			("Cost Center", "parent_cost_center"),
		):
			with self.subTest(doctype=doctype, field=field):
				self.assertNotIn((doctype, field), claimed)

	def test_apply_is_idempotent_and_leaves_nothing_gating(self):
		before = self.claimed()
		marked = upf.apply()["marked"]
		self.assertEqual(sorted(f"{dt}.{fn}" for dt, fn in before), marked)
		self.assertEqual(upf.secondary_fields(), [])
		self.assertEqual(upf.apply()["marked"], [])

	def test_a_flagged_field_is_recognised_however_it_was_flagged(self):
		"""Property Setters this site already ships are somebody's decision, not ours to re-make."""
		existing = frappe.get_all(
			"Property Setter",
			filters={"property": upf.FLAG, "value": ("in", ("1", 1))},
			fields=["doc_type", "field_name"],
			limit=1,
		)
		if not existing:
			self.skipTest("no ignore_user_permissions Property Setter on this site")
		self.assertTrue(upf.is_flagged(existing[0].doc_type, existing[0].field_name))
