"""Tests for the daily motivation: which message a person is meant to see, and that they see it once a day.

    bench --site <scratch-site> run-tests --module sf_trading.tests.test_motivation

Everything runs inside the class's own transaction and is rolled back. The message bank is emptied at
the start of each test (inside that transaction), so a site's real messages never change the outcome.
"""

from contextlib import contextmanager
from datetime import timedelta
from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import add_days, getdate, nowdate

from sf_trading import announcements as ann
from sf_trading import motivation as mot

ROLE_A = "Mot Test Role A"
ROLE_B = "Mot Test Role B"
USER_A = "mot-test-a@example.com"
USER_B = "mot-test-b@example.com"
MANAGER = "mot-test-manager@example.com"


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
			"first_name": email.split("@")[0].replace("mot-test-", "").title(),
			"send_welcome_email": 0,
			"roles": [{"role": role}],
		}
	).insert(ignore_permissions=True)


def _message(text, **fields):
	doc = frappe.get_doc({"doctype": mot.DOCTYPE, "message": text, "enabled": 1, **fields})
	doc.insert(ignore_permissions=True)
	return doc


@contextmanager
def _settings(**changes):
	values = frappe._dict(enabled=1, rotation=mot.IN_ORDER, card_title="Thought for today", show_in_hub=1)
	values.update(changes)
	with patch.object(mot, "settings", return_value=values):
		yield


class TestMotivation(FrappeTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		_ensure_role(ROLE_A)
		_ensure_role(ROLE_B)
		_make_user(USER_A, ROLE_A)
		_make_user(USER_B, ROLE_B)
		_make_user(MANAGER, ann.MANAGER_ROLE)

	def setUp(self):
		# nothing in a test may commit, so a real site's data always rolls back
		patcher = patch.object(frappe.db, "commit")
		patcher.start()
		self.addCleanup(patcher.stop)
		# inside this class's transaction: nothing real is lost, and nothing real leaks into the test
		frappe.db.delete(ann.AUDIENCE, {"parenttype": mot.DOCTYPE})
		frappe.db.delete(mot.DOCTYPE)
		frappe.db.delete(mot.SEEN)

	def tearDown(self):
		frappe.set_user("Administrator")

	# ── Choosing the day's message ─────────────────────────────────────────────────────

	def test_undated_messages_take_turns_one_a_day(self):
		bank = [_message(f"Message {n}") for n in range(3)]
		names = [doc.name for doc in bank]
		day = getdate(nowdate())
		with _settings():
			picked = [mot.todays_message(USER_A, day + timedelta(days=offset)).name for offset in range(6)]
			self.assertEqual(picked[:3], [names[(day.toordinal() + offset) % 3] for offset in range(3)])
			self.assertEqual(picked[:3], picked[3:], "it cycles through the whole bank, then starts again")
			self.assertEqual(len(set(picked[:3])), 3, "every message gets its day")
			self.assertEqual(mot.todays_message(USER_A, day).name, mot.todays_message(USER_A, day).name)

	def test_a_message_dated_today_wins_and_stays_out_of_the_rotation(self):
		plain = [_message("Plain one"), _message("Plain two")]
		today = getdate(nowdate())
		dated = _message("For this day only", show_on=today)
		with _settings():
			self.assertEqual(mot.todays_message(USER_A, today).name, dated.name)
			tomorrow = mot.todays_message(USER_A, today + timedelta(days=1))
			self.assertIn(tomorrow.name, {doc.name for doc in plain}, "the dated one is for its own day only")

	def test_a_message_switched_off_is_never_chosen(self):
		off = _message("Off", enabled=0)
		on = _message("On")
		with _settings():
			for offset in range(5):
				self.assertEqual(mot.todays_message(USER_A, getdate(nowdate()) + timedelta(days=offset)).name, on.name)
		self.assertNotEqual(off.name, on.name)

	def test_random_is_the_same_all_day_for_everyone(self):
		for n in range(5):
			_message(f"Random {n}")
		day = getdate(nowdate())
		with _settings(rotation=mot.RANDOM):
			first = mot.todays_message(USER_A, day).name
			self.assertEqual(first, mot.todays_message(USER_A, day).name)
			self.assertEqual(first, mot.todays_message(USER_B, day).name, "the same pool gives the same draw")

	def test_a_message_limited_to_a_role_is_offered_to_that_role_only(self):
		_message("For role A", audience=[{"audience_type": "Role", "role": ROLE_A}])
		with _settings():
			self.assertIsNotNone(mot.todays_message(USER_A))
			self.assertIsNone(mot.todays_message(USER_B))

		everyone = _message("For all")  # no audience lines at all means everyone
		with _settings():
			self.assertEqual(mot.todays_message(USER_B).name, everyone.name)

	def test_nothing_to_show_is_none(self):
		with _settings():
			self.assertIsNone(mot.todays_message(USER_A))
			self.assertIsNone(mot.claim(USER_A))

	# ── Once a day ─────────────────────────────────────────────────────────────────────

	def test_the_card_comes_with_the_first_load_of_the_day_and_not_again(self):
		first = _message("Only message")
		with _settings():
			payload = mot.claim(USER_A)
			self.assertEqual((payload["name"], payload["message"], payload["title"]), (first.name, "Only message", "Thought for today"))

			self.assertIsNone(mot.claim(USER_A), "a refresh, or a second tab, does not bring it back")
			self.assertIsNone(mot.claim(USER_A))
			self.assertIsNotNone(mot.claim(USER_B), "one person's day is not another's")
		self.assertEqual(frappe.db.count(mot.SEEN, {"user": USER_A}), 1)

	def test_the_whitelisted_call_hands_it_out_once_for_the_signed_in_person(self):
		_message("Via the desk")
		with _settings():
			frappe.set_user(USER_A)
			self.assertEqual(mot.claim_motivation()["message"], "Via the desk")
			self.assertIsNone(mot.claim_motivation())

	def test_an_old_browser_marking_it_seen_after_the_claim_counts_nothing_twice(self):
		only = _message("Old script")
		with _settings():
			mot.claim(USER_A)
			frappe.set_user(USER_A)
			self.assertEqual(mot.mark_motivation_seen(only.name), {"first": 0})
		self.assertEqual(frappe.db.count(mot.SEEN, {"user": USER_A}), 1)

	def test_my_updates_keeps_the_message_the_person_was_shown(self):
		shown = _message("The one I saw")
		with _settings():
			frappe.set_user(USER_A)
			mot.mark_motivation_seen(shown.name)
			frappe.set_user("Administrator")

			# the bank changes later that day: a message dated today now takes the day's slot for new loads
			_message("Added later", show_on=getdate(nowdate()))
			self.assertNotEqual(mot.todays_message(USER_A).name, shown.name)

			frappe.set_user(USER_A)
			self.assertEqual(mot.get_motivation()["message"], "The one I saw")

	def test_the_switch_turns_the_whole_thing_off(self):
		_message("Anything")
		with _settings(enabled=0):
			self.assertIsNone(mot.claim(USER_A))
			frappe.set_user(USER_A)
			self.assertIsNone(mot.get_motivation())

	def test_marking_a_message_that_does_not_exist_is_refused(self):
		frappe.set_user(USER_A)
		with self.assertRaises(frappe.DoesNotExistError):
			mot.mark_motivation_seen("MOT-NOPE")

	def test_the_boot_payload_no_longer_carries_the_message(self):
		# Frappe caches the boot payload per person, so anything in it comes back on every refresh
		_message("Not in boot")
		with _settings():
			frappe.set_user(USER_A)
			boot = frappe._dict()
			ann.boot_session(boot)
			self.assertNotIn("motivation", boot.sf_announcements)

	# ── Filling and keeping the bank ───────────────────────────────────────────────────

	def test_pasting_many_lines_adds_each_and_skips_what_is_already_there(self):
		result = mot.add_motivations(
			"- Small steps add up. — Sayanth\n"
			"2. Thank someone today.\n"
			"\n"
			"Be on time | Everyone\n"
			"small steps add up. — Someone else\n"  # the same message, whatever its case or author
			+ "x" * 300
		)
		self.assertEqual(result, {"added": 3, "duplicates": 1, "too_long": 1})
		self.assertEqual(frappe.db.get_value(mot.DOCTYPE, {"message": "Small steps add up."}, "author"), "Sayanth")
		self.assertEqual(frappe.db.get_value(mot.DOCTYPE, {"message": "Thank someone today."}, "author"), None)
		self.assertEqual(frappe.db.get_value(mot.DOCTYPE, {"message": "Be on time"}, "author"), "Everyone")

		self.assertEqual(mot.add_motivations("Thank someone today.")["duplicates"], 1, "pasting twice does no harm")

	def test_only_a_manager_adds_in_bulk(self):
		frappe.set_user(USER_A)
		with self.assertRaises(frappe.PermissionError):
			mot.add_motivations("Hello")

	def test_a_message_must_be_short_and_its_audience_complete(self):
		with self.assertRaises(frappe.ValidationError):
			_message("x" * (mot.MESSAGE_MAX + 1))
		with self.assertRaises(frappe.ValidationError):
			_message("Needs a role", audience=[{"audience_type": "Role"}])

	def test_only_the_last_two_months_of_seen_rows_are_kept(self):
		old = frappe.get_doc(
			{"doctype": mot.SEEN, "user": USER_A, "on_date": add_days(nowdate(), -(mot.KEEP_SEEN_DAYS + 5)), "motivation": "x"}
		).insert(ignore_permissions=True)
		recent = frappe.get_doc(
			{"doctype": mot.SEEN, "user": USER_B, "on_date": add_days(nowdate(), -3), "motivation": "x"}
		).insert(ignore_permissions=True)
		mot.prune_seen()
		self.assertFalse(frappe.db.exists(mot.SEEN, old.name))
		self.assertTrue(frappe.db.exists(mot.SEEN, recent.name))

	def test_the_workspace_has_the_motivation_shortcuts(self):
		labels = {row["label"] for row in ann.SHORTCUTS}
		self.assertTrue({"Daily Motivation", "Motivation Settings"} <= labels)
