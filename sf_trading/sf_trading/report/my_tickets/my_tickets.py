# Copyright (c) 2026, Enfono Technologies and contributors
# For license information, please see license.txt

"""My Tickets: every support ticket a user raised or is part of, and which of them wait on them.

"Part of" is the same set a ticket's own notifications reach (sf_trading.ticket): raised it, has
it (or had it, until it was resolved), is on its Keep Informed table, has commented on it, or was
@mentioned into it. Each row says how, and Next Step picks out the tickets waiting on this user:
one to work on, a question to answer, a fix to confirm.

Rows are read through frappe.get_list, so the report never lists a ticket the user can no longer
open -- somebody who commented and was later taken off it, say. A Ticket Manager may look at
another user's tickets with the User filter; everyone else always sees their own.
"""

from collections import defaultdict

import frappe
from frappe import _
from frappe.query_builder.functions import Count, Max
from frappe.utils import date_diff, get_datetime, nowdate

from sf_trading import ticket
from sf_trading.query import IN_BATCH, fetch_in

# how a user is part of a ticket -- the values the "My Part" filter sends
RAISED = "Raised"
ASSIGNED = "Assigned"
WATCHING = "Keep Informed"
COMMENTED = "Commented"
MENTIONED = "Mentioned"
PARTS = (RAISED, ASSIGNED, WATCHING, COMMENTED, MENTIONED)

# the status filter's two groupings; anything else is one status
ACTIVE = "Active"
ALL = "All"

FIELDS = [
	"name", "subject", "status", "priority", "ticket_type", "module_area", "raised_by",
	"raised_by_name", "assignee", "assignee_name", "opening_date", "resolved_on", "closed_on", "modified",
]


def execute(filters=None):
	filters = frappe._dict(filters or {})
	user = filters.user or frappe.session.user
	if user != frappe.session.user and not ticket.is_manager():
		frappe.throw(_("Only a Ticket Manager can look at another user's tickets."), frappe.PermissionError)

	parts = _parts(user)
	tickets = _visible_tickets(list(parts))
	activity = _activity(list(tickets))

	entries = [(doc, parts[name], _next_step(doc, parts[name])) for name, doc in tickets.items()]
	summary = _summary(entries)

	rows = [_row(doc, how, step, activity.get(doc.name)) for doc, how, step in entries if _wanted(doc, how, step, filters)]
	# what waits on the user first, then the most recently active
	rows.sort(key=lambda row: get_datetime(row["last_activity"]), reverse=True)
	rows.sort(key=lambda row: not row["next_step"])
	return _columns(), rows, None, None, summary


def _parts(user) -> dict:
	"""ticket -> how the user is part of it."""
	parts = defaultdict(set)

	def add(names, part):
		for name in names:
			if name:
				parts[name].add(part)

	add(frappe.get_all("Ticket", filters={"raised_by": user}, pluck="name"), RAISED)
	# raised on somebody else's behalf counts as raised by whoever typed it in
	add(frappe.get_all("Ticket", filters={"owner": user}, pluck="name"), RAISED)
	add(frappe.get_all("Ticket", filters={"assignee": user}, pluck="name"), ASSIGNED)
	add(
		frappe.get_all(
			"ToDo",
			filters={"reference_type": "Ticket", "allocated_to": user, "status": ["in", ["Open", "Closed"]]},
			pluck="reference_name",
		),
		ASSIGNED,
	)
	add(frappe.get_all("Ticket Watcher", filters={"parenttype": "Ticket", "user": user}, pluck="parent"), WATCHING)
	add(
		frappe.get_all(
			"Comment",
			filters={"reference_doctype": "Ticket", "comment_type": "Comment", "owner": user},
			pluck="reference_name",
		),
		COMMENTED,
	)
	add(
		frappe.get_all("DocShare", filters={"share_doctype": "Ticket", "user": user, "everyone": 0}, pluck="share_name"),
		MENTIONED,
	)
	return parts


def _visible_tickets(names) -> dict:
	# get_list, not get_all: only the tickets the person running the report may open today
	return {row.name: row for row in fetch_in("Ticket", names, fields=FIELDS, permissions=True)}


def _activity(names) -> dict:
	"""ticket -> (number of replies, when the last one was posted)."""
	comment = frappe.qb.DocType("Comment")
	out = {}
	for start in range(0, len(names), IN_BATCH):
		rows = (
			frappe.qb.from_(comment)
			.select(comment.reference_name, Count(comment.name).as_("replies"), Max(comment.creation).as_("last_reply"))
			.where(
				(comment.reference_doctype == "Ticket")
				& (comment.comment_type == "Comment")
				& comment.reference_name.isin(names[start : start + IN_BATCH])
			)
			.groupby(comment.reference_name)
		).run(as_dict=True)
		out.update({row.reference_name: (row.replies, row.last_reply) for row in rows})
	return out


def _next_step(doc, how) -> str:
	"""What this ticket is waiting for from the user, if anything."""
	if ASSIGNED in how and doc.status == ticket.OPEN:
		return _("Start work, or hand it over")
	if ASSIGNED in how and doc.status == ticket.IN_PROGRESS:
		return _("Work on it and resolve it")
	if RAISED in how and doc.status == ticket.ON_HOLD:
		return _("Answer what was asked")
	if RAISED in how and doc.status == ticket.RESOLVED:
		return _("Confirm & Close, or Reopen")
	return ""


def _wanted(doc, how, step, filters) -> bool:
	status = filters.status or ACTIVE
	if status == ACTIVE and doc.status == ticket.CLOSED:
		return False
	if status not in (ACTIVE, ALL) and doc.status != status:
		return False
	if filters.part and filters.part not in how:
		return False
	if filters.needs_me and not step:
		return False
	if filters.from_date and get_datetime(doc.opening_date).date() < get_datetime(filters.from_date).date():
		return False
	return True


def _row(doc, how, step, activity) -> dict:
	replies, last_reply = activity or (0, None)
	ended = doc.closed_on or doc.resolved_on or nowdate()
	return {
		"ticket": doc.name,
		"subject": doc.subject,
		"status": doc.status,
		"next_step": step,
		"part": ", ".join(_(part) for part in PARTS if part in how),
		"priority": doc.priority,
		"assignee": doc.assignee_name or doc.assignee,
		"raised_by": doc.raised_by_name or doc.raised_by,
		"ticket_type": doc.ticket_type,
		"module_area": doc.module_area,
		"opening_date": doc.opening_date,
		"last_activity": max(when for when in (doc.modified, last_reply) if when),
		"replies": replies,
		"age_days": date_diff(ended, doc.opening_date) if doc.opening_date else None,
	}


def _summary(entries) -> list:
	"""Counted over every ticket the user is part of, whatever the filters narrow the table to."""
	needs = sum(1 for doc, how, step in entries if step)
	active = sum(1 for doc, how, step in entries if doc.status != ticket.CLOSED)
	raised = sum(1 for doc, how, step in entries if RAISED in how and doc.status not in ticket.DONE)
	assigned = sum(1 for doc, how, step in entries if ASSIGNED in how and doc.status not in ticket.DONE)
	to_confirm = sum(1 for doc, how, step in entries if RAISED in how and doc.status == ticket.RESOLVED)
	return [
		{"value": needs, "label": _("Needs Action"), "indicator": "Red" if needs else "Green", "datatype": "Int"},
		{"value": active, "label": _("Active"), "indicator": "Blue", "datatype": "Int"},
		{"value": raised, "label": _("Raised, Not Yet Resolved"), "indicator": "Blue", "datatype": "Int"},
		{"value": assigned, "label": _("Assigned, Not Yet Resolved"), "indicator": "Orange" if assigned else "Green", "datatype": "Int"},
		{"value": to_confirm, "label": _("Resolved, Awaiting Confirmation"), "indicator": "Orange" if to_confirm else "Green", "datatype": "Int"},
	]


def _columns() -> list:
	return [
		{"fieldname": "ticket", "label": _("Ticket"), "fieldtype": "Link", "options": "Ticket", "width": 140},
		{"fieldname": "subject", "label": _("Subject"), "fieldtype": "Data", "width": 260},
		{"fieldname": "status", "label": _("Status"), "fieldtype": "Data", "width": 110},
		{"fieldname": "next_step", "label": _("Next Step"), "fieldtype": "Data", "width": 210},
		{"fieldname": "part", "label": _("My Part"), "fieldtype": "Data", "width": 170},
		{"fieldname": "priority", "label": _("Priority"), "fieldtype": "Data", "width": 80},
		{"fieldname": "assignee", "label": _("Assigned To"), "fieldtype": "Data", "width": 150},
		{"fieldname": "raised_by", "label": _("Raised By"), "fieldtype": "Data", "width": 150},
		{"fieldname": "ticket_type", "label": _("Type"), "fieldtype": "Data", "width": 130},
		{"fieldname": "module_area", "label": _("Area"), "fieldtype": "Data", "width": 120},
		{"fieldname": "opening_date", "label": _("Opened On"), "fieldtype": "Datetime", "width": 150},
		{"fieldname": "last_activity", "label": _("Last Activity"), "fieldtype": "Datetime", "width": 150},
		{"fieldname": "replies", "label": _("Replies"), "fieldtype": "Int", "width": 80},
		{"fieldname": "age_days", "label": _("Age (Days)"), "fieldtype": "Int", "width": 90},
	]
