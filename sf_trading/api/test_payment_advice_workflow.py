# apps/sf_trading/sf_trading/api/test_payment_advice_workflow.py
"""Tests for the Payment Advice PM Workflow definition.

setup_workflow() commits, so it is not executed here — a test that commits would survive the
per-test rollback and leave a workflow on the site. What is tested is the definition itself
(the part that can be wrong) and the integration switch that makes the advice's own approver
rule stand down.
"""

import frappe
from frappe.tests.utils import FrappeTestCase

from sf_trading.api.payment_advice_workflow import (
	DOCTYPE,
	ROLE_APPROVER,
	ROLE_PREPARER,
	STATE_APPROVED,
	STATE_DRAFT,
	STATE_PENDING,
	STATE_REJECTED,
	WORKFLOW_NAME,
	_states,
	_transitions,
	has_active_workflow,
	pm_workflow_available,
	workflow_status,
)


class TestPaymentAdviceWorkflowDefinition(FrappeTestCase):
	def test_states_cover_the_lifecycle(self):
		states = {s["state"] for s in _states("X")}
		self.assertEqual(states, {STATE_DRAFT, STATE_PENDING, STATE_APPROVED, STATE_REJECTED})

	def test_only_approved_is_submitted(self):
		for state in _states("X"):
			expected = "1" if state["state"] == STATE_APPROVED else "0"
			self.assertEqual(state["doc_status"], expected, state["state"])

	def test_every_transition_targets_a_declared_state(self):
		declared = {s["state"] for s in _states("X")}
		for transition in _transitions("X"):
			self.assertIn(transition["state"], declared)
			self.assertIn(transition["next_state"], declared)

	def test_rejection_is_recoverable(self):
		"""A rejected advice must have a way back, or work dies there."""
		out_of_rejected = [t for t in _transitions("X") if t["state"] == STATE_REJECTED]
		self.assertTrue(out_of_rejected)
		self.assertEqual(out_of_rejected[0]["next_state"], STATE_PENDING)

	def test_approval_requires_paperwork_and_rejection_a_reason(self):
		approve = next(t for t in _transitions("X") if t["action"] == "Approve")
		reject = next(t for t in _transitions("X") if t["action"] == "Reject")
		self.assertTrue(approve.get("require_attachment"))
		self.assertTrue(reject.get("require_comment"))
		self.assertTrue(reject.get("is_return_for_correction"))

	def test_approver_cannot_be_the_preparer_role(self):
		"""Separation of duties: the role that raises must not be the role that approves."""
		self.assertNotEqual(ROLE_PREPARER, ROLE_APPROVER)
		approve = next(t for t in _transitions("X") if t["action"] == "Approve")
		self.assertEqual(approve["allowed"], ROLE_APPROVER)

	def test_only_submission_steps_allow_self_approval(self):
		for transition in _transitions("X"):
			if transition["action"] == "Approve":
				self.assertFalse(transition.get("allow_self_approval"))

	def test_roles_exist_on_this_site(self):
		for role in (ROLE_PREPARER, ROLE_APPROVER):
			self.assertTrue(frappe.db.exists("Role", role), role)

	def test_workflow_state_field_exists_on_payment_advice(self):
		"""PM Workflow writes into this field; without it the engine has nowhere to store state."""
		meta = frappe.get_meta(DOCTYPE)
		self.assertTrue(meta.has_field("workflow_state"))
		self.assertTrue(meta.get_field("workflow_state").allow_on_submit)


class TestWorkflowIntegration(FrappeTestCase):
	def test_availability_matches_permission_manager(self):
		self.assertEqual(pm_workflow_available(), bool(frappe.db.exists("DocType", "PM Workflow")))

	def test_has_active_workflow_returns_bool(self):
		self.assertIsInstance(has_active_workflow(), bool)

	def test_status_payload_shape(self):
		status = workflow_status()
		self.assertIn("available", status)
		self.assertIn("active", status)

	def test_approver_rule_stands_down_when_workflow_active(self):
		"""The advice's single-approver rule must yield to the workflow, not fight it."""
		from sf_trading.sf_trading.doctype.payment_advice import payment_advice as pa

		advice = frappe.new_doc("Payment Advice")
		advice.company = frappe.db.get_value("Company", {}, "name")
		advice.approver = None  # would normally be fatal at submit

		original = pa.workflow_controls_submission
		try:
			pa.workflow_controls_submission = lambda company=None: True
			advice.validate_approver()  # must not raise
		finally:
			pa.workflow_controls_submission = original

	def test_approver_rule_still_applies_without_a_workflow(self):
		from sf_trading.sf_trading.doctype.payment_advice import payment_advice as pa

		advice = frappe.new_doc("Payment Advice")
		advice.company = frappe.db.get_value("Company", {}, "name")
		advice.approver = None

		original = pa.workflow_controls_submission
		try:
			pa.workflow_controls_submission = lambda company=None: False
			with self.assertRaises(frappe.ValidationError):
				advice.validate_approver()
		finally:
			pa.workflow_controls_submission = original

	def test_workflow_name_is_stable(self):
		"""The name is the idempotency key for setup_workflow; changing it would duplicate."""
		self.assertEqual(WORKFLOW_NAME, "Payment Advice Approval")
