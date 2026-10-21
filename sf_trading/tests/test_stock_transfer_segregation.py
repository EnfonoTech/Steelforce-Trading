# sf_trading/tests/test_stock_transfer_segregation.py
"""A stock transfer is sent by the source warehouse's keeper and accepted by someone else
(sf_trading/approval_routing.py, ensure_transfer_segregation).

    bench --site <site> run-tests --module sf_trading.tests.test_stock_transfer_segregation
"""

from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase

from sf_trading import approval_routing as routing

#: the two conditions exactly as they stand on production before this rule
OLD_SEND = 'doc.purpose == "Material Transfer"'
OLD_ACCEPT = (
	'frappe.db.get_value(\n    "User Permission",\n    {\n        "user": frappe.session.user,\n'
	'        "allow": "Warehouse",\n        "for_value": doc.get("items")[0].get("t_warehouse") if doc.get("items") else None,\n'
	'        "is_default": 1,\n    },\n    "name",\n)'
)

#: (user, warehouse) pairs that hold a DEFAULT Warehouse User Permission
KEEPERS = {("sender", "SRC - X"), ("receiver", "DST - X"), ("sender", "OTHER - X")}


def _get_value(doctype, filters, fieldname):
	assert doctype == "User Permission" and filters["allow"] == "Warehouse" and filters["is_default"] == 1
	return "UP-1" if (filters["user"], filters["for_value"]) in KEEPERS else None


def _allows(condition, user, doc):
	"""Evaluate a transition condition the way permission_manager does."""
	env = {"frappe": frappe._dict(db=frappe._dict(get_value=_get_value), session=frappe._dict(user=user))}
	return bool(frappe.safe_eval(condition, env, {"doc": frappe._dict(doc)}))


def _transfer(owner="creator", src="SRC - X", dst="DST - X", purpose="Material Transfer"):
	return {"owner": owner, "purpose": purpose, "items": [{"s_warehouse": src, "t_warehouse": dst}]}


class TestTheConditions(FrappeTestCase):
	def test_only_the_source_keeper_can_send(self):
		send = routing.transfer_send_condition(OLD_SEND)
		self.assertTrue(_allows(send, "sender", _transfer()))
		self.assertFalse(_allows(send, "receiver", _transfer()))  # keeps the destination, not the source
		self.assertFalse(_allows(send, "stranger", _transfer()))

	def test_a_holder_of_a_plain_permission_on_the_source_is_not_enough(self):
		# the case behind MAT-STE-2026-06511: permission on SRC, but not its default keeper
		self.assertFalse(_allows(routing.transfer_send_condition(OLD_SEND), "plain-permission-holder", _transfer()))

	def test_the_old_purpose_test_still_applies(self):
		send = routing.transfer_send_condition(OLD_SEND)
		self.assertFalse(_allows(send, "sender", _transfer(purpose="Material Issue")))

	def test_a_transfer_without_rows_cannot_be_sent(self):
		self.assertFalse(_allows(routing.transfer_send_condition(OLD_SEND), "sender", {"owner": "x", "purpose": "Material Transfer", "items": []}))

	def test_the_receiving_keeper_accepts_but_not_their_own_transfer(self):
		accept = routing.transfer_accept_condition(OLD_ACCEPT)
		self.assertTrue(_allows(accept, "receiver", _transfer(owner="sender")))
		self.assertFalse(_allows(accept, "receiver", _transfer(owner="receiver")))  # MAT-STE-2026-06511
		self.assertFalse(_allows(accept, "sender", _transfer(owner="sender")))
		self.assertFalse(_allows(accept, "stranger", _transfer(owner="sender")))

	def test_an_empty_current_condition_leaves_just_the_rule(self):
		self.assertTrue(_allows(routing.transfer_send_condition(), "sender", _transfer()))
		self.assertTrue(_allows(routing.transfer_accept_condition(), "receiver", _transfer(owner="sender")))
		self.assertFalse(_allows(routing.transfer_accept_condition(), "receiver", _transfer(owner="receiver")))


class FakeTable:
	"""The two transitions as the site holds them, written back by set_value."""

	def __init__(self, send=OLD_SEND, accept=OLD_ACCEPT, extra=()):
		self.rows = {"r-send": ("Draft", "Send for Acceptance", send),
			"r-accept": ("Pending Acceptance", "Accept", accept),
			"r-reject": ("Pending Acceptance", "Reject", OLD_ACCEPT),
			"r-submit": ("Draft", "Submit", 'doc.purpose != "Material Transfer"')}
		self.writes = []

	def get_all(self, *a, **k):
		return [frappe._dict(name=n, state=s, action=act, condition=c) for n, (s, act, c) in self.rows.items()]

	def set_value(self, doctype, name, field, value, **k):
		self.writes.append((name, k.get("update_modified")))
		s, act, _ = self.rows[name]
		self.rows[name] = (s, act, value)


class TestTheUpgrade(FrappeTestCase):
	def _run(self, table, exists=True):
		with patch.object(routing.frappe.db, "exists", return_value=exists), \
				patch.object(routing.frappe.db, "get_all", table.get_all), \
				patch.object(routing.frappe.db, "set_value", table.set_value), \
				patch.object(routing.frappe, "clear_document_cache") as doc_cache, \
				patch.object(routing.frappe, "clear_cache") as cache:
			return routing.ensure_transfer_segregation(), doc_cache, cache

	def test_the_send_and_accept_rows_get_their_rule_and_nothing_else_moves(self):
		table = FakeTable()
		changed, doc_cache, cache = self._run(table)
		self.assertTrue(changed)
		self.assertEqual(sorted(w[0] for w in table.writes), ["r-accept", "r-send"])
		self.assertTrue(all(w[1] is False for w in table.writes))  # `modified` untouched
		self.assertIn("s_warehouse", table.rows["r-send"][2])
		self.assertIn('doc.get("owner")', table.rows["r-accept"][2])
		self.assertEqual(table.rows["r-reject"][2], OLD_ACCEPT)  # reject is unchanged
		self.assertEqual(table.rows["r-submit"][2], 'doc.purpose != "Material Transfer"')
		doc_cache.assert_called_once_with("PM Workflow", routing.TRANSFER_WORKFLOW)
		cache.assert_called_once_with(doctype="Stock Entry")

	def test_it_is_done_once(self):
		table = FakeTable()
		self._run(table)
		before = dict(table.rows)
		table.writes.clear()
		changed, doc_cache, cache = self._run(table)
		self.assertFalse(changed)
		self.assertEqual((table.writes, table.rows), ([], before))
		doc_cache.assert_not_called()

	def test_a_condition_someone_changed_is_wrapped_not_replaced(self):
		table = FakeTable(send='doc.purpose == "Material Transfer" and doc.company == "X"')
		self._run(table)
		self.assertIn('doc.company == "X"', table.rows["r-send"][2])
		self.assertIn("s_warehouse", table.rows["r-send"][2])

	def test_a_site_without_the_workflow_is_left_alone(self):
		table = FakeTable()
		changed, doc_cache, cache = self._run(table, exists=False)
		self.assertFalse(changed)
		self.assertEqual(table.writes, [])
