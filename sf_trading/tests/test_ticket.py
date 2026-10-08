"""Tests for support tickets: who may do what, who can see them, and who hears about what.

    bench --site <scratch-site> run-tests --module sf_trading.tests.test_ticket

Everything here runs inside the class's own transaction and is rolled back -- nothing commits.
Notifications are made inline because frappe.flags.in_test makes the enqueue run at once.
"""

import frappe
from frappe.desk.form import assign_to
from frappe.desk.form.utils import add_comment
from frappe.tests.utils import FrappeTestCase

from sf_trading import ticket

DESK_ROLE = "Ticket Test Desk Role"
REPORTER = "ticket-test-reporter@example.com"
ASSIGNEE = "ticket-test-assignee@example.com"
OTHER_ASSIGNEE = "ticket-test-other@example.com"
MANAGER = "ticket-test-manager@example.com"
WATCHER = "ticket-test-watcher@example.com"
OUTSIDER = "ticket-test-outsider@example.com"


def _ensure_role(role):
	if not frappe.db.exists("Role", role):
		frappe.get_doc({"doctype": "Role", "role_name": role, "desk_access": 1}).insert(ignore_permissions=True)


def _make_user(email, role):
	if frappe.db.exists("User", email):
		return
	frappe.get_doc(
		{
			"doctype": "User",
			"email": email,
			"first_name": email.split("@")[0].replace("ticket-test-", "").title(),
			"send_welcome_email": 0,
			"roles": [{"role": role}],
		}
	).insert(ignore_permissions=True)


class TestTicket(FrappeTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		_ensure_role(ticket.MANAGER_ROLE)
		_ensure_role(DESK_ROLE)
		_make_user(MANAGER, ticket.MANAGER_ROLE)
		for email in (REPORTER, ASSIGNEE, OTHER_ASSIGNEE, WATCHER, OUTSIDER):
			_make_user(email, DESK_ROLE)

	def setUp(self):
		frappe.set_user("Administrator")

	def tearDown(self):
		frappe.set_user("Administrator")

	# ── helpers ──

	def raise_ticket(self, as_user=REPORTER, **fields):
		frappe.set_user(as_user)
		try:
			return frappe.get_doc(
				{"doctype": "Ticket", "subject": "Invoice will not print", "description": "<p>It broke</p>", **fields}
			).insert()
		finally:
			frappe.set_user("Administrator")

	def act(self, as_user, method, *args, **kwargs):
		frappe.set_user(as_user)
		try:
			return method(*args, **kwargs)
		finally:
			frappe.set_user("Administrator")

	def assigned(self, as_user=MANAGER, to=ASSIGNEE, **fields):
		doc = self.raise_ticket(**fields)
		self.act(as_user, ticket.assign, doc.name, to)
		return frappe.get_doc("Ticket", doc.name)

	def bells(self, user, name):
		return frappe.get_all(
			"Notification Log",
			filters={"for_user": user, "document_type": "Ticket", "document_name": name},
			pluck="subject",
		)

	def todo_status(self, name, user):
		return frappe.db.get_value(
			"ToDo",
			{"reference_type": "Ticket", "reference_name": name, "allocated_to": user},
			"status",
			order_by="creation desc",
		)

	def can(self, user, ptype, doc):
		return frappe.has_permission("Ticket", ptype, doc=frappe.get_doc("Ticket", doc.name), user=user)

	# ── raising ──

	def test_raise_starts_open_and_tells_the_managers(self):
		doc = self.raise_ticket()
		self.assertTrue(doc.name.startswith("TKT-"))
		self.assertEqual(doc.status, ticket.OPEN)
		self.assertEqual(doc.raised_by, REPORTER)
		self.assertTrue(doc.opening_date)
		self.assertTrue(any("raised a new ticket" in s for s in self.bells(MANAGER, doc.name)))
		# nobody is told about the ticket they raised themselves
		self.assertFalse(self.bells(REPORTER, doc.name))

	def test_reporter_cannot_raise_for_someone_else_or_preassign(self):
		with self.assertRaises(frappe.ValidationError):
			self.raise_ticket(raised_by=OUTSIDER)
		with self.assertRaises(frappe.ValidationError):
			self.raise_ticket(assignee=ASSIGNEE)

	def test_manager_may_raise_on_someones_behalf(self):
		doc = self.raise_ticket(as_user=MANAGER, raised_by=REPORTER)
		self.assertEqual(doc.raised_by, REPORTER)
		self.assertTrue(any("for you" in s for s in self.bells(REPORTER, doc.name)))

	# ── allocation ──

	def test_assigning_lists_it_for_the_assignee_and_tells_the_reporter(self):
		doc = self.assigned()
		self.assertEqual(doc.assignee, ASSIGNEE)
		self.assertEqual(self.todo_status(doc.name, ASSIGNEE), "Open")
		self.assertTrue(any("is now with" in s for s in self.bells(REPORTER, doc.name)))
		self.assertTrue(self.can(ASSIGNEE, "write", doc))
		self.assertFalse(self.can(OUTSIDER, "read", doc))

	def test_reassigning_cancels_the_old_assignment(self):
		doc = self.assigned()
		self.act(MANAGER, ticket.assign, doc.name, OTHER_ASSIGNEE)
		self.assertEqual(frappe.db.get_value("Ticket", doc.name, "assignee"), OTHER_ASSIGNEE)
		self.assertEqual(self.todo_status(doc.name, ASSIGNEE), "Cancelled")
		self.assertEqual(self.todo_status(doc.name, OTHER_ASSIGNEE), "Open")

	def test_only_a_manager_or_the_assignee_may_allocate(self):
		doc = self.raise_ticket()
		with self.assertRaises(frappe.ValidationError):
			self.act(REPORTER, ticket.assign, doc.name, ASSIGNEE)

	def test_assignee_can_hand_over(self):
		doc = self.assigned()
		self.act(ASSIGNEE, ticket.assign, doc.name, OTHER_ASSIGNEE)
		self.assertEqual(frappe.db.get_value("Ticket", doc.name, "assignee"), OTHER_ASSIGNEE)

	def test_sidebar_assignment_reaches_the_field(self):
		doc = self.raise_ticket()
		self.act(MANAGER, assign_to.add, {"assign_to": [ASSIGNEE], "doctype": "Ticket", "name": doc.name})
		self.assertEqual(frappe.db.get_value("Ticket", doc.name, "assignee"), ASSIGNEE)
		self.act(MANAGER, assign_to.remove, "Ticket", doc.name, ASSIGNEE)
		self.assertIsNone(frappe.db.get_value("Ticket", doc.name, "assignee"))

	# ── working it ──

	def test_reporter_cannot_resolve_but_can_close(self):
		doc = self.assigned()
		with self.assertRaises(frappe.ValidationError):
			self.act(REPORTER, ticket.set_status, doc.name, ticket.RESOLVED, "<p>fixed it myself</p>")
		self.act(REPORTER, ticket.set_status, doc.name, ticket.CLOSED, "not needed any more")
		doc.reload()
		self.assertEqual(doc.status, ticket.CLOSED)
		self.assertTrue(doc.closed_on)
		self.assertEqual(self.todo_status(doc.name, ASSIGNEE), "Closed")

	def test_resolve_needs_details_and_tells_everyone_else(self):
		doc = self.assigned(watchers=[{"user": WATCHER}])
		with self.assertRaises(frappe.ValidationError):
			self.act(ASSIGNEE, ticket.set_status, doc.name, ticket.RESOLVED)
		self.act(ASSIGNEE, ticket.set_status, doc.name, ticket.RESOLVED, "<p>Reset the print format</p>")
		doc.reload()
		self.assertEqual(doc.status, ticket.RESOLVED)
		self.assertEqual(doc.resolved_by, ASSIGNEE)
		self.assertTrue(doc.resolved_on)
		self.assertGreaterEqual(doc.resolution_time, 0)
		self.assertEqual(self.todo_status(doc.name, ASSIGNEE), "Closed")
		for user in (REPORTER, WATCHER):
			self.assertTrue(any("resolved" in s for s in self.bells(user, doc.name)), user)
		self.assertFalse(any("resolved" in s for s in self.bells(ASSIGNEE, doc.name)))

	def test_reopen_counts_and_puts_it_back_on_the_list(self):
		doc = self.assigned()
		self.act(ASSIGNEE, ticket.set_status, doc.name, ticket.RESOLVED, "<p>done</p>")
		self.act(REPORTER, ticket.set_status, doc.name, ticket.OPEN, "still broken")
		doc.reload()
		self.assertEqual(doc.status, ticket.OPEN)
		self.assertEqual(doc.reopen_count, 1)
		self.assertIsNone(doc.resolved_on)
		self.assertEqual(self.todo_status(doc.name, ASSIGNEE), "Open")
		self.assertTrue(any("reopened" in s for s in self.bells(ASSIGNEE, doc.name)))
		self.assertTrue(
			frappe.db.exists(
				"Comment",
				{"reference_doctype": "Ticket", "reference_name": doc.name, "comment_type": "Comment",
				 "content": ["like", "%still broken%"]},
			)
		)

	def test_watcher_cannot_change_the_status(self):
		doc = self.assigned(watchers=[{"user": WATCHER}])
		with self.assertRaises(frappe.PermissionError):
			self.act(WATCHER, ticket.set_status, doc.name, ticket.CLOSED)

	# ── the conversation ──

	def test_reply_reaches_everyone_but_the_writer(self):
		doc = self.assigned(watchers=[{"user": WATCHER}])
		self.act(ASSIGNEE, add_comment, "Ticket", doc.name, "<p>Looking into it</p>", ASSIGNEE, "Assignee")
		for user in (REPORTER, WATCHER):
			self.assertTrue(any("replied on" in s for s in self.bells(user, doc.name)), user)
		self.assertFalse(any("replied on" in s for s in self.bells(ASSIGNEE, doc.name)))
		self.assertTrue(frappe.db.get_value("Ticket", doc.name, "first_response_on"))

	def test_mention_lets_someone_read_and_reply_but_not_edit(self):
		doc = self.assigned()
		mention = (
			f'<p><span class="mention" data-id="{OUTSIDER}" data-value="Outsider" '
			f'data-denotation-char="@">@Outsider</span> can you check stock?</p>'
		)
		self.act(ASSIGNEE, add_comment, "Ticket", doc.name, mention, ASSIGNEE, "Assignee")
		self.assertTrue(self.can(OUTSIDER, "read", doc))
		self.assertFalse(self.can(OUTSIDER, "write", doc))
		# the reply comes back to everyone else on the ticket, the mentioned person included
		self.act(OUTSIDER, add_comment, "Ticket", doc.name, "<p>Stock is fine</p>", OUTSIDER, "Outsider")
		self.assertTrue(any("replied on" in s for s in self.bells(ASSIGNEE, doc.name)))

	# ── desk furniture ──

	def test_workspace_and_help_entry_are_built_once(self):
		if frappe.db.exists("Workspace", ticket.WORKSPACE):
			frappe.delete_doc("Workspace", ticket.WORKSPACE, ignore_permissions=True, force=True)
		ticket.setup()
		ticket.setup()
		workspace = frappe.get_doc("Workspace", ticket.WORKSPACE)
		labels = [row.label for row in workspace.shortcuts]
		self.assertEqual(labels, [label for label, *_rest in ticket.SHORTCUTS])
		# a shortcut row renders nothing unless a content block names it by label
		blocks = {b["data"].get("shortcut_name") for b in frappe.parse_json(workspace.content) if b["type"] == "shortcut"}
		self.assertEqual(blocks, set(labels))
		help_rows = [
			row for row in frappe.get_single("Navbar Settings").help_dropdown if row.route == ticket.NEW_TICKET_ROUTE
		]
		self.assertEqual(len(help_rows), 1)
		self.assertEqual(help_rows[0].idx, 1)

	# ── who sees what ──

	def test_watcher_reads_but_cannot_edit(self):
		doc = self.raise_ticket(watchers=[{"user": WATCHER}])
		self.assertTrue(self.can(WATCHER, "read", doc))
		self.assertFalse(self.can(WATCHER, "write", doc))
		self.assertTrue(any("added you" in s for s in self.bells(WATCHER, doc.name)))

	def test_list_shows_only_tickets_you_are_on(self):
		mine = self.raise_ticket()
		theirs = self.raise_ticket(as_user=OUTSIDER)
		names = self.act(REPORTER, frappe.get_list, "Ticket", pluck="name", limit_page_length=0)
		self.assertIn(mine.name, names)
		self.assertNotIn(theirs.name, names)
		everything = self.act(MANAGER, frappe.get_list, "Ticket", pluck="name", limit_page_length=0)
		self.assertIn(mine.name, everything)
		self.assertIn(theirs.name, everything)
		self.assertEqual(ticket.get_permission_query_conditions(MANAGER), "")
