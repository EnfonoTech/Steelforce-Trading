# sf_trading/tests/test_sr_operations_approval.py
"""A Stock Reconciliation is approved by the Inventory Head and then by the Operations Head
(sf_trading/approval_routing.py).

    bench --site <site> run-tests --module sf_trading.tests.test_sr_operations_approval
"""

from unittest.mock import MagicMock, patch

import frappe
from frappe.tests.utils import FrappeTestCase

from sf_trading import approval_routing as routing


def _only(definition, state, action):
	rows = [t for t in definition["transitions"] if t["state"] == state and t["action"] == action]
	return rows


class TestTheDefinition(FrappeTestCase):
	def setUp(self):
		self.definition = routing.stock_reconciliation_workflow()

	def test_the_inventory_head_hands_over_instead_of_posting(self):
		(approve,) = _only(self.definition, "Pending", "Approve")
		self.assertEqual(approve["allowed"], "Inventory Head")
		self.assertEqual(approve["next_state"], routing.SR_SECOND_STATE)
		self.assertEqual(approve["allow_self_approval"], 0)
		self.assertEqual(approve["require_attachment"], 1)

	def test_only_the_operations_heads_approval_posts_it(self):
		(approve,) = _only(self.definition, routing.SR_SECOND_STATE, "Approve")
		self.assertEqual((approve["allowed"], approve["next_state"]), ("Operations Head", "Approved"))
		self.assertEqual(approve["allow_self_approval"], 0)
		self.assertEqual(approve["require_attachment"], 1)
		# and nothing else reaches Approved
		reach = [t for t in self.definition["transitions"] if t["next_state"] == "Approved"]
		self.assertEqual(len(reach), 1)
		submitted = [s["state"] for s in self.definition["states"] if s["doc_status"] == "1"]
		self.assertEqual(submitted, ["Approved"])

	def test_either_approver_can_send_it_back_with_a_reason(self):
		for state, role in (("Pending", "Inventory Head"), (routing.SR_SECOND_STATE, "Operations Head")):
			(reject,) = _only(self.definition, state, "Reject")
			self.assertEqual((reject["allowed"], reject["next_state"]), (role, "Rejected"))
			self.assertEqual((reject["require_comment"], reject["is_return_for_correction"]), (1, 1))
			self.assertEqual(reject["allow_self_approval"], 0)

	def test_the_second_state_is_a_draft_state_the_operations_head_owns(self):
		(state,) = [s for s in self.definition["states"] if s["state"] == routing.SR_SECOND_STATE]
		self.assertEqual((state["doc_status"], state["allow_edit"]), ("0", "Operations Head"))

	def test_the_preparers_still_send_and_resend(self):
		sends = [t for t in self.definition["transitions"] if t["action"] == "Send for Approval"]
		self.assertEqual({(t["state"], t["allowed"]) for t in sends},
			{(s, r) for s in ("Draft", "Rejected") for r in routing.SR_PREPARER_ROLES})
		self.assertTrue(all(t["next_state"] == "Pending" and t["require_attachment"] == 1 for t in sends))

	def test_every_state_and_transition_hangs_together(self):
		states = {s["state"] for s in self.definition["states"]}
		for t in self.definition["transitions"]:
			self.assertIn(t["state"], states)
			self.assertIn(t["next_state"], states)

	def test_the_new_state_has_a_style(self):
		self.assertEqual(routing.STATE_STYLES[routing.SR_SECOND_STATE], "Warning")


class FakeWorkflow:
	"""Enough of a PM Workflow for the upgrade: child rows as dicts, append, save."""

	def __init__(self, states, transitions):
		self.states = [frappe._dict(s) for s in states]
		self.transitions = [frappe._dict(t) for t in transitions]
		self.saved = 0

	def append(self, table, row):
		getattr(self, table).append(frappe._dict(row))

	def save(self, **kwargs):
		self.saved += 1


def _as_created_on_11_october():
	"""The workflow exactly as it was on production before the second approval."""
	definition = routing.stock_reconciliation_workflow()
	states = [s for s in definition["states"] if s["state"] != routing.SR_SECOND_STATE]
	transitions = [t for t in definition["transitions"] if t["state"] != routing.SR_SECOND_STATE]
	for t in transitions:
		if t["state"] == "Pending" and t["action"] == "Approve":
			t["next_state"] = "Approved"
	return FakeWorkflow(states, transitions)


class TestTheUpgrade(FrappeTestCase):
	def _upgrade(self, workflow, exists=True):
		masters, grant = MagicMock(), MagicMock()
		with patch.object(routing.frappe.db, "exists", return_value=exists), \
				patch.object(routing.frappe, "get_doc", return_value=workflow), \
				patch.object(routing.frappe, "clear_cache"), \
				patch.object(routing, "_ensure_masters", masters), \
				patch.object(routing, "grant_second_approver_access", grant):
			return routing.ensure_operations_approval(), masters, grant

	def test_an_existing_workflow_gets_the_second_stage(self):
		workflow = _as_created_on_11_october()
		changed, masters, grant = self._upgrade(workflow)
		self.assertTrue(changed)
		self.assertEqual(workflow.saved, 1)
		grant.assert_called_once()
		masters.assert_called_once()
		by = {(t.state, t.action): t for t in workflow.transitions}
		self.assertEqual(by[("Pending", "Approve")].next_state, routing.SR_SECOND_STATE)
		self.assertEqual(by[(routing.SR_SECOND_STATE, "Approve")].allowed, "Operations Head")
		self.assertEqual(by[(routing.SR_SECOND_STATE, "Approve")].next_state, "Approved")
		self.assertEqual(by[(routing.SR_SECOND_STATE, "Reject")].next_state, "Rejected")
		self.assertIn(routing.SR_SECOND_STATE, [s.state for s in workflow.states])
		# nothing was removed: 4 sends + 2 first-stage + 2 second-stage, 4 states + 1
		self.assertEqual((len(workflow.transitions), len(workflow.states)), (8, 5))

	def test_it_is_done_once(self):
		workflow = _as_created_on_11_october()
		self._upgrade(workflow)
		changed, masters, grant = self._upgrade(workflow)
		self.assertFalse(changed)
		self.assertEqual((workflow.saved, len(workflow.transitions), len(workflow.states)), (1, 8, 5))
		grant.assert_not_called()

	def test_a_workflow_someone_reshaped_is_left_alone(self):
		workflow = _as_created_on_11_october()
		workflow.transitions = [t for t in workflow.transitions if not (t.state == "Pending" and t.action == "Approve")]
		changed, masters, grant = self._upgrade(workflow)
		self.assertFalse(changed)
		self.assertEqual(workflow.saved, 0)
		grant.assert_not_called()

	def test_a_site_without_the_workflow_is_left_alone(self):
		changed, masters, grant = self._upgrade(_as_created_on_11_october(), exists=False)
		self.assertFalse(changed)
		grant.assert_not_called()


class TestTheRoleAndItsAccess(FrappeTestCase):
	def test_the_role_may_read_write_and_submit_and_no_more(self):
		add, prop = MagicMock(), MagicMock()
		with patch.object(routing.frappe.db, "exists", return_value=True), \
				patch("frappe.permissions.add_permission", add), patch("frappe.permissions.update_permission_property", prop):
			routing.grant_second_approver_access()
		add.assert_called_once_with("Stock Reconciliation", "Operations Head", 0)
		granted = [call.args[3] for call in prop.call_args_list]
		self.assertEqual(granted, ["read", "write", "submit"])
		self.assertTrue(all(call.args[4] == 1 and call.kwargs == {"validate": False} for call in prop.call_args_list))

	def test_the_role_is_created_when_the_site_has_none(self):
		made = MagicMock()
		with patch.object(routing.frappe.db, "exists", return_value=False), patch.object(routing.frappe, "get_doc", made), \
				patch("frappe.permissions.add_permission"), patch("frappe.permissions.update_permission_property"):
			routing.grant_second_approver_access()
		made.assert_called_once_with({"doctype": "Role", "role_name": "Operations Head", "desk_access": 1})

	def test_a_fresh_site_gets_the_access_with_the_workflow(self):
		grant = MagicMock()
		inserted = []

		class Doc:
			def __init__(self, definition):
				self.definition = definition

			def insert(self, **kwargs):
				inserted.append(self.definition["workflow_name"])

		with patch.object(routing.frappe.db, "exists", return_value=False), patch.object(routing, "_ensure_masters"), \
				patch.object(routing, "grant_second_approver_access", grant), patch.object(routing.frappe, "get_doc", Doc):
			created = routing.ensure_workflows()
		self.assertEqual(created, [routing.SO_WORKFLOW, routing.SR_WORKFLOW])
		grant.assert_called_once()
