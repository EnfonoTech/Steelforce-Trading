# sf_trading/announcements.py
# Copyright (c) 2026, Enfono Technologies and contributors
# For license information, please see license.txt

"""Announcements: tell the right people about a new rule, report, feature or notice -- and see who read it.

A manager posts an **SF Announcement** and says who should see it: everyone, a role, a department
(with or without the departments below it), a branch, or named people. Each person then sees only
what is meant for them, in the **Updates Hub** page and the bell in the navbar, and the bell also
rings (a Notification Log) when it is published.

How it is built, so the next person does not have to rediscover it:

* **Nobody but a manager can open the SF Announcement document.** Staff read through the whitelisted
  functions below, which decide per person -- by their roles, their Employee department and branch,
  and their name -- what they may see. So there is no list view, search result or API call that can
  show an update to someone it was not meant for.
* **Who is in an audience is worked out in two directions** and the tests hold them level:
  `visible_names(user)` asks "what is meant for this person?" (a person reading their feed), and
  `resolve_audience(rows)` asks "who is this meant for?" (the bell at publish time, the reach count).
* **A report is shown, but opened only by those allowed to.** A "Report" update carries a button that
  appears only when the reader may run that report -- the same role and DocType checks the desk makes.
  The author is warned, before publishing, how many people in the audience could not open it.
* **Publishing is idempotent.** `notified_on` stops a second bell for the same update, a retried job
  skips anybody who already has the bell, and a reminder waits `REMIND_EVERY_HOURS` hours.
* **Reading is a row per person** (SF Announcement Read, named `<announcement>::<user>`), which is what
  the reach figures, the "need your confirmation" list and the reminders are made from.
"""

import json
import re
from functools import reduce
from operator import or_
from urllib.parse import quote

import frappe
from frappe import _
from frappe.desk.doctype.notification_settings.notification_settings import is_notifications_enabled
from frappe.utils import add_to_date, cint, cstr, escape_html, get_datetime, get_fullname, get_url, now_datetime

DOCTYPE = "SF Announcement"
AUDIENCE = "SF Announcement Audience"
READ_LOG = "SF Announcement Read"
MANAGER_ROLE = "Announcement Manager"
MANAGER_ROLES = {MANAGER_ROLE, "System Manager"}
MODULE = "Sf Trading"

PAGE = "sf-updates"
WORKSPACE = "Updates Hub"
WORKSPACE_ICON = "notification"

DRAFT, SCHEDULED, PUBLISHED, EXPIRED, ARCHIVED = "Draft", "Scheduled", "Published", "Expired", "Archived"
KINDS = ("Announcement", "New Feature", "Rule or Policy", "Report", "Maintenance")
LINK_TYPES = ("Report", "DocType", "Page or Web Address")

SUMMARY_MAX = 400
REMIND_EVERY_HOURS = 4
NEW_REPORT_DAYS = 21
PAGE_MAX = 50
POPUP_MAX = 5
PENDING_LIST_MAX = 200

# the field each audience type is read from
AUDIENCE_FIELD = {"Role": "role", "Department": "department", "Branch": "branch", "User": "user"}
# roles every desk user holds without a Has Role row, so "everyone" is the honest reading of them
EVERYONE_ROLES = {"All", "Desk User"}

_SAFE_URL = re.compile(r"(/(?!/)|https?://)[^\s<>\"']+")


# ── Who is a manager, who is a desk user ────────────────────────────────────────────────

def is_manager(user=None) -> bool:
	user = user or frappe.session.user
	return user == "Administrator" or bool(MANAGER_ROLES.intersection(frappe.get_roles(user)))


def _desk_user() -> str:
	"""The signed-in desk user. The feed is for staff; a website user or a guest has no use for it."""
	user = frappe.session.user
	if user == "Guest" or frappe.db.get_value("User", user, "user_type") != "System User":
		frappe.throw(_("Only staff can read updates."), frappe.PermissionError)
	return user


def _require_manager():
	if not is_manager():
		frappe.throw(_("Only an Announcement Manager can do this."), frappe.PermissionError)


def _enabled_desk_users() -> set:
	return set(
		frappe.get_all(
			"User",
			filters={"enabled": 1, "user_type": "System User", "name": ["not in", ["Administrator", "Guest"]]},
			pluck="name",
		)
	)


# ── Who is in an audience ───────────────────────────────────────────────────────────────

def _with_ancestors(departments) -> set:
	"""These departments and every department above them -- the ones whose "with sub-departments" row reaches them."""
	found, stack = set(), list(departments or ())
	while stack:
		department = stack.pop()
		if department in found:
			continue
		found.add(department)
		parent = frappe.db.get_value("Department", department, "parent_department")
		if parent:
			stack.append(parent)
	return found


def _with_descendants(departments) -> set:
	"""These departments and every department below them."""
	found, frontier = set(departments or ()), set(departments or ())
	while frontier:
		children = set(
			frappe.get_all("Department", filters={"parent_department": ["in", list(frontier)]}, pluck="name")
		)
		frontier = children - found
		found |= children
	return found


def _configured_branches(user) -> set:
	"""The branches the user is set up to work in on the Branch Configuration, beside the one on their Employee."""
	return set(
		frappe.get_all(
			"Branch Configuration User", filters={"user": user, "parenttype": "Branch Configuration"}, pluck="parent"
		)
	)


def user_profile(user) -> frappe._dict:
	"""What decides which updates are meant for this person."""
	departments, branches = set(), set()
	for row in frappe.get_all(
		"Employee", filters={"user_id": user, "status": "Active"}, fields=["department", "branch"]
	):
		if row.department:
			departments.add(row.department)
		if row.branch:
			branches.add(row.branch)
	return frappe._dict(
		roles=set(frappe.get_roles(user)),
		departments=departments,
		department_chain=_with_ancestors(departments),
		branches=branches | _configured_branches(user),
	)


def audience_fits(audience, user, profile):
	"""A query condition: this audience row (the query-builder table `audience`) is meant for this person.

	One definition for everything that has an audience table -- the updates and the daily motivations --
	so they cannot drift apart. `resolve_audience` is the same rule asked the other way round.
	"""
	fits = [
		audience.audience_type == "Everyone",
		(audience.audience_type == "User") & (audience.user == user),
	]
	if profile.roles:
		fits.append((audience.audience_type == "Role") & audience.role.isin(list(profile.roles)))
	if profile.departments:
		fits.append(
			(audience.audience_type == "Department")
			& (
				audience.department.isin(list(profile.departments))
				| ((audience.include_sub_departments == 1) & audience.department.isin(list(profile.department_chain)))
			)
		)
	if profile.branches:
		fits.append((audience.audience_type == "Branch") & audience.branch.isin(list(profile.branches)))
	return reduce(or_, fits)


def visible_names(user=None) -> list:
	"""The live updates meant for this person: published, not past `expires_on`, and an audience row that fits them."""
	user = user or frappe.session.user
	profile = user_profile(user)
	announcement, audience = frappe.qb.DocType(DOCTYPE), frappe.qb.DocType(AUDIENCE)

	return (
		frappe.qb.from_(announcement)
		.join(audience)
		.on(audience.parent == announcement.name)
		.select(announcement.name)
		.distinct()
		.where(announcement.status == PUBLISHED)
		.where(audience.parenttype == DOCTYPE)
		.where(announcement.expires_on.isnull() | (announcement.expires_on > now_datetime()))
		.where(audience_fits(audience, user, profile))
	).run(pluck=True)


def resolve_audience(rows) -> list:
	"""Every enabled desk user the audience rows reach. `rows` may be Document rows or plain dicts."""
	enabled = _enabled_desk_users()
	roles, departments, with_sub, branches, named = set(), set(), set(), set(), set()
	for row in rows or ():
		kind = row.get("audience_type")
		if kind == "Everyone":
			return sorted(enabled)
		if kind == "Role" and row.get("role"):
			roles.add(row.get("role"))
		elif kind == "Department" and row.get("department"):
			(with_sub if cint(row.get("include_sub_departments")) else departments).add(row.get("department"))
		elif kind == "Branch" and row.get("branch"):
			branches.add(row.get("branch"))
		elif kind == "User" and row.get("user"):
			named.add(row.get("user"))

	if roles & EVERYONE_ROLES:
		return sorted(enabled)

	users = set(named)
	if roles:
		users.update(frappe.get_all("Has Role", filters={"role": ["in", list(roles)], "parenttype": "User"}, pluck="parent"))

	employee_filters = {"status": "Active", "user_id": ["is", "set"]}
	department_set = departments | _with_descendants(with_sub)
	if department_set:
		users.update(
			frappe.get_all("Employee", filters={**employee_filters, "department": ["in", list(department_set)]}, pluck="user_id")
		)
	if branches:
		users.update(
			frappe.get_all("Employee", filters={**employee_filters, "branch": ["in", list(branches)]}, pluck="user_id")
		)
		users.update(
			frappe.get_all(
				"Branch Configuration User",
				filters={"parenttype": "Branch Configuration", "parent": ["in", list(branches)]},
				pluck="user",
			)
		)
	return sorted(users & enabled)


# ── Saving rules ────────────────────────────────────────────────────────────────────────

def _safe_url(url) -> bool:
	return bool(_SAFE_URL.fullmatch(cstr(url)))


def validate_announcement(doc):
	"""Called from the controller on every save, however the document got here."""
	doc.title = cstr(doc.title).strip()
	doc.summary = cstr(doc.summary).strip()
	if len(doc.summary) > SUMMARY_MAX:
		frappe.throw(
			_("The short message is {0} characters. Keep it under {1}; put the rest in the full message.").format(
				len(doc.summary), SUMMARY_MAX
			)
		)
	_check_link(doc)
	check_audience(doc)
	_check_times(doc)
	if doc.status in (SCHEDULED, PUBLISHED) and not doc.get("audience"):
		frappe.throw(_("Add who should see this update before it goes out."))


def _check_link(doc):
	kind = doc.link_type or ""
	if kind == "Report":
		if not doc.link_report:
			frappe.throw(_("Pick the report people should open."))
		doc.link_doctype = doc.link_url = None
	elif kind == "DocType":
		if not doc.link_doctype:
			frappe.throw(_("Pick the DocType people should open."))
		doc.link_report = doc.link_url = None
	elif kind == "Page or Web Address":
		url = cstr(doc.link_url).strip()
		# this address becomes a link on everybody's screen, so only a route or an http(s) address is accepted
		if not _safe_url(url):
			frappe.throw(_("The address must start with /app/ or https:// and have no spaces."))
		doc.link_url = url
		doc.link_report = doc.link_doctype = None
	else:
		doc.link_type = doc.link_report = doc.link_doctype = doc.link_url = doc.link_label = None
		return
	if not cstr(doc.link_label).strip():
		doc.link_label = _("Open")


def check_audience(doc):
	"""Every audience row names who it means, and keeps no value left over from an earlier choice."""
	for row in doc.get("audience") or ():
		field = AUDIENCE_FIELD.get(row.audience_type)
		if field and not row.get(field):
			frappe.throw(_("Audience row {0}: pick the {1}.").format(row.idx, _(row.audience_type).lower()))
		# a value left from a previous choice of "Who" must not widen or narrow the audience
		for other, other_field in AUDIENCE_FIELD.items():
			if other != row.audience_type:
				row.set(other_field, None)


def _check_times(doc):
	if doc.publish_on and doc.expires_on and get_datetime(doc.expires_on) <= get_datetime(doc.publish_on):
		frappe.throw(_("\"Hide After\" must be later than \"Publish At\"."))


def clean_up(doc):
	"""on_trash: the read rows and the bell entries of a deleted update go with it."""
	frappe.db.delete(READ_LOG, {"announcement": doc.name})
	frappe.db.delete("Notification Log", {"document_type": DOCTYPE, "document_name": doc.name})


# ── Publishing ──────────────────────────────────────────────────────────────────────────

@frappe.whitelist()
def publish(announcement: str) -> dict:
	"""Send an update out now, or schedule it when "Publish At" is in the future."""
	_require_manager()
	doc = frappe.get_doc(DOCTYPE, announcement)
	doc.check_permission("write")
	if doc.status not in (DRAFT, SCHEDULED):
		frappe.throw(_("{0} is already {1}.").format(doc.name, _(doc.status)))
	if doc.expires_on and get_datetime(doc.expires_on) <= now_datetime():
		frappe.throw(_("\"Hide After\" is already in the past, so nobody would see this."))

	audience_size = len(resolve_audience(doc.get("audience")))
	if not audience_size:
		frappe.throw(_("Nobody is in this audience yet. Add who should see it."))

	if doc.publish_on and get_datetime(doc.publish_on) > now_datetime():
		doc.status = SCHEDULED
		doc.published_by = frappe.session.user
		doc.save()
		return {"status": SCHEDULED, "audience": audience_size}

	doc.published_by = doc.published_by or frappe.session.user
	go_live(doc)
	return {"status": PUBLISHED, "audience": audience_size}


def go_live(doc):
	"""Make the update visible and ring the bell once."""
	doc.status = PUBLISHED
	doc.published_on = now_datetime()
	doc.published_by = doc.published_by or doc.owner
	doc.save(ignore_permissions=True)
	if not doc.notified_on:
		doc.db_set("notified_on", now_datetime(), update_modified=False)
		_enqueue_notify(doc.name, reminder=0)


@frappe.whitelist()
def set_state(announcement: str, state: str) -> str:
	"""Take an update down (Archived), or turn it back into a Draft so it can be edited and sent again."""
	_require_manager()
	doc = frappe.get_doc(DOCTYPE, announcement)
	doc.check_permission("write")
	allowed_from = {
		ARCHIVED: {DRAFT, SCHEDULED, PUBLISHED, EXPIRED},
		DRAFT: {SCHEDULED, EXPIRED, ARCHIVED},
	}
	if state not in allowed_from or doc.status not in allowed_from[state]:
		frappe.throw(_("{0} cannot go from {1} to {2}.").format(doc.name, _(doc.status), _(state)))
	doc.status = state
	if state == DRAFT:
		# sent again from scratch: the bell rings again and the old "published" facts are void
		doc.published_on = doc.notified_on = doc.last_reminded_on = None
	doc.save()
	return doc.status


def _enqueue_notify(announcement, reminder, only=None):
	frappe.enqueue(
		"sf_trading.announcements.notify_audience",
		queue="default",
		timeout=900,
		announcement=announcement,
		reminder=reminder,
		only=only,
		now=frappe.flags.in_test,
		enqueue_after_commit=not frappe.flags.in_test,
	)


def notify_new_audience(doc):
	"""on_update: a published update whose audience was widened tells the people newly in it -- nobody else.

	Without this, adding a role or a branch to something already out would reach those people only if
	they happened to open the Updates Hub. Going live is `go_live`'s job, so a Draft or Scheduled
	update, and a document that was not Published before this save, are left alone.
	"""
	if doc.status != PUBLISHED or doc.is_new():
		return
	before = doc.get_doc_before_save()
	if not before or before.status != PUBLISHED:
		return
	newly = set(resolve_audience(doc.get("audience"))) - set(resolve_audience(before.get("audience")))
	if newly:
		_enqueue_notify(doc.name, reminder=0, only=sorted(newly))


def _pending_users(doc, users) -> list:
	"""Who has not read it yet -- or, when it needs a confirmation, has not confirmed it."""
	reads = {
		row.user: row
		for row in frappe.get_all(
			READ_LOG, filters={"announcement": doc.name}, fields=["user", "read_on", "acknowledged_on"]
		)
	}
	needs_ack = cint(doc.require_ack)
	return [
		user
		for user in users
		if user not in reads or not reads[user].read_on or (needs_ack and not reads[user].acknowledged_on)
	]


def notify_audience(announcement, reminder=0, only=None):
	"""Ring the bell for each person in the audience. Runs in the background; one failure never stops the rest.

	`only` narrows it to those people (the ones a widened audience just added)."""
	doc = frappe.get_doc(DOCTYPE, announcement)
	if doc.status != PUBLISHED:
		return 0

	users = resolve_audience(doc.get("audience"))
	if only is not None:
		wanted = set(only)
		users = [user for user in users if user in wanted]
	sender = doc.published_by or doc.owner
	if reminder:
		users = _pending_users(doc, users)
	else:
		users = [user for user in users if user != sender]

	title = frappe.bold(escape_html(doc.title))
	subject = (
		_("Reminder: {0}").format(title)
		if reminder
		else _("{0}: {1}").format(_(doc.kind), title)
	)
	link = get_url(f"/app/{PAGE}?open={quote(doc.name)}")
	payload = {
		"name": doc.name,
		"title": doc.title,
		"summary": doc.summary,
		"kind": doc.kind,
		"priority": doc.priority,
		"require_ack": cint(doc.require_ack),
	}

	sent = 0
	for user in users:
		try:
			if not is_notifications_enabled(user):
				continue
			already = frappe.db.exists(
				"Notification Log",
				{"for_user": user, "document_type": DOCTYPE, "document_name": doc.name, "subject": subject},
			)
			if already and not reminder:
				continue
			frappe.get_doc(
				{
					"doctype": "Notification Log",
					"type": "Alert",
					"for_user": user,
					"from_user": sender,
					"subject": subject,
					"email_content": doc.summary,
					"document_type": DOCTYPE,
					"document_name": doc.name,
					"link": link,
				}
			).insert(ignore_permissions=True)
			frappe.publish_realtime("sf_announcement", payload, user=user)
			sent += 1
			if sent % 50 == 0:
				frappe.db.commit()
		except Exception:
			frappe.log_error(frappe.get_traceback(), f"sf_trading announcements: bell for {user} failed")
	return sent


@frappe.whitelist()
def remind_pending(announcement: str) -> dict:
	"""Ring the bell again for the people who have not read (or not confirmed) an update."""
	_require_manager()
	doc = frappe.get_doc(DOCTYPE, announcement)
	doc.check_permission("write")
	if doc.status != PUBLISHED:
		frappe.throw(_("Only a published update can be sent again."))
	if doc.last_reminded_on and get_datetime(doc.last_reminded_on) > add_to_date(now_datetime(), hours=-REMIND_EVERY_HOURS):
		frappe.throw(
			_("A reminder already went out at {0}. Wait {1} hours between reminders.").format(
				frappe.utils.format_datetime(doc.last_reminded_on), REMIND_EVERY_HOURS
			)
		)
	pending = len(_pending_users(doc, resolve_audience(doc.get("audience"))))
	doc.db_set("last_reminded_on", now_datetime(), update_modified=False)
	_enqueue_notify(doc.name, reminder=1)
	return {"reminded": pending}


def run_schedule():
	"""scheduler (every tick): send what is due, and retire what has passed its "Hide After" time."""
	# a site that has not migrated the table (a sibling site on a shared bench) has nothing to do
	if not frappe.db.table_exists(DOCTYPE):
		return
	now = now_datetime()
	for name in frappe.get_all(DOCTYPE, filters={"status": SCHEDULED, "publish_on": ["<=", now]}, pluck="name"):
		try:
			go_live(frappe.get_doc(DOCTYPE, name))
			frappe.db.commit()
		except Exception:
			frappe.db.rollback()
			frappe.log_error(frappe.get_traceback(), f"sf_trading announcements: scheduled send of {name} failed")

	announcement = frappe.qb.DocType(DOCTYPE)
	(
		frappe.qb.update(announcement)
		.set(announcement.status, EXPIRED)
		.where(
			(announcement.status == PUBLISHED)
			& announcement.expires_on.isnotnull()
			& (announcement.expires_on <= now)
		)
	).run()
	frappe.db.commit()


# ── Reading: the feed, the counters, read and confirm ───────────────────────────────────

FEED_FIELDS = [
	"name", "title", "summary", "details", "kind", "priority", "pinned", "require_ack", "status",
	"published_on", "published_by", "owner", "modified", "link_type", "link_report", "link_doctype",
	"link_url", "link_label",
]  # fmt: skip


def _load(names, user, order_by="pinned desc, published_on desc, modified desc", fields=None):
	"""The named updates, each carrying when this user read / confirmed it."""
	if not names:
		return []
	rows = frappe.get_all(
		DOCTYPE, filters={"name": ["in", list(names)]}, fields=fields or FEED_FIELDS, order_by=order_by
	)
	reads = {
		row.announcement: row
		for row in frappe.get_all(
			READ_LOG,
			filters={"user": user, "announcement": ["in", list(names)]},
			fields=["announcement", "read_on", "acknowledged_on"],
		)
	}
	for row in rows:
		read = reads.get(row.name)
		row.read_on = read.read_on if read else None
		row.acknowledged_on = read.acknowledged_on if read else None
	return rows


def _counts(rows) -> dict:
	return {
		"total": len(rows),
		"unread": sum(1 for row in rows if not row.read_on),
		"need_ack": sum(1 for row in rows if cint(row.get("require_ack")) and not row.acknowledged_on),
		"urgent": sum(1 for row in rows if row.get("priority") == "Urgent" and not row.read_on),
	}


def summary(user=None) -> dict:
	user = user or frappe.session.user
	rows = _load(
		visible_names(user), user, order_by="name", fields=["name", "priority", "require_ack"]
	)
	return _counts(rows)


@frappe.whitelist()
def get_summary() -> dict:
	user = _desk_user()
	return {**summary(user), "can_post": is_manager(user)}


@frappe.whitelist()
def get_feed(view="all", kind=None, search=None, start=0, page_length=20) -> dict:
	"""One page of the updates meant for the signed-in person.

	view: all | unread | urgent | need_ack | posts (a manager: everything posted, any status).
	"""
	user = _desk_user()
	start, page_length = max(cint(start), 0), min(max(cint(page_length), 1), PAGE_MAX)

	if view == "posts":
		_require_manager()
		rows = _load(frappe.get_all(DOCTYPE, pluck="name"), user, order_by="modified desc")
	else:
		rows = _load(visible_names(user), user)
		if view == "unread":
			rows = [row for row in rows if not row.read_on]
		elif view == "urgent":
			rows = [row for row in rows if row.priority == "Urgent"]
		elif view == "need_ack":
			rows = [row for row in rows if cint(row.require_ack) and not row.acknowledged_on]

	if kind in KINDS:
		rows = [row for row in rows if row.kind == kind]
	needle = cstr(search).strip().lower()
	if needle:
		rows = [row for row in rows if needle in f"{row.title} {row.summary}".lower()]

	page = rows[start : start + page_length]
	return {"items": _shape(page, show_reach=view == "posts"), "total": len(rows), "start": start}


@frappe.whitelist()
def get_popup_queue() -> list:
	"""What must interrupt the person: Urgent updates not yet seen, and updates waiting for their confirmation."""
	user = _desk_user()
	rows = [
		row
		for row in _load(visible_names(user), user)
		if (row.priority == "Urgent" and not row.read_on) or (cint(row.require_ack) and not row.acknowledged_on)
	]
	return _shape(rows[:POPUP_MAX])


@frappe.whitelist()
def get_one(announcement: str):
	"""One update, for the link in a notification. A person gets it only if it is meant for them."""
	user = _desk_user()
	if announcement not in set(visible_names(user)) and not is_manager(user):
		frappe.throw(_("This update is not meant for you."), frappe.PermissionError)
	return (_shape(_load([announcement], user)) or [None])[0]


def _shape(rows, show_reach=False) -> list:
	"""What the screen needs for each update, with every figure already decided here."""
	if not rows:
		return []
	names = [row.name for row in rows]
	audience = _audience_rows(names)
	labels = _audience_labels(names, audience)
	return [
		{
			"name": row.name,
			"title": row.title,
			"summary": row.summary,
			"details": _clean_html(row.details),
			"kind": row.kind,
			"priority": row.priority,
			"pinned": cint(row.pinned),
			"require_ack": cint(row.require_ack),
			"status": row.status,
			"published_on": row.published_on,
			"posted_by": get_fullname(row.published_by or row.owner),
			"audience": labels.get(row.name, ""),
			"is_read": bool(row.read_on),
			"acknowledged": bool(row.acknowledged_on),
			"acknowledged_on": row.acknowledged_on,
			"link": _link_for(row),
			"reach": _reach_counts(row.name, audience.get(row.name, [])) if show_reach and row.status == PUBLISHED else None,
		}
		for row in rows
	]


def _clean_html(html):
	if not html:
		return ""
	try:
		from frappe.utils.html_utils import sanitize_html

		return sanitize_html(html)
	except Exception:
		return escape_html(frappe.utils.strip_html(html))


def _audience_rows(names) -> dict:
	"""The audience rows of all these updates in one query, grouped by update."""
	grouped = {}
	for row in frappe.get_all(
		AUDIENCE,
		filters={"parent": ["in", list(names)], "parenttype": DOCTYPE},
		fields=["parent", "audience_type", "role", "department", "include_sub_departments", "branch", "user"],
		order_by="idx asc",
	):
		grouped.setdefault(row.parent, []).append(row)
	return grouped


def _audience_labels(names, audience) -> dict:
	"""A short "who is this for" line per update: "Sales Manager, SFSB branch +1 more"."""
	parts, people = {}, {}
	for row in (row for rows in audience.values() for row in rows):
		if row.audience_type == "User":
			people[row.parent] = people.get(row.parent, 0) + 1
			continue
		text = {
			"Everyone": _("Everyone"),
			"Role": row.role,
			"Department": (
				_("{0} department and its teams").format(row.department)
				if cint(row.include_sub_departments)
				else _("{0} department").format(row.department)
			),
			"Branch": _("{0} branch").format(row.branch),
		}.get(row.audience_type)
		if text:
			parts.setdefault(row.parent, []).append(text)
	labels = {}
	for name in names:
		items = list(parts.get(name, []))
		if people.get(name):
			items.append(_("{0} named people").format(people[name]))
		labels[name] = ", ".join(items[:3]) + (_(" +{0} more").format(len(items) - 3) if len(items) > 3 else "")
	return labels


# ── The button on an update: opens a report / a list / a page, but only for those who may ──

def _allowed_reports() -> dict:
	"""The reports the signed-in user may run, by role -- the desk's own answer, fetched once per request."""
	if "sf_allowed_reports" not in frappe.local.flags:
		from frappe.boot import get_allowed_reports

		frappe.local.flags.sf_allowed_reports = get_allowed_reports(cache=True) or {}
	return frappe.local.flags.sf_allowed_reports


def report_route(name):
	"""(may the user open it, where to). A report the user is not allowed gets no button at all."""
	info = frappe.db.get_value("Report", name, ["report_type", "ref_doctype", "disabled"], as_dict=True)
	if not info or cint(info.disabled) or name not in _allowed_reports():
		return False, None
	if info.ref_doctype and not frappe.has_permission(info.ref_doctype, "report"):
		return False, None
	if info.report_type == "Report Builder":
		return True, ["List", info.ref_doctype, "Report", name]
	return True, ["query-report", name]


def _link_for(row):
	kind = row.link_type
	if kind not in LINK_TYPES:
		return None
	label = row.link_label or _("Open")
	if kind == "Report":
		can_open, route = report_route(row.link_report)
		return {"type": kind, "label": label, "target": row.link_report, "can_open": can_open, "route": route}
	if kind == "DocType":
		can_open = bool(frappe.db.exists("DocType", row.link_doctype)) and frappe.has_permission(
			row.link_doctype, "read"
		)
		return {
			"type": kind, "label": label, "target": row.link_doctype, "can_open": can_open,
			"route": ["List", row.link_doctype] if can_open else None,
		}  # fmt: skip
	# an address inside the desk is followed as a route, anything else opens in a new tab
	url = row.link_url or ""
	return {"type": kind, "label": label, "target": url, "can_open": _safe_url(url), "href": url}


@frappe.whitelist()
def get_my_reports(search=None) -> list:
	"""The reports this person may run -- those of this app, and any that an update they can see points to."""
	user = _desk_user()
	allowed = _allowed_reports()
	announced = {}
	for row in _load(visible_names(user), user, fields=["name", "title", "link_type", "link_report", "published_on"]):
		if row.link_type == "Report" and row.link_report and row.link_report not in announced:
			announced[row.link_report] = row

	wanted = list({*frappe.get_all("Report", filters={"disabled": 0, "module": MODULE}, pluck="name"), *announced})
	if not wanted:
		return []
	cutoff = add_to_date(now_datetime(), days=-NEW_REPORT_DAYS)
	needle = cstr(search).strip().lower()
	out = []
	for report in frappe.get_all(
		"Report",
		filters={"name": ["in", wanted], "disabled": 0},
		fields=["name", "report_type", "ref_doctype", "module", "modified"],
	):
		if report.name not in allowed or (needle and needle not in report.name.lower()):
			continue
		can_open, route = report_route(report.name)
		if not can_open:
			continue
		note = announced.get(report.name)
		out.append(
			{
				"name": report.name,
				"module": report.module,
				"route": route,
				"announced_in": note.name if note else None,
				"announced_title": note.title if note else None,
				"is_new": bool(note) or get_datetime(report.modified) >= cutoff,
			}
		)
	out.sort(key=lambda item: (not item["is_new"], item["name"].lower()))
	return out


# ── Reading and confirming ──────────────────────────────────────────────────────────────

def _as_list(value) -> list:
	if isinstance(value, str):
		value = json.loads(value) if value.strip().startswith("[") else [value]
	return [cstr(v) for v in value or ()]


def _touch(announcement, user, acknowledge):
	"""Record that this person read (and maybe confirmed) an update. Safe to call twice."""
	row_name = f"{announcement}::{user}"
	now = now_datetime()
	existing = frappe.db.get_value(READ_LOG, row_name, ["read_on", "acknowledged_on"], as_dict=True)
	if not existing:
		try:
			frappe.get_doc(
				{
					"doctype": READ_LOG,
					"announcement": announcement,
					"user": user,
					"read_on": now,
					"acknowledged_on": now if acknowledge else None,
				}
			).insert(ignore_permissions=True)
			return
		except frappe.DuplicateEntryError:
			# another tab got there first; fall through and top the row up
			frappe.clear_last_message()
			existing = frappe.db.get_value(READ_LOG, row_name, ["read_on", "acknowledged_on"], as_dict=True)
	changes = {}
	if not existing.read_on:
		changes["read_on"] = now
	if acknowledge and not existing.acknowledged_on:
		changes["acknowledged_on"] = now
	if changes:
		frappe.db.set_value(READ_LOG, row_name, changes, update_modified=False)


def _clear_bell(user, names):
	"""Reading an update clears its bell entry too, so the two counts agree."""
	for name in names:
		frappe.db.set_value(
			"Notification Log",
			{"for_user": user, "document_type": DOCTYPE, "document_name": name, "read": 0},
			"read",
			1,
			update_modified=False,
		)


@frappe.whitelist()
def mark_read(names) -> dict:
	user = _desk_user()
	meant_for_user = set(visible_names(user)) & set(_as_list(names))
	for name in meant_for_user:
		_touch(name, user, acknowledge=False)
	_clear_bell(user, meant_for_user)
	return summary(user)


@frappe.whitelist()
def mark_all_read() -> dict:
	user = _desk_user()
	names = visible_names(user)
	for name in names:
		_touch(name, user, acknowledge=False)
	_clear_bell(user, names)
	return summary(user)


@frappe.whitelist()
def acknowledge(announcement: str) -> dict:
	""""I have read and understood" -- also counts as read."""
	user = _desk_user()
	if announcement not in set(visible_names(user)):
		frappe.throw(_("This update is not meant for you."), frappe.PermissionError)
	_touch(announcement, user, acknowledge=True)
	_clear_bell(user, [announcement])
	return summary(user)


# ── For the author: who it reaches, who has read it, who cannot open the report ────────

def _reach_counts(announcement, audience_rows) -> dict:
	users = resolve_audience(audience_rows)
	reads = frappe.get_all(
		READ_LOG,
		filters={"announcement": announcement, "user": ["in", users or [""]]},
		fields=["user", "read_on", "acknowledged_on"],
	)
	return {
		"total": len(users),
		"seen": sum(1 for row in reads if row.read_on),
		"acknowledged": sum(1 for row in reads if row.acknowledged_on),
	}


@frappe.whitelist()
def get_reach(announcement: str) -> dict:
	"""Who has read an update, who has confirmed it, and who is still to do so."""
	_require_manager()
	doc = frappe.get_doc(DOCTYPE, announcement)
	doc.check_permission("read")
	users = resolve_audience(doc.get("audience"))
	reads = {
		row.user: row
		for row in frappe.get_all(
			READ_LOG,
			filters={"announcement": announcement},
			fields=["user", "read_on", "acknowledged_on"],
		)
	}
	needs_ack = bool(cint(doc.require_ack))
	pending = _pending_users(doc, users)
	return {
		"status": doc.status,
		"total": len(users),
		"seen": sum(1 for user in users if user in reads and reads[user].read_on),
		"acknowledged": sum(1 for user in users if user in reads and reads[user].acknowledged_on),
		"needs_ack": needs_ack,
		"pending": [
			{"user": user, "full_name": get_fullname(user), "seen": bool(user in reads and reads[user].read_on)}
			for user in pending[:PENDING_LIST_MAX]
		],
		"pending_total": len(pending),
		"last_reminded_on": doc.last_reminded_on,
		"can_remind": doc.status == PUBLISHED and bool(pending),
	}


@frappe.whitelist()
def preview_audience(rows=None, link_type=None, link_report=None) -> dict:
	"""For the form, before publishing: how many people the audience rows reach, and who could not open the report."""
	_require_manager()
	rows = json.loads(rows) if isinstance(rows, str) else (rows or [])
	users = resolve_audience(rows)
	out = {
		"count": len(users),
		"sample": [{"user": user, "full_name": get_fullname(user)} for user in users[:12]],
		"no_report_access": None,
	}
	if link_type == "Report" and link_report and users:
		out["no_report_access"] = _without_report_access(link_report, users)
	return out


@frappe.whitelist()
def sanitize_preview(html=None) -> str:
	"""For the form's preview: the full message exactly as it will be cleaned for readers."""
	_require_manager()
	return _clean_html(html)


def _without_report_access(report, users) -> dict:
	"""Who in `users` holds none of the roles the report is limited to."""
	allowed_roles = frappe.get_all("Has Role", filters={"parenttype": "Report", "parent": report}, pluck="role")
	if not allowed_roles:
		return {"count": 0, "sample": []}
	holders = set(
		frappe.get_all("Has Role", filters={"parenttype": "User", "role": ["in", allowed_roles]}, pluck="parent")
	)
	missing = [user for user in users if user not in holders]
	return {
		"count": len(missing),
		"sample": [get_fullname(user) for user in missing[:8]],
		"roles": allowed_roles,
	}


# ── Desk furniture: the Updates Hub workspace, the role, and the boot payload ───────────

SHORTCUTS = (
	{"label": "My Updates", "type": "Page", "link_to": PAGE},
	{"label": "Post an Announcement", "type": "DocType", "link_to": DOCTYPE, "doc_view": "New"},
	{"label": "All Announcements", "type": "DocType", "link_to": DOCTYPE, "doc_view": "List"},
	{"label": "Who Has Read What", "type": "DocType", "link_to": READ_LOG, "doc_view": "List"},
	{"label": "Daily Motivation", "type": "DocType", "link_to": "SF Motivation", "doc_view": "List"},
	{"label": "Motivation Settings", "type": "DocType", "link_to": "SF Motivation Settings"},
)


def workspace_content() -> list:
	blocks = [{"type": "header", "data": {"text": '<span class="h4"><b>Updates and Announcements</b></span>', "col": 12}}]
	blocks += [{"type": "shortcut", "data": {"shortcut_name": row["label"], "col": 3}} for row in SHORTCUTS]
	blocks.append({"type": "spacer", "data": {"col": 12}})
	return blocks


def _shortcut_blocks(content) -> set:
	try:
		blocks = json.loads(content or "[]")
	except ValueError:
		return set()
	return {block.get("data", {}).get("shortcut_name") for block in blocks if block.get("type") == "shortcut"}


def ensure_workspace():
	"""The Updates Hub workspace, for every desk user (what each sees is trimmed by their permissions).

	Rebuilt only while it lacks one of SHORTCUTS, so a release that adds a shortcut reaches a site that
	already has the workspace and a copy somebody merely rearranged is left alone.
	"""
	exists = bool(frappe.db.exists("Workspace", WORKSPACE))
	if exists:
		doc = frappe.get_doc("Workspace", WORKSPACE)
		if {row["label"] for row in SHORTCUTS} <= _shortcut_blocks(doc.content):
			return
	else:
		doc = frappe.get_doc(
			{
				"doctype": "Workspace", "name": WORKSPACE, "label": WORKSPACE, "title": WORKSPACE,
				"module": MODULE, "public": 1, "icon": WORKSPACE_ICON,
			}
		)  # fmt: skip

	doc.set("shortcuts", [])
	doc.content = json.dumps(workspace_content())
	for row in SHORTCUTS:
		# a copy: append() writes "doctype" into the dict it is handed
		doc.append("shortcuts", dict(row))
	if exists:
		doc.save(ignore_permissions=True)
	else:
		doc.insert(ignore_permissions=True)


def setup():
	"""after_migrate entry point. Each step is guarded: furniture never aborts a migrate."""
	for step in (ensure_workspace,):
		try:
			step()
		except Exception:
			frappe.log_error(frappe.get_traceback(), f"sf_trading announcements: {step.__name__} failed")


def boot_session(bootinfo):
	"""The counters the navbar bell shows the moment the desk opens, with no extra request.

	Never raises: a site that has not migrated yet, or a guest, simply has no bell.
	"""
	try:
		user = frappe.session.user
		if user == "Guest" or not frappe.db.table_exists(DOCTYPE):
			return
		if frappe.db.get_value("User", user, "user_type") != "System User":
			return
		bootinfo.sf_announcements = {**summary(user), "can_post": is_manager(user), "page": PAGE, "motivation": None}
	except Exception:
		frappe.log_error(frappe.get_traceback(), "sf_trading announcements: boot payload failed")
		return

	# the first desk load of the day carries today's motivation; a failure here never costs the person their bell
	try:
		from sf_trading import motivation

		bootinfo.sf_announcements["motivation"] = motivation.for_boot(user)
	except Exception:
		frappe.log_error(frappe.get_traceback(), "sf_trading motivation: boot payload failed")
