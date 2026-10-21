# sf_trading/tests/test_ticket_auto_close_and_efficiency.py
"""A ticket that stays Resolved closes by itself, and the Ticket Closing Efficiency report ranks who resolves and
who closes (sf_trading/ticket.py, the Ticket controller, sf_trading/report/ticket_closing_efficiency).

    bench --site <site> run-tests --module sf_trading.tests.test_ticket_auto_close_and_efficiency

Everything is patched at the modules' own seams (and the report's builders take plain tickets), so nothing
is created, read or written.
"""

import json
import os
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

import frappe
from frappe.tests.utils import FrappeTestCase

from sf_trading import ticket
from sf_trading.sf_trading.doctype.ticket.ticket import Ticket
from sf_trading.sf_trading.report.ticket_closing_efficiency import ticket_closing_efficiency as report

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
NOW = datetime(2026, 10, 20, 12, 0, 0)


def read(*parts):
	with open(os.path.join(ROOT, *parts), encoding="utf-8") as f:
		return f.read()


# ── the wait ────────────────────────────────────────────────────────────────────────

class TestTheWait(FrappeTestCase):
	def days(self, row):
		with patch.object(ticket.frappe.db, "get_singles_dict", return_value=row, create=True):
			return ticket.auto_close_days()

	def test_five_days_until_someone_says_otherwise(self):
		for row in ({}, {ticket.AUTO_CLOSE_FIELD: None}, {ticket.AUTO_CLOSE_FIELD: ""}):
			self.assertEqual(self.days(row), 5, row)

	def test_the_settings_value_is_used_and_zero_switches_it_off(self):
		self.assertEqual(self.days({ticket.AUTO_CLOSE_FIELD: "7"}), 7)
		self.assertEqual(self.days({ticket.AUTO_CLOSE_FIELD: 3}), 3)
		self.assertEqual(self.days({ticket.AUTO_CLOSE_FIELD: "0"}), 0)
		self.assertEqual(self.days({ticket.AUTO_CLOSE_FIELD: "-2"}), 0)

	def test_the_value_is_not_read_through_get_single_value(self):
		# a field never saved reads 0 there, which would mean "switched off" on every site not yet saved
		with patch.object(ticket.frappe.db, "get_single_value", side_effect=AssertionError("reads a missing field as 0"), create=True), \
				patch.object(ticket.frappe.db, "get_singles_dict", return_value={}, create=True):
			self.assertEqual(ticket.auto_close_days(), 5)

	def test_the_query_looks_for_resolved_tickets_older_than_the_cutoff_oldest_first(self):
		sql = ticket.due_query(NOW - timedelta(days=5)).get_sql()
		for needle in ("`status`='Resolved'", "COALESCE(`resolved_on`,`modified`)<='2026-10-15", "ORDER BY COALESCE(`resolved_on`,`modified`)", "LIMIT %d" % ticket.AUTO_CLOSE_BATCH):
			self.assertIn(needle, sql)


class TestTheNightlyJob(FrappeTestCase):
	def run_job(self, days=5, names=("TKT-1", "TKT-2"), closer=None):
		query = MagicMock()
		query.run.return_value = list(names)
		close = closer or MagicMock(return_value=True)
		with patch.object(ticket, "auto_close_days", return_value=days), \
				patch.object(ticket.frappe.db, "table_exists", return_value=True, create=True), \
				patch.object(ticket, "due_query", return_value=query) as due, \
				patch.object(ticket, "close_after_wait", close), \
				patch.object(ticket.frappe.db, "commit", create=True) as commit, \
				patch.object(ticket.frappe.db, "rollback", create=True) as rollback, \
				patch.object(ticket.frappe, "log_error", create=True) as log:
			result = ticket.close_resolved_tickets()
		return result, close, due, commit, rollback, log

	def test_each_due_ticket_is_closed_and_committed_on_its_own(self):
		result, close, due, commit, _rollback, _log = self.run_job()
		self.assertEqual(result, 2)
		self.assertEqual([c.args for c in close.call_args_list], [("TKT-1", 5), ("TKT-2", 5)])
		self.assertEqual(commit.call_count, 2)

	def test_a_site_with_no_ticket_table_is_left_alone(self):
		with patch.object(ticket, "auto_close_days", return_value=5), \
				patch.object(ticket.frappe.db, "table_exists", return_value=False, create=True), \
				patch.object(ticket, "due_query", side_effect=AssertionError("no table to read")):
			self.assertEqual(ticket.close_resolved_tickets(), 0)

	def test_switched_off_it_looks_at_nothing(self):
		result, close, due, commit, _r, _l = self.run_job(days=0)
		self.assertEqual(result, 0)
		due.assert_not_called()
		close.assert_not_called()

	def test_one_failure_is_logged_and_the_rest_still_close(self):
		def close(name, days):
			if name == "TKT-1":
				raise RuntimeError("boom")
			return True

		result, _c, _d, commit, rollback, log = self.run_job(names=("TKT-1", "TKT-2", "TKT-3"), closer=close)
		self.assertEqual(result, 2)
		rollback.assert_called_once()
		log.assert_called_once()
		self.assertIn("TKT-1", log.call_args.args[1])

	def test_a_ticket_reopened_since_the_list_was_read_is_not_counted(self):
		result, *_ = self.run_job(closer=MagicMock(side_effect=[False, True]))
		self.assertEqual(result, 1)

	def test_the_cutoff_is_the_wait_before_now(self):
		query = MagicMock()
		query.run.return_value = []
		with patch.object(ticket, "auto_close_days", return_value=5), patch.object(ticket, "now_datetime", return_value=NOW), \
				patch.object(ticket.frappe.db, "table_exists", return_value=True, create=True), \
				patch.object(ticket, "due_query", return_value=query) as due:
			ticket.close_resolved_tickets()
		self.assertEqual(due.call_args.args[0], NOW - timedelta(days=5))


class FakeTicket(frappe._dict):
	"""A Ticket document: attributes, flags, and a save that records how it was called."""

	def __init__(self, **values):
		super().__init__(status=values.pop("status", "Resolved"), **values)
		self["flags"] = frappe._dict()
		self["saved"] = []

	def save(self, **kwargs):
		self["saved"].append(kwargs)


class TestClosingOne(FrappeTestCase):
	def close(self, doc):
		note = MagicMock()
		with patch.object(ticket.frappe, "get_doc", return_value=doc, create=True), patch.object(ticket, "_post_note", note):
			return ticket.close_after_wait("TKT-1", 5), note

	def test_it_closes_as_the_system_with_the_ticket_rules_set_aside(self):
		doc = FakeTicket()
		done, note = self.close(doc)
		self.assertTrue(done)
		self.assertEqual(doc.status, ticket.CLOSED)
		self.assertTrue(doc.flags.auto_closed)
		self.assertTrue(doc.flags.ignore_ticket_rules)
		self.assertEqual(doc.saved, [{"ignore_permissions": True}])
		self.assertIn("5 days", doc.flags.status_note)
		note.assert_called_once()
		self.assertTrue(note.call_args.kwargs["quiet"])  # the status notice already carries the words

	def test_a_ticket_that_is_no_longer_resolved_is_left_alone(self):
		for status in (ticket.OPEN, ticket.IN_PROGRESS, ticket.ON_HOLD, ticket.CLOSED):
			doc = FakeTicket(status=status)
			done, note = self.close(doc)
			self.assertFalse(done, status)
			self.assertEqual((doc.status, doc.saved), (status, []))
			note.assert_not_called()


class TestTheNotice(FrappeTestCase):
	def notice(self, status, previous, auto=False):
		doc = FakeTicket(status=status, name="TKT-1", subject="Subject", resolution_details="")
		doc.flags.auto_closed = auto
		notify = MagicMock()
		with patch.object(ticket, "notify", notify), patch.object(ticket, "involved_users", return_value={"a@example.com"}), \
				patch.object(ticket, "_set_assignments"), patch.object(ticket, "_who", return_value="Someone"), \
				patch.object(ticket.frappe, "session", frappe._dict(user="Administrator"), create=True):
			ticket._on_status_change(doc, previous)
		return notify.call_args.args[1]

	def test_an_automatic_close_says_so_instead_of_naming_the_system_user(self):
		message = self.notice(ticket.CLOSED, ticket.RESOLVED, auto=True)
		self.assertIn("closed automatically", message)
		self.assertNotIn("Someone", message)

	def test_a_close_by_a_person_still_names_them(self):
		self.assertIn("Someone closed", self.notice(ticket.CLOSED, ticket.RESOLVED))


# ── the controller's own clock ──────────────────────────────────────────────────────

class TestTheTicketClock(FrappeTestCase):
	def track(self, status, before_status, user="reporter@example.com", auto=False, **extra):
		doc = FakeTicket(status=status, raised_by="reporter@example.com", owner="reporter@example.com", opening_date=NOW - timedelta(days=6),
			reopen_count=0, first_response_on=NOW - timedelta(days=5), closed_by=None, auto_closed=0, resolved_by=None, **extra)
		doc.flags.auto_closed = auto
		before = frappe._dict(status=before_status, first_response_on=doc.first_response_on)
		with patch("sf_trading.sf_trading.doctype.ticket.ticket.now_datetime", return_value=NOW), \
				patch("sf_trading.sf_trading.doctype.ticket.ticket.frappe.session", frappe._dict(user=user), create=True):
			Ticket._set_tracking(doc, before)
		return doc

	def test_a_person_closing_is_recorded_as_the_closer(self):
		doc = self.track(ticket.CLOSED, ticket.RESOLVED, user="reporter@example.com")
		self.assertEqual((doc.closed_by, doc.auto_closed, doc.closed_on), ("reporter@example.com", 0, NOW))

	def test_the_system_closing_has_no_closer_and_is_marked(self):
		doc = self.track(ticket.CLOSED, ticket.RESOLVED, user="Administrator", auto=True)
		self.assertEqual((doc.closed_by, doc.auto_closed, doc.closed_on), (None, 1, NOW))

	def test_the_automatic_close_is_not_a_first_response(self):
		doc = self.track(ticket.CLOSED, ticket.RESOLVED, user="Administrator", auto=True)
		doc.first_response_on = None
		before = frappe._dict(status=ticket.RESOLVED, first_response_on=None)
		doc.flags.auto_closed = True
		with patch("sf_trading.sf_trading.doctype.ticket.ticket.now_datetime", return_value=NOW), \
				patch("sf_trading.sf_trading.doctype.ticket.ticket.frappe.session", frappe._dict(user="Administrator"), create=True):
			Ticket._set_tracking(doc, before)
		self.assertIsNone(doc.first_response_on)

	def test_a_reopen_clears_who_closed_it_and_whether_the_system_did(self):
		doc = FakeTicket(status=ticket.OPEN, raised_by="reporter@example.com", owner="reporter@example.com", opening_date=NOW - timedelta(days=6),
			reopen_count=0, first_response_on=NOW - timedelta(days=5), closed_by="someone@example.com", auto_closed=1,
			resolved_by="worker@example.com", resolved_on=NOW - timedelta(days=5), closed_on=NOW - timedelta(days=1))
		before = frappe._dict(status=ticket.CLOSED, first_response_on=doc.first_response_on)
		with patch("sf_trading.sf_trading.doctype.ticket.ticket.now_datetime", return_value=NOW), \
				patch("sf_trading.sf_trading.doctype.ticket.ticket.frappe.session", frappe._dict(user="reporter@example.com"), create=True):
			Ticket._set_tracking(doc, before)
		self.assertEqual((doc.closed_by, doc.auto_closed, doc.closed_on, doc.resolved_by, doc.resolved_on, doc.reopen_count), (None, 0, None, None, None, 1))

	def test_resolving_still_records_the_resolver_and_the_time_it_took(self):
		doc = self.track(ticket.RESOLVED, ticket.IN_PROGRESS, user="worker@example.com")
		self.assertEqual((doc.resolved_by, doc.resolved_on, doc.resolution_time), ("worker@example.com", NOW, 6 * 86400))


# ── the report ──────────────────────────────────────────────────────────────────────

def done(resolver="a@example.com", status="Closed", hours=2.0, first=0.5, reopen=0, auto=0, priority="Medium", closed_by=None, closed_after=None):
	opened = NOW - timedelta(days=30)
	resolved_on = opened + timedelta(hours=hours)
	return frappe._dict(
		name="TKT-X", status=status, priority=priority, ticket_type="Error / Bug", module_area="Selling", assignee=resolver,
		resolved_by=resolver, closed_by=closed_by, auto_closed=auto, opening_date=opened,
		first_response_on=opened + timedelta(hours=first) if first is not None else None, resolved_on=resolved_on,
		closed_on=resolved_on + timedelta(days=closed_after) if closed_after is not None else None,
		resolution_time=hours * 3600, reopen_count=reopen,
	)


class TestResolvers(FrappeTestCase):
	def rows(self, tickets, backlog=None):
		return report.resolver_rows(tickets, backlog or {}, {"a@example.com": "Aaa", "b@example.com": "Bbb"})

	def test_most_resolved_leads_then_the_quicker_then_the_one_with_fewer_reopens(self):
		rows = self.rows([done("a@example.com", hours=4)] * 3 + [done("b@example.com", hours=1)] * 3 + [done("c@example.com", hours=1)])
		self.assertEqual([r["user"] for r in rows], ["b@example.com", "a@example.com", "c@example.com"])
		self.assertEqual([r["rank"] for r in rows], [1, 2, 3])

	def test_the_counts_and_rates(self):
		tickets = [done(status="Closed", reopen=0), done(status="Closed", auto=1, reopen=1), done(status="Resolved", hours=30, priority="Urgent"), done(status="Resolved", priority="High", reopen=2)]
		(row,) = self.rows(tickets, {"a@example.com": 6})
		self.assertEqual((row["resolved"], row["closed"], row["auto_closed"], row["waiting"]), (4, 2, 1, 2))
		self.assertEqual((row["reopened"], row["reopen_pct"]), (2, 50.0))
		self.assertEqual((row["urgent_high"], row["open_now"]), (2, 6))
		self.assertEqual(row["within_24h"], 75.0)  # one of the four took 30 hours
		self.assertEqual((row["fastest"], row["slowest"]), (2.0, 30.0))
		self.assertEqual(row["avg_resolution"], 9.0)
		self.assertEqual(row["avg_first_response"], 0.5)
		self.assertEqual(row["full_name"], "Aaa")

	def test_missing_times_leave_blanks_not_zeros(self):
		(row,) = self.rows([done(first=None, hours=0)])
		self.assertIsNone(row["avg_first_response"])
		self.assertIsNone(row["avg_resolution"])
		self.assertIsNone(row["fastest"])
		self.assertIsNone(row["within_24h"])

	def test_a_resolver_with_no_name_on_file_shows_their_id(self):
		(row,) = report.resolver_rows([done("zed@example.com")], {}, {})
		self.assertEqual(row["full_name"], "zed@example.com")

	def test_no_tickets_no_rows(self):
		self.assertEqual(self.rows([]), [])


class TestClosers(FrappeTestCase):
	def rows(self, tickets):
		return report.closer_rows(tickets, {"a@example.com": "Aaa"})

	def test_people_the_system_and_unrecorded_closes_are_ranked_together(self):
		tickets = [done(closed_by="a@example.com", closed_after=0.5)] * 2 + [done(auto=1, closed_after=5)] * 3 + [done(closed_by=None, closed_after=1)]
		rows = self.rows(tickets)
		self.assertEqual([(r["closer"], r["closed"]) for r in rows], [(report.AUTO_LABEL, 3), ("a@example.com", 2), (report.UNKNOWN_LABEL, 1)])
		by = {r["closer"]: r for r in rows}
		self.assertEqual(by["a@example.com"]["full_name"], "Aaa")
		self.assertEqual((by["a@example.com"]["avg_days_to_close"], by["a@example.com"]["same_day"]), (0.5, 2))
		self.assertEqual(by[report.AUTO_LABEL]["avg_days_to_close"], 5.0)
		self.assertEqual(by[report.AUTO_LABEL]["same_day"], 0)

	def test_a_ticket_closed_without_being_resolved_is_counted_as_such(self):
		t = done(closed_by="a@example.com")
		t.resolved_on = None
		t.closed_on = NOW
		(row,) = self.rows([t])
		self.assertEqual(row["without_resolving"], 1)
		self.assertIsNone(row["avg_days_to_close"])


class TestChartAndSummary(FrappeTestCase):
	def test_the_chart_is_the_top_ten_by_the_ranked_column(self):
		rows = [{"user": "u%d" % i, "full_name": "U%d" % i, "resolved": 20 - i} for i in range(12)]
		chart = report._chart(rows, "Resolved", "resolved")
		self.assertEqual(len(chart["data"]["labels"]), 10)
		self.assertEqual(chart["data"]["datasets"][0]["values"][:2], [20, 19])
		self.assertIsNone(report._chart([], "Resolved", "resolved"))

	def test_the_resolver_summary(self):
		tickets = [done(status="Closed"), done(status="Closed", auto=1, reopen=1), done(status="Resolved"), done(status="Resolved")]
		cards = {c["label"]: c["value"] for c in report._summary(tickets, [], False)}
		self.assertEqual((cards["Resolved"], cards["Closed"], cards["Closed Automatically"], cards["Reopen %"]), (4, 2, 1, 25.0))

	def test_the_closer_summary_separates_people_from_the_system(self):
		rows = report.closer_rows([done(closed_by="a@example.com", closed_after=1), done(auto=1, closed_after=5), done(auto=1, closed_after=5)], {"a@example.com": "Aaa"})
		cards = {c["label"]: c["value"] for c in report._summary([], rows, True)}
		self.assertEqual((cards["Closed"], cards["Closed By A Person"], cards["Closed Automatically"], cards["People Closing"]), (3, 1, 2, 1))


class TestWhoMayOpenIt(FrappeTestCase):
	def test_only_a_ticket_manager_may_rank_people(self):
		with patch.object(report.ticket, "is_manager", return_value=False):
			with self.assertRaises(frappe.PermissionError):
				report.execute({})

	def test_a_manager_gets_columns_rows_chart_and_summary(self):
		with patch.object(report.ticket, "is_manager", return_value=True), \
				patch.object(report, "_finished", return_value=[done()]), patch.object(report, "_backlog", return_value={}), \
				patch.object(report, "_full_names", return_value={"a@example.com": "Aaa"}):
			columns, rows, message, chart, summary = report.execute({})
			self.assertEqual((len(rows), message), (1, None))
			self.assertTrue(chart and summary)
			self.assertIn("avg_resolution", [c["fieldname"] for c in columns])
			columns, rows, *_ = report.execute({"view": "Closers"})
			self.assertIn("avg_days_to_close", [c["fieldname"] for c in columns])


# ── what ships ──────────────────────────────────────────────────────────────────────

class TestWhatTheSystemShips(FrappeTestCase):
	def hooks(self):
		return read("hooks.py")

	def test_the_job_runs_daily(self):
		text = self.hooks()
		daily = text[text.rindex("\nscheduler_events = {"):]  # the live one, not the commented template at the top
		self.assertLess(daily.index('"daily"'), daily.index("sf_trading.ticket.close_resolved_tickets"))
		self.assertLess(daily.index("sf_trading.ticket.close_resolved_tickets"), daily.index('"all"'))

	def test_the_setting_is_five_days_and_not_negative(self):
		fields = {f["fieldname"]: f for f in json.loads(read("sf_trading", "doctype", "sf_trading_settings", "sf_trading_settings.json"))["fields"]}
		f = fields[ticket.AUTO_CLOSE_FIELD]
		self.assertEqual((f["fieldtype"], f["default"], f["non_negative"]), ("Int", "5", 1))
		order = json.loads(read("sf_trading", "doctype", "sf_trading_settings", "sf_trading_settings.json"))["field_order"]
		self.assertEqual(order[order.index("tickets_section") + 1], ticket.AUTO_CLOSE_FIELD)

	def test_the_ticket_carries_who_closed_it(self):
		doc = json.loads(read("sf_trading", "doctype", "ticket", "ticket.json"))
		fields = {f["fieldname"]: f for f in doc["fields"]}
		self.assertEqual((fields["closed_by"]["fieldtype"], fields["closed_by"]["options"], fields["closed_by"]["read_only"]), ("Link", "User", 1))
		self.assertEqual((fields["auto_closed"]["fieldtype"], fields["auto_closed"]["read_only"]), ("Check", 1))
		order = doc["field_order"]
		self.assertEqual(order[order.index("closed_on") + 1:order.index("closed_on") + 3], ["closed_by", "auto_closed"])

	def test_the_report_is_for_ticket_managers_and_is_on_the_workspace(self):
		meta = json.loads(read("sf_trading", "report", "ticket_closing_efficiency", "ticket_closing_efficiency.json"))
		self.assertEqual(({r["role"] for r in meta["roles"]}, meta["ref_doctype"], meta["report_type"]), ({"Ticket Manager", "System Manager"}, "Ticket", "Script Report"))
		self.assertIn(ticket.EFFICIENCY_REPORT, [row["label"] for row in ticket.SHORTCUTS])
		self.assertEqual(meta["name"], ticket.EFFICIENCY_REPORT)
		for ext in ("py", "js"):
			self.assertTrue(os.path.exists(os.path.join(ROOT, "sf_trading", "report", "ticket_closing_efficiency", "ticket_closing_efficiency." + ext)))

	def test_the_backfill_patch_is_registered_after_the_model_sync(self):
		lines = read("patches.txt").splitlines()
		self.assertGreater(lines.index("sf_trading.patches.v0_1.ticket_closed_by_backfill"), lines.index("[post_model_sync]"))


class TestTheBackfill(FrappeTestCase):
	def run_patch(self, todo, versions):
		from sf_trading.patches.v0_1 import ticket_closed_by_backfill as patch_module

		set_value = MagicMock()
		with patch.object(patch_module.frappe, "get_all", return_value=todo, create=True) as get_all, \
				patch.object(patch_module.frappe.db, "sql", return_value=versions, create=True), \
				patch.object(patch_module.frappe.db, "set_value", set_value, create=True):
			patch_module.execute()
		return set_value, get_all

	def version(self, ticket_name, owner, *changes):
		return frappe._dict(docname=ticket_name, owner=owner, data=json.dumps({"changed": [list(c) for c in changes]}))

	def test_a_closed_ticket_gets_the_owner_of_the_version_that_closed_it(self):
		set_value, _ = self.run_patch(["TKT-1"], [self.version("TKT-1", "reporter@example.com", ("status", "Resolved", "Closed"))])
		set_value.assert_called_once_with("Ticket", "TKT-1", "closed_by", "reporter@example.com", update_modified=False)

	def test_the_latest_close_wins_when_a_ticket_was_reopened_and_closed_again(self):
		versions = [
			self.version("TKT-1", "first@example.com", ("status", "Resolved", "Closed")),
			self.version("TKT-1", "x@example.com", ("status", "Closed", "Open")),
			self.version("TKT-1", "second@example.com", ("status", "Resolved", "Closed")),
		]
		set_value, _ = self.run_patch(["TKT-1"], versions)
		self.assertEqual(set_value.call_args.args[3], "second@example.com")

	def test_a_ticket_with_no_such_version_is_left_blank_and_other_changes_do_not_count(self):
		versions = [self.version("TKT-1", "a@example.com", ("priority", "Low", "High")), self.version("TKT-2", "b@example.com", ("status", "Open", "Resolved"))]
		set_value, _ = self.run_patch(["TKT-1", "TKT-2"], versions)
		set_value.assert_not_called()

	def test_it_reads_only_closed_tickets_with_no_closer_that_the_system_did_not_close(self):
		_, get_all = self.run_patch([], [])
		self.assertEqual(get_all.call_args.kwargs["filters"], {"status": "Closed", "closed_by": ["is", "not set"], "auto_closed": 0})

	def test_a_damaged_version_row_is_skipped(self):
		bad = frappe._dict(docname="TKT-1", owner="a@example.com", data="{not json")
		set_value, _ = self.run_patch(["TKT-1"], [bad])
		set_value.assert_not_called()
