"""Tests for announcements: who sees an update, who is told, who can open its report.

    bench --site <scratch-site> run-tests --module sf_trading.tests.test_announcements

Everything runs inside the class's own transaction and is rolled back. The two scheduler paths that
commit (run_schedule) are run with frappe.db.commit switched off, so nothing here outlives the run.
Notifications are made inline because frappe.flags.in_test makes the enqueue run at once.
"""

from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import add_to_date, now_datetime

from sf_trading import announcements as ann

ROLE_A = "Ann Test Role A"
ROLE_B = "Ann Test Role B"
USER_A = "ann-test-a@example.com"  # role A; an Employee in the child department, in the branch
USER_B = "ann-test-b@example.com"  # role B; an Employee in the parent department
USER_C = "ann-test-c@example.com"  # role B; no Employee at all
MANAGER = "ann-test-manager@example.com"
USERS = (USER_A, USER_B, USER_C)


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
			"first_name": email.split("@")[0].replace("ann-test-", "").title(),
			"send_welcome_email": 0,
			"roles": [{"role": role}],
		}
	).insert(ignore_permissions=True)


def _make_employee(user, company, department=None, branch=None):
	frappe.get_doc(
		{
			"doctype": "Employee",
			"first_name": user.split("@")[0],
			# sft-uat's Employee form also requires a number (hr_suite)
			"employee_number": "T-" + user.split("@")[0],
			"gender": "Male",
			"date_of_birth": "1990-01-01",
			"date_of_joining": "2020-01-01",
			"company": company,
			"status": "Active",
			"user_id": user,
			"department": department,
			"branch": branch,
		}
	).insert(ignore_permissions=True)


def _announcement(rows, publish=True, **fields):
	doc = frappe.get_doc(
		{
			"doctype": ann.DOCTYPE,
			"title": fields.pop("title", "A test update"),
			"summary": fields.pop("summary", "Something to know."),
			"kind": fields.pop("kind", "Announcement"),
			"audience": rows,
			**fields,
		}
	)
	doc.insert(ignore_permissions=True)
	if publish:
		doc.published_by = MANAGER
		ann.go_live(doc)
	return doc


def _bells(doc, user):
	return frappe.db.count(
		"Notification Log", {"document_type": ann.DOCTYPE, "document_name": doc.name, "for_user": user}
	)


class TestAnnouncements(FrappeTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		_ensure_role(ROLE_A)
		_ensure_role(ROLE_B)
		_make_user(USER_A, ROLE_A)
		_make_user(USER_B, ROLE_B)
		_make_user(USER_C, ROLE_B)
		_make_user(MANAGER, ann.MANAGER_ROLE)

		company = frappe.defaults.get_global_default("company") or frappe.db.get_value("Company", {}, "name")
		parent = frappe.get_doc(
			{"doctype": "Department", "department_name": "Ann Test Parent", "company": company, "is_group": 1,
			 "parent_department": "All Departments"}
		).insert(ignore_permissions=True)
		child = frappe.get_doc(
			{"doctype": "Department", "department_name": "Ann Test Child", "company": company,
			 "parent_department": parent.name}
		).insert(ignore_permissions=True)
		if not frappe.db.exists("Branch", "Ann Test Branch"):
			frappe.get_doc({"doctype": "Branch", "branch": "Ann Test Branch"}).insert(ignore_permissions=True)
		cls.parent_department, cls.child_department = parent.name, child.name
		cls.company = company
		_make_employee(USER_A, company, department=child.name, branch="Ann Test Branch")
		_make_employee(USER_B, company, department=parent.name)

	def setUp(self):
		# Nothing in a test may commit (notify_audience commits every 50 people, and "Everyone" on a real
		# site is more than 50, which would keep the test data) and nothing may ring a real person's
		# screen with a live push. Both are switched off for the test.
		for patcher in (patch.object(frappe.db, "commit"), patch.object(frappe, "publish_realtime")):
			patcher.start()
			self.addCleanup(patcher.stop)
		# an empty feed inside this class's transaction: nothing real is lost, and neither real updates nor
		# those an earlier test left change what this one sees
		frappe.db.delete("Notification Log", {"document_type": ann.DOCTYPE})
		frappe.db.delete(ann.READ_LOG)
		frappe.db.delete(ann.AUDIENCE, {"parenttype": ann.DOCTYPE})
		frappe.db.delete(ann.DOCTYPE)

	def tearDown(self):
		frappe.set_user("Administrator")

	# ── Who is in an audience, asked from both sides ───────────────────────────────────

	def _audiences(self):
		return {
			"everyone": ([{"audience_type": "Everyone"}], set(USERS)),
			"role": ([{"audience_type": "Role", "role": ROLE_A}], {USER_A}),
			"named": ([{"audience_type": "User", "user": USER_C}], {USER_C}),
			"department with sub": (
				[{"audience_type": "Department", "department": self.parent_department, "include_sub_departments": 1}],
				{USER_A, USER_B},
			),
			"department alone": (
				[{"audience_type": "Department", "department": self.parent_department, "include_sub_departments": 0}],
				{USER_B},
			),
			"branch": ([{"audience_type": "Branch", "branch": "Ann Test Branch"}], {USER_A}),
			# both Employees work for the company; USER_C has no Employee and no company permission
			"company": ([{"audience_type": "Company", "company": self.company}], {USER_A, USER_B}),
			"two rows": (
				[{"audience_type": "Role", "role": ROLE_A}, {"audience_type": "User", "user": USER_C}],
				{USER_A, USER_C},
			),
		}

	def test_audience_is_the_same_from_both_sides(self):
		for label, (rows, expected) in self._audiences().items():
			doc = _announcement(rows, title=label)
			resolved = set(ann.resolve_audience(doc.audience)) & set(USERS)
			seeing = {user for user in USERS if doc.name in ann.visible_names(user)}
			self.assertEqual(resolved, expected, f"resolve_audience: {label}")
			self.assertEqual(seeing, expected, f"visible_names: {label}")

	def test_a_company_user_permission_ties_a_person_to_that_company(self):
		doc = _announcement([{"audience_type": "Company", "company": self.company}])
		self.assertNotIn(doc.name, ann.visible_names(USER_C), "no Employee and no permission: tied to no company")
		self.assertNotIn(USER_C, ann.resolve_audience(doc.audience))

		frappe.get_doc(
			{"doctype": "User Permission", "user": USER_C, "allow": "Company", "for_value": self.company}
		).insert(ignore_permissions=True)
		self.assertIn(doc.name, ann.visible_names(USER_C))
		self.assertIn(USER_C, ann.resolve_audience(doc.audience))

	def test_an_update_for_another_company_does_not_reach_them(self):
		other = frappe.db.get_value("Company", {"name": ["!=", self.company]}, "name")
		if not other:
			self.skipTest("needs a second company")
		doc = _announcement([{"audience_type": "Company", "company": other}])
		self.assertFalse(set(ann.resolve_audience(doc.audience)) & set(USERS))
		for user in USERS:
			self.assertNotIn(doc.name, ann.visible_names(user))

	def test_a_company_row_must_name_the_company(self):
		# built, not inserted: saving fills an empty Company link with the signed-in user's default company,
		# so on a site that has one (prod) the row would no longer be empty by the time it is checked
		doc = frappe.get_doc(
			{
				"doctype": ann.DOCTYPE,
				"title": "A test update",
				"summary": "Something to know.",
				"kind": "Announcement",
				"audience": [{"audience_type": "Company"}],
			}
		)
		with self.assertRaises(frappe.ValidationError):
			ann.check_audience(doc)

	def test_a_company_row_reads_as_its_company_in_the_audience_line(self):
		doc = _announcement([{"audience_type": "Company", "company": self.company}, {"audience_type": "Role", "role": ROLE_A}])
		line = ann._audience_labels([doc.name], ann._audience_rows([doc.name]))[doc.name]
		self.assertEqual(line, f"{self.company} company, {ROLE_A}")

	def test_nothing_reads_the_company_column_before_the_migrate_that_adds_it(self):
		doc = _announcement([{"audience_type": "Role", "role": ROLE_A}])
		with patch.object(ann, "_has_company_audience", return_value=False):
			self.assertIn(doc.name, ann.visible_names(USER_A))
			rows = ann._audience_rows([doc.name])
			self.assertNotIn("company", rows[doc.name][0])

	def test_a_draft_a_scheduled_and_an_expired_update_are_not_seen(self):
		rows = [{"audience_type": "Everyone"}]
		draft = _announcement(rows, publish=False, title="Draft")
		self.assertNotIn(draft.name, ann.visible_names(USER_A))

		scheduled = _announcement(rows, publish=False, title="Later", publish_on=add_to_date(now_datetime(), hours=3))
		scheduled.db_set("status", ann.SCHEDULED)
		self.assertNotIn(scheduled.name, ann.visible_names(USER_A))

		expired = _announcement(rows, title="Gone")
		expired.db_set("expires_on", add_to_date(now_datetime(), minutes=-5))
		self.assertNotIn(expired.name, ann.visible_names(USER_A), "hidden the moment it passes Hide After")

	def test_the_scheduler_sends_what_is_due_and_retires_what_is_over(self):
		rows = [{"audience_type": "User", "user": USER_A}]
		due = _announcement(rows, publish=False, title="Due", publish_on=add_to_date(now_datetime(), hours=1))
		due.db_set({"status": ann.SCHEDULED, "published_by": MANAGER, "publish_on": add_to_date(now_datetime(), minutes=-1)})
		over = _announcement(rows, title="Over")
		over.db_set("expires_on", add_to_date(now_datetime(), minutes=-1))

		with patch.object(frappe.db, "commit"):
			ann.run_schedule()

		self.assertEqual(frappe.db.get_value(ann.DOCTYPE, due.name, "status"), ann.PUBLISHED)
		self.assertIn(due.name, ann.visible_names(USER_A))
		self.assertEqual(_bells(due, USER_A), 1, "going live rang the bell")
		self.assertEqual(frappe.db.get_value(ann.DOCTYPE, over.name, "status"), ann.EXPIRED)

	# ── The bell ───────────────────────────────────────────────────────────────────────

	def test_publishing_rings_the_bell_once_and_not_for_the_sender(self):
		doc = _announcement(
			[{"audience_type": "User", "user": USER_A}, {"audience_type": "User", "user": USER_B},
			 {"audience_type": "User", "user": MANAGER}]
		)
		self.assertEqual((_bells(doc, USER_A), _bells(doc, USER_B), _bells(doc, MANAGER)), (1, 1, 0))

		ann.notify_audience(doc.name)  # a retried job
		self.assertEqual((_bells(doc, USER_A), _bells(doc, USER_B)), (1, 1), "a retry never doubles the bell")

		link = frappe.db.get_value("Notification Log", {"for_user": USER_A, "document_name": doc.name}, "link")
		self.assertIn(f"/app/{ann.PAGE}?open={doc.name}", link)

	def test_a_reminder_goes_only_to_people_who_still_have_to_act(self):
		doc = _announcement(
			[{"audience_type": "User", "user": USER_A}, {"audience_type": "User", "user": USER_B}], require_ack=1
		)
		frappe.set_user(USER_A)
		ann.acknowledge(doc.name)
		frappe.set_user(USER_B)
		ann.mark_read(doc.name)  # read, but a rule must also be confirmed
		frappe.set_user("Administrator")

		pending = ann._pending_users(doc, [USER_A, USER_B])
		self.assertEqual(pending, [USER_B])
		sent = ann.notify_audience(doc.name, reminder=1)
		self.assertEqual(sent, 1)
		self.assertEqual(_bells(doc, USER_A), 1)
		self.assertEqual(_bells(doc, USER_B), 2)

	def test_the_reminder_waits_between_two_asks(self):
		doc = _announcement([{"audience_type": "User", "user": USER_A}])
		ann.remind_pending(doc.name)
		with self.assertRaises(frappe.ValidationError):
			ann.remind_pending(doc.name)

	# ── Reading ────────────────────────────────────────────────────────────────────────

	def test_reading_confirming_and_the_counters(self):
		note = _announcement([{"audience_type": "User", "user": USER_A}], title="Note")
		rule = _announcement([{"audience_type": "User", "user": USER_A}], title="Rule", require_ack=1, priority="Urgent")

		frappe.set_user(USER_A)
		counts = ann.summary(USER_A)
		self.assertEqual((counts["unread"], counts["need_ack"], counts["urgent"]), (2, 1, 1))

		counts = ann.mark_read(note.name)
		self.assertEqual(counts["unread"], 1)
		self.assertEqual(
			frappe.db.get_value("Notification Log", {"for_user": USER_A, "document_name": note.name}, "read"),
			1,
			"reading it clears its bell entry",
		)
		counts = ann.acknowledge(rule.name)
		self.assertEqual((counts["unread"], counts["need_ack"], counts["urgent"]), (0, 0, 0))
		ann.acknowledge(rule.name)  # twice is harmless
		self.assertEqual(frappe.db.count(ann.READ_LOG, {"announcement": rule.name, "user": USER_A}), 1)

	def test_nobody_reads_or_confirms_what_is_not_meant_for_them(self):
		doc = _announcement([{"audience_type": "User", "user": USER_A}])
		frappe.set_user(USER_B)
		with self.assertRaises(frappe.PermissionError):
			ann.acknowledge(doc.name)
		with self.assertRaises(frappe.PermissionError):
			ann.get_one(doc.name)
		ann.mark_read(doc.name)  # silently ignored: it is not on their list
		self.assertFalse(frappe.db.exists(ann.READ_LOG, f"{doc.name}::{USER_B}"))
		self.assertEqual([item["name"] for item in ann.get_feed()["items"]], [])

	def test_the_feed_filters_and_orders(self):
		rows = [{"audience_type": "User", "user": USER_A}]
		plain = _announcement(rows, title="Plain")
		pinned = _announcement(rows, title="Pinned", pinned=1)
		report = _announcement(rows, title="A report", kind="Report")
		frappe.set_user(USER_A)

		items = ann.get_feed()["items"]
		self.assertEqual(items[0]["name"], pinned.name, "a pinned update leads")
		self.assertEqual([i["name"] for i in ann.get_feed(kind="Report")["items"]], [report.name])
		self.assertEqual([i["name"] for i in ann.get_feed(search="plai")["items"]], [plain.name])
		ann.mark_read(plain.name)
		self.assertNotIn(plain.name, [i["name"] for i in ann.get_feed(view="unread")["items"]])

	# ── A report is opened only by those who may ───────────────────────────────────────

	def test_the_report_button_appears_only_for_someone_allowed_to_open_it(self):
		report = frappe.get_doc(
			{"doctype": "Report", "report_name": "Ann Test Report", "ref_doctype": "User",
			 "report_type": "Report Builder", "is_standard": "No", "roles": [{"role": ROLE_A}]}
		).insert(ignore_permissions=True)
		doc = _announcement(
			[{"audience_type": "Everyone"}], kind="Report", link_type="Report", link_report=report.name
		)

		real = frappe.has_permission

		def permit_user_reports(doctype, ptype=None, *args, **kwargs):
			return True if (doctype, ptype) == ("User", "report") else real(doctype, ptype, *args, **kwargs)

		with patch.object(frappe, "has_permission", side_effect=permit_user_reports):
			frappe.set_user(USER_A)
			frappe.local.flags.pop("sf_allowed_reports", None)
			link = ann.get_one(doc.name)["link"]
			self.assertTrue(link["can_open"])
			self.assertEqual(link["route"], ["List", "User", "Report", report.name])

			frappe.set_user(USER_B)  # does not hold the report's role
			frappe.local.flags.pop("sf_allowed_reports", None)
			link = ann.get_one(doc.name)["link"]
			self.assertFalse(link["can_open"])
			self.assertIsNone(link["route"])
		frappe.local.flags.pop("sf_allowed_reports", None)

	def test_the_author_is_told_who_could_not_open_the_report(self):
		report = frappe.get_doc(
			{"doctype": "Report", "report_name": "Ann Test Report 2", "ref_doctype": "User",
			 "report_type": "Report Builder", "is_standard": "No", "roles": [{"role": ROLE_A}]}
		).insert(ignore_permissions=True)
		gap = ann._without_report_access(report.name, [USER_A, USER_B])
		self.assertEqual(gap["count"], 1)
		self.assertEqual(gap["roles"], [ROLE_A])

	# ── Saving rules ───────────────────────────────────────────────────────────────────

	def test_an_address_must_be_a_route_or_an_https_address(self):
		for good in ("/app/sf-updates", "https://example.com/a?b=1", "http://example.com"):
			self.assertTrue(ann._safe_url(good), good)
		for bad in ("javascript:alert(1)", "//evil.example.com", "data:text/html,x", "/app/a b", "https://x.com/\"onclick=", ""):
			self.assertFalse(ann._safe_url(bad), bad)

		with self.assertRaises(frappe.ValidationError):
			_announcement(
				[{"audience_type": "Everyone"}], publish=False, link_type="Page or Web Address", link_url="javascript:alert(1)"
			)

	def test_an_audience_row_must_name_who_it_means(self):
		with self.assertRaises(frappe.ValidationError):
			_announcement([{"audience_type": "Role"}], publish=False)

	def test_a_stale_value_does_not_widen_a_row(self):
		doc = _announcement(
			[{"audience_type": "Role", "role": ROLE_A, "user": USER_C}], title="Stale"
		)
		self.assertIsNone(doc.audience[0].user)
		self.assertEqual({u for u in USERS if doc.name in ann.visible_names(u)}, {USER_A})

	def test_hide_after_must_be_later_than_publish_at(self):
		with self.assertRaises(frappe.ValidationError):
			_announcement(
				[{"audience_type": "Everyone"}], publish=False,
				publish_on=add_to_date(now_datetime(), hours=2), expires_on=add_to_date(now_datetime(), hours=1),
			)

	# ── The author's side ──────────────────────────────────────────────────────────────

	def test_only_a_manager_publishes_or_looks_at_reach(self):
		doc = _announcement([{"audience_type": "Everyone"}], publish=False)
		frappe.set_user(USER_A)
		for call in (
			lambda: ann.publish(doc.name),
			lambda: ann.get_reach(doc.name),
			lambda: ann.remind_pending(doc.name),
			lambda: ann.set_state(doc.name, "Archived"),
			lambda: ann.preview_audience("[]"),
			lambda: ann.get_feed(view="posts"),
		):
			with self.assertRaises(frappe.PermissionError):
				call()

	def test_publish_and_take_down_through_the_manager_api(self):
		frappe.set_user("Administrator")
		doc = _announcement([{"audience_type": "User", "user": USER_A}], publish=False)
		result = ann.publish(doc.name)
		self.assertEqual((result["status"], result["audience"]), (ann.PUBLISHED, 1))
		self.assertIn(doc.name, ann.visible_names(USER_A))

		with self.assertRaises(frappe.ValidationError):
			ann.publish(doc.name)  # already out

		self.assertEqual(ann.set_state(doc.name, "Archived"), ann.ARCHIVED)
		self.assertNotIn(doc.name, ann.visible_names(USER_A))
		self.assertEqual(ann.set_state(doc.name, "Draft"), ann.DRAFT)
		self.assertIsNone(frappe.db.get_value(ann.DOCTYPE, doc.name, "notified_on"), "sent again from scratch")

	def test_a_future_publish_time_schedules_instead_of_sending(self):
		doc = _announcement(
			[{"audience_type": "User", "user": USER_A}], publish=False, publish_on=add_to_date(now_datetime(), hours=2)
		)
		self.assertEqual(ann.publish(doc.name)["status"], ann.SCHEDULED)
		self.assertNotIn(doc.name, ann.visible_names(USER_A))
		self.assertEqual(_bells(doc, USER_A), 0)

	def test_publishing_to_nobody_is_refused(self):
		_ensure_role("Ann Test Role Nobody Holds")
		doc = _announcement([{"audience_type": "Role", "role": "Ann Test Role Nobody Holds"}], publish=False)
		with self.assertRaises(frappe.ValidationError):
			ann.publish(doc.name)

	def test_reach_counts_who_has_read_and_confirmed(self):
		doc = _announcement(
			[{"audience_type": "User", "user": USER_A}, {"audience_type": "User", "user": USER_B}], require_ack=1
		)
		frappe.set_user(USER_A)
		ann.acknowledge(doc.name)
		frappe.set_user("Administrator")

		reach = ann.get_reach(doc.name)
		self.assertEqual((reach["total"], reach["seen"], reach["acknowledged"]), (2, 1, 1))
		self.assertEqual([p["user"] for p in reach["pending"]], [USER_B])
		self.assertEqual(ann.get_feed(view="posts")["items"][0]["reach"]["seen"], 1)

	def test_widening_a_published_audience_tells_only_the_new_people(self):
		doc = _announcement([{"audience_type": "User", "user": USER_A}])
		self.assertEqual((_bells(doc, USER_A), _bells(doc, USER_B)), (1, 0))

		doc = frappe.get_doc(ann.DOCTYPE, doc.name)
		doc.append("audience", {"audience_type": "User", "user": USER_B})
		doc.save(ignore_permissions=True)
		self.assertEqual((_bells(doc, USER_A), _bells(doc, USER_B)), (1, 1), "only the person added is told")
		self.assertIn(doc.name, ann.visible_names(USER_B))

		doc.title = "Edited title"  # an edit that widens nothing rings nothing
		doc.save(ignore_permissions=True)
		self.assertEqual((_bells(doc, USER_A), _bells(doc, USER_B)), (1, 1))

	def test_a_draft_that_gains_people_is_not_announced_before_it_is_published(self):
		doc = _announcement([{"audience_type": "User", "user": USER_A}], publish=False)
		doc.append("audience", {"audience_type": "User", "user": USER_B})
		doc.save(ignore_permissions=True)
		self.assertEqual((_bells(doc, USER_A), _bells(doc, USER_B)), (0, 0))

	def test_the_audience_line_says_who_it_is_for(self):
		doc = _announcement(
			[{"audience_type": "Role", "role": ROLE_A}, {"audience_type": "Branch", "branch": "Ann Test Branch"},
			 {"audience_type": "User", "user": USER_C}]
		)
		frappe.set_user(USER_A)
		self.assertEqual(ann.get_one(doc.name)["audience"], f"{ROLE_A}, Ann Test Branch branch, 1 named people")

	def test_the_preview_cleans_the_message_the_way_readers_get_it(self):
		cleaned = ann.sanitize_preview("<p>Hello</p><script>alert(1)</script>")
		self.assertIn("Hello", cleaned)
		self.assertNotIn("<script", cleaned)
		frappe.set_user(USER_A)
		with self.assertRaises(frappe.PermissionError):
			ann.sanitize_preview("<p>x</p>")

	def test_the_boot_payload_is_for_staff_and_never_raises(self):
		frappe.set_user(USER_A)
		boot = frappe._dict()
		ann.boot_session(boot)
		self.assertEqual(set(boot.sf_announcements) >= {"unread", "need_ack", "urgent", "can_post", "page"}, True)
		self.assertFalse(boot.sf_announcements["can_post"])

		frappe.set_user("Guest")
		boot = frappe._dict()
		ann.boot_session(boot)
		self.assertNotIn("sf_announcements", boot)

	def test_the_workspace_is_built_with_every_shortcut(self):
		ann.ensure_workspace()
		doc = frappe.get_doc("Workspace", ann.WORKSPACE)
		self.assertEqual({row.label for row in doc.shortcuts}, {row["label"] for row in ann.SHORTCUTS})
		self.assertTrue(ann._shortcut_blocks(doc.content) >= {row["label"] for row in ann.SHORTCUTS})
