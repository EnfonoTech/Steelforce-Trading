# sf_trading/ticket.py
"""Support tickets: who is involved, who may see a ticket, and who hears about what.

Any desk user raises a ticket; a Ticket Manager allocates it; the person it is with works it;
whoever raised it closes it, or reopens it. The conversation is the form's own comment box.

**Involved** means: whoever raised it (and whoever typed it in, if that was somebody else), the
assignee and anyone else holding an assignment for it, the people on its Keep Informed table,
everyone who has commented on it, and everyone it was shared with -- an @mention shares it.
Everyone involved hears about every reply, status change and hand-over, except whoever made it.

**Access**: a Ticket Manager or System Manager sees every ticket; everyone else sees only the
tickets they are involved in. Keep Informed people and @mentioned people may read and reply,
not edit. The role permission (Desk User may read/write/create) grants; the two hooks below
narrow it -- a controller permission hook can only ever deny.

**Notifications**: always the desk bell (a Notification Log of type Alert, pushed live). Email
as well when the site has an outgoing Email Account and the user has not switched email
notifications off -- frappe never emails an Alert by itself (see
api/overdue_notifications.py), so the guarded frappe.sendmail below does it.

**Assignment** is frappe's own ToDo, so the ticket sits in the assignee's ToDo list and frappe
sends its usual assignment notification. The `assignee` field is the one people read; the
sidebar's Assign To writes ToDos directly, so sync_assignee_from_todo copies those changes back
onto the field. The field is deliberately not called `assigned_to`: frappe's assign_to rewrites a
field of that name on every new assignment and blanks it whenever ANY assignment is closed or
removed -- a co-assignee's included -- which would lose who the ticket is with.
"""

import json

import frappe
from frappe import _
from frappe.desk.doctype.notification_settings.notification_settings import (
	is_email_notifications_enabled,
	is_notifications_enabled,
)
from frappe.desk.form.assign_to import _add as add_assignment
from frappe.desk.form.assign_to import notify_assignment
from frappe.desk.notifications import extract_mentions
from frappe.share import add_docshare
from frappe.utils import escape_html, get_fullname, get_url_to_form, now_datetime, nowdate, strip_html

DOCTYPE = "Ticket"
MANAGER_ROLE = "Ticket Manager"
MANAGER_ROLES = {MANAGER_ROLE, "System Manager"}

OPEN = "Open"
IN_PROGRESS = "In Progress"
ON_HOLD = "On Hold"
RESOLVED = "Resolved"
CLOSED = "Closed"
STATUSES = (OPEN, IN_PROGRESS, ON_HOLD, RESOLVED, CLOSED)
DONE = (RESOLVED, CLOSED)

# all a Keep Informed or @mentioned person may do: look at the ticket and talk about it
READ_ONLY_PTYPES = ("read", "select", "print")

# ToDo knows only three priorities
TODO_PRIORITY = {"Low": "Low", "Medium": "Medium", "High": "High", "Urgent": "High"}


# ── Who is who on a ticket ──────────────────────────────────────────────────────────

def is_manager(user=None) -> bool:
	user = user or frappe.session.user
	return user == "Administrator" or bool(MANAGER_ROLES.intersection(frappe.get_roles(user)))


def holds_assignment(ticket_name, user, statuses=("Open",)) -> bool:
	if not ticket_name:
		return False
	return bool(
		frappe.db.exists(
			"ToDo",
			{
				"reference_type": DOCTYPE,
				"reference_name": ticket_name,
				"allocated_to": user,
				"status": ["in", list(statuses)],
			},
		)
	)


def is_assignee(doc, user=None) -> bool:
	"""The field's user, or anyone else currently holding an assignment from the sidebar."""
	user = user or frappe.session.user
	return doc.get("assignee") == user or holds_assignment(doc.get("name"), user)


def is_reporter(doc, user=None) -> bool:
	user = user or frappe.session.user
	return user in (doc.get("raised_by"), doc.get("owner"))


def is_watcher(doc, user=None) -> bool:
	user = user or frappe.session.user
	return any(row.user == user for row in doc.get("watchers") or [])


def involved_users(doc) -> set:
	"""Everyone who hears about a change to this ticket -- see the module docstring."""
	users = {doc.get("owner"), doc.get("raised_by"), doc.get("assignee")}
	users.update(row.user for row in doc.get("watchers") or [])
	users.update(
		frappe.get_all(
			"ToDo",
			filters={"reference_type": DOCTYPE, "reference_name": doc.name, "status": ["in", ["Open", "Closed"]]},
			pluck="allocated_to",
		)
	)
	users.update(
		frappe.get_all(
			"Comment",
			filters={"reference_doctype": DOCTYPE, "reference_name": doc.name, "comment_type": "Comment"},
			pluck="owner",
		)
	)
	users.update(
		frappe.get_all(
			"DocShare",
			filters={"share_doctype": DOCTYPE, "share_name": doc.name, "everyone": 0},
			pluck="user",
		)
	)
	return {user for user in users if user and user != "Guest"}


def ticket_managers() -> list:
	"""Who allocates new tickets: the Ticket Managers, or the System Managers while nobody holds that role."""
	for role in (MANAGER_ROLE, "System Manager"):
		users = _enabled_desk_users(
			frappe.get_all("Has Role", filters={"role": role, "parenttype": "User"}, pluck="parent")
		)
		if users:
			return users
	return []


def _enabled_desk_users(users) -> list:
	users = [user for user in set(users or ()) if user]
	if not users:
		return []
	return frappe.get_all(
		"User", filters={"name": ["in", users], "enabled": 1, "user_type": "System User"}, pluck="name"
	)


# ── Permission hooks ────────────────────────────────────────────────────────────────

def has_permission(doc, ptype=None, user=None, debug=False):
	"""has_permission hook for Ticket.

	Returning False here still lets frappe's own share check through afterwards, read-only --
	which is exactly what an @mention should give somebody.
	"""
	user = user or frappe.session.user
	if is_manager(user):
		return True
	if frappe.get_cached_value("User", user, "user_type") != "System User":
		return False
	if is_reporter(doc, user) or doc.get("assignee") == user:
		return True
	# whoever holds, or has finished, an assignment on it keeps working access
	if holds_assignment(doc.get("name"), user, statuses=("Open", "Closed")):
		return True
	if is_watcher(doc, user):
		return (ptype or "read") in READ_ONLY_PTYPES
	return False


def get_permission_query_conditions(user=None, doctype=None):
	"""permission_query_conditions hook for Ticket -- the list's twin of has_permission.

	Tickets shared with the user (an @mention) are added by frappe itself, OR-ed onto this.
	"""
	user = user or frappe.session.user
	if is_manager(user):
		return ""
	# frappe takes this hook's answer as SQL text, so it cannot be a bound parameter. The one
	# value in it is the session user's id, quoted by the database driver's own escape -- the
	# same construction frappe's ToDo and Event hooks use.
	who = frappe.db.escape(user)
	return f"""(`tabTicket`.`owner` = {who}
		or `tabTicket`.`raised_by` = {who}
		or `tabTicket`.`assignee` = {who}
		or exists (select 1 from `tabTicket Watcher` tw
			where tw.parenttype = 'Ticket' and tw.parent = `tabTicket`.`name` and tw.user = {who})
		or exists (select 1 from `tabToDo` td
			where td.reference_type = 'Ticket' and td.reference_name = `tabTicket`.`name`
			and td.allocated_to = {who} and td.status in ('Open', 'Closed')))"""


# ── The rules for a change ──────────────────────────────────────────────────────────

def validate_change(doc, before):
	"""Who may make this change. Called from the controller's validate."""
	user = frappe.session.user
	if is_manager(user):
		return

	if before is None:
		if doc.raised_by != user:
			frappe.throw(
				_("You can raise a ticket for yourself only. A Ticket Manager can raise one on someone else's behalf."),
				title=_("Not Allowed"),
			)
		if doc.assignee:
			frappe.throw(_("A Ticket Manager allocates new tickets. Leave Assigned To empty."), title=_("Not Allowed"))
		if doc.status != OPEN:
			frappe.throw(_("A new ticket starts as Open."), title=_("Not Allowed"))
		return

	if doc.raised_by != before.raised_by:
		frappe.throw(_("Only a Ticket Manager can change who raised a ticket."), title=_("Not Allowed"))

	assignee = is_assignee(before, user)
	if (doc.assignee or None) != (before.assignee or None) and not assignee:
		frappe.throw(
			_("Only a Ticket Manager, or the person the ticket is with, can allocate it."), title=_("Not Allowed")
		)

	if doc.status != before.status and not assignee:
		closing = doc.status == CLOSED
		reopening = before.status in DONE and doc.status == OPEN
		if not (is_reporter(before, user) and (closing or reopening)):
			frappe.throw(
				_(
					"Only the person the ticket is with, or a Ticket Manager, can move it to {0}. "
					"You can close your own ticket, or reopen it once it is resolved."
				).format(_(doc.status)),
				title=_("Not Allowed"),
			)


# ── After a save ────────────────────────────────────────────────────────────────────

def after_save(doc, before):
	"""on_update of Ticket, called from the controller: assignments and notifications."""
	if before is None:
		_on_new(doc)
		return
	if (before.assignee or None) != (doc.assignee or None):
		_on_reassigned(doc, before.assignee)
	if before.status != doc.status:
		_on_status_change(doc, before.status)
	_on_watchers_added(doc, {row.user for row in before.get("watchers") or []})


def _on_new(doc):
	actor = frappe.session.user
	notify(
		set(ticket_managers()) - {actor, doc.raised_by},
		_("{0} raised a new ticket {1}").format(_who(), _what(doc)),
		doc,
		body=doc.description,
	)
	if doc.raised_by != actor:
		notify(
			{doc.raised_by},
			_("{0} raised ticket {1} for you").format(_who(), _what(doc)),
			doc,
			body=doc.description,
		)
	if doc.assignee and doc.status not in DONE:
		_assign(doc, doc.assignee)
	_on_watchers_added(doc, set())


def _on_reassigned(doc, previous):
	actor = frappe.session.user
	if previous:
		_set_assignments(doc, "Cancelled", user=previous)
		# frappe tells somebody their assignment was removed -- not when they handed it over themselves
		if previous != actor:
			notify_assignment(actor, previous, DOCTYPE, doc.name)
	if doc.assignee and doc.status not in DONE:
		_assign(doc, doc.assignee)
	if doc.assignee and doc.raised_by not in (actor, doc.assignee):
		notify(
			{doc.raised_by},
			_("Your ticket {0} is now with {1}").format(_what(doc), _who(doc.assignee)),
			doc,
		)


def _on_status_change(doc, previous):
	if doc.status in DONE:
		_set_assignments(doc, "Closed")
	elif previous in DONE and doc.assignee:
		# reopened: back on the assignee's ToDo list -- quietly, the "reopened" notice below says why
		_assign(doc, doc.assignee, quiet=True)

	if previous in DONE and doc.status not in DONE:
		message = _("{0} reopened {1}")
	else:
		message = {
			IN_PROGRESS: _("{0} started working on {1}"),
			ON_HOLD: _("{0} put {1} on hold"),
			RESOLVED: _("{0} resolved {1}"),
			CLOSED: _("{0} closed {1}"),
		}.get(doc.status, _("{0} moved {1} back to Open"))
	body = doc.resolution_details if doc.status == RESOLVED else doc.flags.status_note
	notify(involved_users(doc) - {frappe.session.user}, message.format(_who(), _what(doc)), doc, body=body)


def _on_watchers_added(doc, previous):
	added = {row.user for row in doc.get("watchers") or []} - set(previous) - {frappe.session.user}
	if added:
		notify(added, _("{0} added you to ticket {1}").format(_who(), _what(doc)), doc, body=doc.description)


def _assign(doc, user, quiet=False):
	"""Put the ticket on the user's ToDo list. frappe notifies them itself, unless quiet."""
	if holds_assignment(doc.name, user):
		return
	assignment = {
		"description": escape_html(doc.subject or doc.name),
		"priority": TODO_PRIORITY.get(doc.priority, "Medium"),
	}
	if quiet:
		frappe.get_doc(
			{
				"doctype": "ToDo",
				"allocated_to": user,
				"reference_type": DOCTYPE,
				"reference_name": doc.name,
				"status": "Open",
				"date": nowdate(),
				"assigned_by": frappe.session.user,
				**assignment,
			}
		).insert(ignore_permissions=True)
		return
	add_assignment({"assign_to": [user], "doctype": DOCTYPE, "name": doc.name, **assignment}, ignore_permissions=True)


def _set_assignments(doc, status, user=None):
	"""Close (ticket done) or cancel (handed over) the open assignments, without frappe's own
	"your assignment was removed" message -- the caller decides who hears what."""
	filters = {"reference_type": DOCTYPE, "reference_name": doc.name, "status": "Open"}
	if user:
		filters["allocated_to"] = user
	for name in frappe.get_all("ToDo", filters=filters, pluck="name"):
		todo = frappe.get_doc("ToDo", name)
		todo.status = status
		todo.save(ignore_permissions=True)


# ── The conversation, and the sidebar ───────────────────────────────────────────────

def on_comment(doc, method=None):
	"""after_insert on Comment: a reply on a ticket reaches everyone on it.

	Never raises -- somebody's reply is never refused because a notification could not go out.
	"""
	if doc.reference_doctype != DOCTYPE or doc.comment_type != "Comment" or not doc.reference_name:
		return
	if frappe.flags.sf_ticket_note:
		# the note of a status change: that change's own notification already carries it
		return
	try:
		ticket = frappe.get_doc(DOCTYPE, doc.reference_name)
		author = doc.owner or frappe.session.user
		mentioned = set(extract_mentions(doc.content or ""))
		_share_with_mentioned(ticket, mentioned)
		if not ticket.first_response_on and author not in (ticket.raised_by, ticket.owner):
			ticket.db_set("first_response_on", now_datetime(), update_modified=False)
		# frappe has already told whoever was @mentioned
		notify(
			involved_users(ticket) - {author} - mentioned,
			_("{0} replied on {1}").format(_who(author), _what(ticket)),
			ticket,
			body=doc.content,
		)
	except Exception:
		frappe.log_error(frappe.get_traceback(), "sf_trading: ticket reply handling failed")


def _share_with_mentioned(ticket, users):
	"""An @mention brings somebody in: they can read the ticket and reply, nothing more."""
	for user in _enabled_desk_users(users):
		if frappe.has_permission(DOCTYPE, "read", doc=ticket, user=user):
			continue
		add_docshare(DOCTYPE, ticket.name, user, read=1, flags={"ignore_share_permission": True})


def sync_assignee_from_todo(doc, method=None):
	"""on_update on ToDo: an assignment made or removed from the sidebar reaches the field.

	The ticket's own saves already keep the two in step, so this only acts when they differ: a
	first assignment on an unallocated ticket fills the field, and removing the field's user hands
	the ticket to whoever else still holds an assignment (or leaves it unallocated).
	"""
	if doc.reference_type != DOCTYPE or not doc.reference_name:
		return
	current = frappe.db.get_value(DOCTYPE, doc.reference_name, "assignee")
	if doc.status == "Open" and not current:
		new = doc.allocated_to
	elif doc.status == "Cancelled" and current and current == doc.allocated_to:
		others = frappe.get_all(
			"ToDo",
			filters={
				"reference_type": DOCTYPE,
				"reference_name": doc.reference_name,
				"status": "Open",
				"allocated_to": ["!=", doc.allocated_to],
			},
			pluck="allocated_to",
			order_by="creation desc",
			limit=1,
		)
		new = others[0] if others else None
	else:
		return
	if not frappe.db.exists(DOCTYPE, doc.reference_name):
		return

	ticket = frappe.get_doc(DOCTYPE, doc.reference_name)
	# notify=True: an open form reloads, instead of later saving the old value back over this one
	ticket.db_set({"assignee": new, "assignee_name": get_fullname(new) if new else None}, notify=True)
	if new and ticket.raised_by not in (frappe.session.user, new):
		notify({ticket.raised_by}, _("Your ticket {0} is now with {1}").format(_what(ticket), _who(new)), ticket)


# ── Notifications ───────────────────────────────────────────────────────────────────

def notify(users, subject, doc, body=None):
	"""Ring the bell for each user, and email them too where the site can send email.

	The caller decides who; this only drops disabled and non-desk users. Never raises -- a
	notification that cannot go out is never a reason to refuse the save that caused it.
	"""
	users = {user for user in users or () if user and user != "Guest"}
	if not users:
		return
	try:
		rows = frappe.get_all(
			"User",
			filters={"name": ["in", list(users)], "enabled": 1, "user_type": "System User"},
			fields=["name", "email"],
		)
		emails = [row.email for row in rows if row.email]
		if not emails:
			return
		frappe.enqueue(
			"frappe.desk.doctype.notification_log.notification_log.make_notification_logs",
			doc=frappe._dict(
				type="Alert",
				document_type=DOCTYPE,
				document_name=doc.name,
				subject=subject,
				from_user=frappe.session.user,
				email_content=body,
			),
			# make_notification_logs finds its users BY EMAIL, not by name
			users=emails,
			now=frappe.flags.in_test,
			enqueue_after_commit=True,
		)
		_send_email(rows, subject, doc, body)
	except Exception:
		frappe.log_error(frappe.get_traceback(), "sf_trading: ticket notification failed")


def _send_email(rows, subject, doc, body):
	"""Best effort: only with an outgoing Email Account, only to users who still take email."""
	if not frappe.db.exists("Email Account", {"enable_outgoing": 1}):
		return
	recipients = [
		row.email
		for row in rows
		if row.email and is_notifications_enabled(row.name) and is_email_notifications_enabled(row.name)
	]
	if not recipients:
		return
	message = "".join(
		[
			"<p>", subject, "</p>",
			f"<div>{body}</div>" if body else "",
			'<p><a href="', get_url_to_form(DOCTYPE, doc.name), '">',
			escape_html(_("Open {0}").format(doc.name)), "</a></p>",
		]
	)
	try:
		frappe.sendmail(
			recipients=recipients,
			subject=strip_html(subject),
			message=message,
			reference_doctype=DOCTYPE,
			reference_name=doc.name,
		)
	except frappe.OutgoingEmailError:
		# no usable outgoing account -- email is optional, the bell has already rung
		pass


def _who(user=None) -> str:
	return frappe.bold(escape_html(get_fullname(user or frappe.session.user)))


def _what(doc) -> str:
	return f"{frappe.bold(doc.name)}: {escape_html(doc.subject or '')}"


# ── The form's buttons ──────────────────────────────────────────────────────────────

@frappe.whitelist()
def set_status(ticket: str, status: str, note: str | None = None):
	"""Start Work / Put On Hold / Resolve / Confirm & Close / Reopen.

	Who may do which is the controller's rule; this only carries the note along. A Resolve note
	is the resolution itself; any other note goes on the ticket as a comment.
	"""
	if status not in STATUSES:
		frappe.throw(_("Unknown ticket status: {0}").format(escape_html(status)))
	doc = frappe.get_doc(DOCTYPE, ticket)
	doc.check_permission("write")

	note = (note or "").strip()
	note_html = None
	if status == RESOLVED:
		if note:
			doc.resolution_details = note
	elif note:
		note_html = escape_html(note).replace("\n", "<br>")
		doc.flags.status_note = note_html

	status_changes = doc.status != status
	doc.status = status
	doc.save()
	if note_html:
		_post_note(doc, note_html, quiet=status_changes)


@frappe.whitelist()
def assign(ticket: str, user: str):
	"""Assign / Assign to Me / Hand Over. Who may allocate is the controller's rule."""
	doc = frappe.get_doc(DOCTYPE, ticket)
	doc.check_permission("write")
	doc.assignee = user
	doc.save()


def _post_note(doc, note_html, quiet):
	frappe.flags.sf_ticket_note = quiet
	try:
		doc.add_comment("Comment", note_html)
	finally:
		frappe.flags.sf_ticket_note = False


# ── Desk furniture: the Tickets workspace and the Help menu entry ────────────────────

WORKSPACE = "Tickets"
MODULE = "Sf Trading"
NEW_TICKET_ROUTE = "/app/ticket/new"
HELP_MENU_LABEL = "Raise a Support Ticket"
QUICK_LIST = "Recent Tickets"

_NOT_DONE = '"status": ["not in", ["Resolved", "Closed"]]'
SHORTCUTS = (
	# label, doc_view, stats_filter (a JS expression the desk evaluates), count format
	("Raise a Ticket", "New", None, None),
	("My Open Tickets", "List", '{"raised_by": frappe.session.user, ' + _NOT_DONE + "}", "{} Open"),
	("Assigned to Me", "List", '{"assignee": frappe.session.user, ' + _NOT_DONE + "}", "{} Pending"),
	("Unassigned", "List", '{"assignee": ["is", "not set"], ' + _NOT_DONE + "}", "{} Waiting"),
	("All Tickets", "List", None, None),
)


def workspace_content():
	blocks = [{"type": "header", "data": {"text": '<span class="h4"><b>Support Tickets</b></span>', "col": 12}}]
	blocks += [{"type": "shortcut", "data": {"shortcut_name": label, "col": 3}} for label, *_rest in SHORTCUTS]
	blocks += [
		{"type": "spacer", "data": {"col": 12}},
		{"type": "quick_list", "data": {"quick_list_name": QUICK_LIST, "col": 12}},
	]
	return blocks


def ensure_workspace():
	"""The Tickets workspace, for every desk user. A copy somebody has rearranged is theirs."""
	if frappe.db.exists("Workspace", WORKSPACE):
		doc = frappe.get_doc("Workspace", WORKSPACE)
		if SHORTCUTS[0][0] in (doc.content or ""):
			return
	else:
		doc = frappe.get_doc(
			{
				"doctype": "Workspace", "name": WORKSPACE, "label": WORKSPACE, "title": WORKSPACE,
				"module": MODULE, "public": 1, "icon": "support",
			}
		)

	doc.set("shortcuts", [])
	doc.set("quick_lists", [])
	doc.content = json.dumps(workspace_content())
	for label, view, stats_filter, count_format in SHORTCUTS:
		doc.append(
			"shortcuts",
			{
				"label": label, "type": "DocType", "link_to": DOCTYPE, "doc_view": view,
				"stats_filter": stats_filter, "format": count_format,
			},
		)
	doc.append("quick_lists", {"document_type": DOCTYPE, "label": QUICK_LIST, "quick_list_filter": json.dumps([])})
	if doc.is_new():
		doc.insert(ignore_permissions=True)
	else:
		doc.save(ignore_permissions=True)


def ensure_help_menu_item():
	"""Help -> Raise a Support Ticket, first in the menu people already look in.

	Added once. Hide it from Navbar Settings rather than deleting it -- a deleted row comes back
	on the next migrate, a hidden one stays hidden.
	"""
	settings = frappe.get_single("Navbar Settings")
	if any((row.route or "") == NEW_TICKET_ROUTE for row in settings.help_dropdown):
		return
	row = settings.append(
		"help_dropdown", {"item_label": HELP_MENU_LABEL, "item_type": "Route", "route": NEW_TICKET_ROUTE}
	)
	settings.help_dropdown.remove(row)
	settings.help_dropdown.insert(0, row)
	for idx, item in enumerate(settings.help_dropdown, start=1):
		item.idx = idx
	settings.save(ignore_permissions=True)


def setup():
	"""after_migrate entry point. Each step is guarded: furniture never aborts a migrate."""
	for step in (ensure_workspace, ensure_help_menu_item):
		try:
			step()
		except Exception:
			frappe.log_error(frappe.get_traceback(), f"sf_trading tickets: {step.__name__} failed")
