# sf_trading/tests/test_sales_return_reason.py
"""Tests for the Sales Return Reason master: the seed patch and the Sales Invoice template field.

    bench --site <scratch-site> run-tests --module sf_trading.tests.test_sales_return_reason
"""

from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase

from sf_trading.patches.v0_1.seed_sales_return_reasons import REASONS
from sf_trading.patches.v0_1.seed_sales_return_reasons import execute as seed_sales_return_reasons
from sf_trading.sales_return_reason import OTHER_REASON, require_reason_template, validate_return_reason


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

	def test_template_field_is_present_on_sales_invoice(self):
		meta = frappe.get_meta("Sales Invoice")
		field = meta.get_field("custom_return_reason_template")
		self.assertIsNotNone(field, "custom_return_reason_template is missing from Sales Invoice")
		self.assertEqual(field.fieldtype, "Link")
		self.assertEqual(field.options, "Sales Return Reason")

	def test_pre_existing_return_reason_field_is_untouched(self):
		# this module must never repurpose the pre-existing free-text field
		meta = frappe.get_meta("Sales Invoice")
		field = meta.get_field("custom_return_reason")
		self.assertIsNotNone(field, "custom_return_reason should still exist, unowned by this app")
		self.assertEqual(field.fieldtype, "Data")

	# ── the "Other" -> must-say-more-than-just-that rule, checked server-side ─────────
	def make_return(self, template=None, reason_text=None):
		return frappe._dict(
			doctype="Sales Invoice",
			is_return=1,
			custom_return_reason_template=template,
			custom_return_reason=reason_text,
		)

	def test_other_with_nothing_appended_is_refused(self):
		doc = self.make_return(template=OTHER_REASON, reason_text=OTHER_REASON)
		self.assertRaises(frappe.ValidationError, validate_return_reason, doc)

	def test_other_with_a_description_is_allowed(self):
		doc = self.make_return(template=OTHER_REASON, reason_text="Other Customer changed their mind")
		validate_return_reason(doc)

	def test_a_named_reason_needs_no_extra_text(self):
		doc = self.make_return(template="Damaged", reason_text="Damaged")
		validate_return_reason(doc)

	def test_an_ordinary_invoice_is_left_alone(self):
		doc = frappe._dict(
			doctype="Sales Invoice",
			is_return=0,
			custom_return_reason_template=OTHER_REASON,
			custom_return_reason=OTHER_REASON,
		)
		validate_return_reason(doc)


# ── the template itself is required on a return, on the requester's own saves ─────────────────
def stub_return(template=None, docstatus=0, state=None, was=None, pm_action=None, **extra):
	before = frappe._dict(workflow_state=was) if was is not None else None
	return frappe._dict(
		doctype="Sales Invoice",
		is_return=1,
		docstatus=docstatus,
		workflow_state=state,
		custom_return_reason_template=template,
		custom_return_reason="Customer changed the size",
		flags=frappe._dict(pm_workflow_action=pm_action),
		get_doc_before_save=lambda: before,
		**extra,
	)


class TestReturnReasonRequired(FrappeTestCase):
	def test_a_draft_return_without_a_template_is_refused(self):
		self.assertRaises(frappe.ValidationError, require_reason_template, stub_return())

	def test_a_picked_template_passes(self):
		require_reason_template(stub_return(template="Damaged"))

	def test_an_ordinary_invoice_is_left_alone(self):
		require_reason_template(frappe._dict(doctype="Sales Invoice", is_return=0))

	def test_a_direct_submit_is_refused(self):
		self.assertRaises(frappe.ValidationError, require_reason_template, stub_return(docstatus=1))

	def test_send_for_approval_is_the_requesters_action_and_is_refused(self):
		doc = stub_return(state="Pending", was="Draft", pm_action="Send for Approval")
		self.assertRaises(frappe.ValidationError, require_reason_template, doc)

	def test_resending_a_rejected_return_is_refused(self):
		doc = stub_return(state="Pending", was="Rejected", pm_action="Send for Approval")
		self.assertRaises(frappe.ValidationError, require_reason_template, doc)

	def test_the_approvers_approve_is_never_refused(self):
		# a return already in the chain before the rule existed must still be approvable
		require_reason_template(stub_return(docstatus=1, state="Approved", was="Pending", pm_action="Approve"))

	def test_the_approvers_reject_is_never_refused(self):
		require_reason_template(stub_return(state="Rejected", was="Pending", pm_action="Reject"))

	def test_another_save_while_waiting_for_approval_is_left_alone(self):
		require_reason_template(stub_return(state="Pending", was="Pending"))

	def test_ignore_mandatory_stands_aside(self):
		doc = stub_return()
		doc.flags.ignore_mandatory = True
		require_reason_template(doc)

	def test_an_import_stands_aside(self):
		frappe.flags.in_import = True
		try:
			require_reason_template(stub_return())
		finally:
			frappe.flags.in_import = False

	def test_a_consolidated_pos_credit_note_stands_aside(self):
		require_reason_template(stub_return(is_consolidated=1))

	def test_a_credit_note_issued_by_a_return_delivery_note_stands_aside(self):
		doc = stub_return(items=[frappe._dict(delivery_note="DN-RET-0001")])
		with patch.object(frappe.db, "exists", return_value=True) as exists:
			require_reason_template(doc)
		self.assertEqual(exists.call_args.args[0], "Delivery Note")
		with patch.object(frappe.db, "exists", return_value=False):
			self.assertRaises(frappe.ValidationError, require_reason_template, doc)

	def test_the_form_marks_it_mandatory_on_a_draft_return(self):
		field = frappe.get_meta("Sales Invoice").get_field("custom_return_reason_template")
		self.assertIn("doc.is_return", field.mandatory_depends_on or "")
		self.assertIn("docstatus == 0", field.mandatory_depends_on or "")

	def test_the_rule_runs_on_sales_invoice_validate(self):
		hooks = frappe.get_hooks("doc_events").get("Sales Invoice", {}).get("validate", [])
		self.assertIn("sf_trading.sales_return_reason.require_reason_template", hooks)
