# Copyright (c) 2026, Enfono Technologies and contributors
# For license information, please see license.txt

"""Ticket Closing Efficiency: who resolves the support tickets, who closes them, and how well.

Two rankings of the same tickets:

  Resolvers  the person a ticket was with when it was marked Resolved (`resolved_by`), ranked by how many
             they resolved in the period, then by how fast. Columns beside the count say whether the work
             held (Reopened, Reopen %), how quickly they answered (Avg First Response) and finished
             (Avg Resolution, Fastest, Slowest, Within 24 Hrs), how much of it was urgent, and what they
             still have open now.
  Closers    the person who confirmed a ticket Closed (`closed_by`) -- usually whoever raised it -- or the
             system, when the ticket stayed Resolved for the wait and closed itself (`auto_closed`).
             A close from before the field existed is read from the version log (patches/v0_1/
             ticket_closed_by_backfill.py); one with no record shows as Not Recorded.

A ticket counts on the day it was finished: resolved, or closed when it never was resolved. A reopen clears
a ticket's resolver and times, so a ticket that came back counts only once it is finished again, but its
Reopened mark stays (`reopen_count`). Open Now is today's backlog and ignores the dates. Only Ticket
Managers and System Managers may open it: it ranks people, across tickets they were never part of.
"""

import frappe
from frappe import _
from frappe.query_builder.functions import Coalesce, Count
from frappe.utils import add_days, flt, getdate, time_diff_in_seconds

from sf_trading import ticket

RESOLVERS = "Resolvers"
CLOSERS = "Closers"

AUTO_LABEL = "Auto-closed (system)"
UNKNOWN_LABEL = "Not Recorded"

FIELDS = (
	"name", "status", "priority", "ticket_type", "module_area", "assignee", "resolved_by", "closed_by",
	"auto_closed", "opening_date", "first_response_on", "resolved_on", "closed_on", "resolution_time", "reopen_count",
)
DAY = 86400


def execute(filters=None):
	filters = frappe._dict(filters or {})
	if not ticket.is_manager():
		frappe.throw(_("Only a Ticket Manager can see who is closing tickets."), frappe.PermissionError)

	closers = filters.view == CLOSERS
	tickets = _finished(filters, closers)
	names = _full_names({t.closed_by if closers else t.resolved_by for t in tickets})
	if closers:
		rows = closer_rows(tickets, names)
		columns = _closer_columns()
		label, key = _("Closed"), "closed"
	else:
		rows = resolver_rows(tickets, _backlog(filters), names)
		columns = _resolver_columns()
		label, key = _("Resolved"), "resolved"
	return columns, rows, None, _chart(rows, label, key), _summary(tickets, rows, closers)


# ── The data ────────────────────────────────────────────────────────────────────────

def _scope(query, table, filters):
	for field in ("ticket_type", "module_area", "priority"):
		if filters.get(field):
			query = query.where(table[field] == filters[field])
	return query


def _finished(filters, closers):
	"""Resolved and Closed tickets finished inside the dates, as plain dicts."""
	table = frappe.qb.DocType("Ticket")
	finished_on = Coalesce(table.resolved_on, table.closed_on)
	query = frappe.qb.from_(table).select(*[table[f] for f in FIELDS])
	if closers:
		query = query.where(table.status == ticket.CLOSED)
	else:
		query = query.where(table.status.isin([ticket.RESOLVED, ticket.CLOSED]) & table.resolved_by.isnotnull())
	if filters.get("from_date"):
		query = query.where(finished_on >= getdate(filters.from_date))
	if filters.get("to_date"):
		query = query.where(finished_on < add_days(getdate(filters.to_date), 1))
	if filters.get("user"):
		query = query.where((table.closed_by if closers else table.resolved_by) == filters.user)
	return [frappe._dict(row) for row in _scope(query, table, filters).run(as_dict=True)]


def _backlog(filters) -> dict:
	"""Tickets each person has open today -- the dates do not apply."""
	table = frappe.qb.DocType("Ticket")
	query = (
		frappe.qb.from_(table)
		.select(table.assignee, Count("*").as_("n"))
		.where(~table.status.isin([ticket.RESOLVED, ticket.CLOSED]) & table.assignee.isnotnull())
		.groupby(table.assignee)
	)
	return {row.assignee: row.n for row in _scope(query, table, filters).run(as_dict=True)}


def _full_names(users) -> dict:
	users = [u for u in users if u]
	if not users:
		return {}
	return dict(frappe.get_all("User", filters={"name": ["in", users]}, fields=["name", "full_name"], as_list=True))


# ── The two rankings (pure: tickets in, rows out) ───────────────────────────────────

def _hours(seconds):
	return round(flt(seconds) / 3600, 1)


def _mean(values):
	values = list(values)
	return round(sum(values) / len(values), 1) if values else None


def _first_response_hours(t):
	if not (t.first_response_on and t.opening_date):
		return None
	seconds = time_diff_in_seconds(t.first_response_on, t.opening_date)
	return _hours(seconds) if seconds >= 0 else None


def resolver_rows(tickets, backlog, names) -> list:
	"""One row per person who resolved something, best first: most resolved, then quickest, then fewest reopens."""
	by_user = {}
	for t in tickets:
		by_user.setdefault(t.resolved_by, []).append(t)

	rows = []
	for user, mine in by_user.items():
		resolved = len(mine)
		timed = [flt(t.resolution_time) for t in mine if flt(t.resolution_time) > 0]
		reopened = sum(1 for t in mine if flt(t.reopen_count) > 0)
		rows.append(
			{
				"user": user,
				"full_name": names.get(user) or user,
				"resolved": resolved,
				"closed": sum(1 for t in mine if t.status == ticket.CLOSED),
				"auto_closed": sum(1 for t in mine if flt(t.auto_closed)),
				"waiting": sum(1 for t in mine if t.status == ticket.RESOLVED),
				"reopened": reopened,
				"reopen_pct": round(reopened * 100.0 / resolved, 1),
				"avg_first_response": _mean(h for h in map(_first_response_hours, mine) if h is not None),
				"avg_resolution": _mean(_hours(s) for s in timed),
				"fastest": _hours(min(timed)) if timed else None,
				"slowest": _hours(max(timed)) if timed else None,
				"within_24h": round(sum(1 for s in timed if s <= DAY) * 100.0 / len(timed), 1) if timed else None,
				"urgent_high": sum(1 for t in mine if t.priority in ("Urgent", "High")),
				"open_now": backlog.get(user, 0),
			}
		)
	rows.sort(key=lambda r: (-r["resolved"], r["avg_resolution"] if r["avg_resolution"] is not None else float("inf"), r["reopen_pct"], r["user"]))
	for rank, row in enumerate(rows, start=1):
		row["rank"] = rank
	return rows


def closer_rows(tickets, names) -> list:
	"""One row per person who closed something, plus the system and the closes nobody recorded."""
	by_closer = {}
	for t in tickets:
		key = AUTO_LABEL if flt(t.auto_closed) else (t.closed_by or UNKNOWN_LABEL)
		by_closer.setdefault(key, []).append(t)

	rows = []
	for closer, mine in by_closer.items():
		waits = [time_diff_in_seconds(t.closed_on, t.resolved_on) for t in mine if t.closed_on and t.resolved_on]
		reopened = sum(1 for t in mine if flt(t.reopen_count) > 0)
		rows.append(
			{
				"user": closer if closer in names else None,
				"closer": closer,
				"full_name": names.get(closer) or ("" if closer in (AUTO_LABEL, UNKNOWN_LABEL) else closer),
				"closed": len(mine),
				"avg_days_to_close": _mean(max(w, 0) / DAY for w in waits),
				"same_day": sum(1 for w in waits if 0 <= w <= DAY),
				"without_resolving": sum(1 for t in mine if not t.resolved_on),
				"reopened": reopened,
			}
		)
	rows.sort(key=lambda r: (-r["closed"], r["closer"]))
	for rank, row in enumerate(rows, start=1):
		row["rank"] = rank
	return rows


# ── Columns, chart, summary ─────────────────────────────────────────────────────────

def _column(label, fieldname, fieldtype="Int", width=100, **extra):
	return {"label": _(label), "fieldname": fieldname, "fieldtype": fieldtype, "width": width, **extra}


def _resolver_columns():
	return [
		_column("Rank", "rank", width=55),
		_column("User", "user", "Link", 190, options="User"),
		_column("Name", "full_name", "Data", 150),
		_column("Resolved", "resolved"),
		_column("Closed", "closed", width=80),
		_column("Auto Closed", "auto_closed", width=95),
		_column("Waiting To Close", "waiting", width=120),
		_column("Reopened", "reopened", width=85),
		_column("Reopen %", "reopen_pct", "Percent", 90, precision=1),
		_column("Avg First Response (Hrs)", "avg_first_response", "Float", 150, precision=1),
		_column("Avg Resolution (Hrs)", "avg_resolution", "Float", 140, precision=1),
		_column("Fastest (Hrs)", "fastest", "Float", 105, precision=1),
		_column("Slowest (Hrs)", "slowest", "Float", 105, precision=1),
		_column("Resolved Within 24 Hrs", "within_24h", "Percent", 150, precision=1),
		_column("Urgent / High", "urgent_high", width=100),
		_column("Open Now", "open_now", width=90),
	]


def _closer_columns():
	return [
		_column("Rank", "rank", width=55),
		_column("Closed By", "closer", "Data", 200),
		_column("Name", "full_name", "Data", 150),
		_column("Closed", "closed", width=85),
		_column("Avg Days Resolved To Closed", "avg_days_to_close", "Float", 190, precision=1),
		_column("Closed Within A Day", "same_day", width=140),
		_column("Closed Without Resolving", "without_resolving", width=170),
		_column("Reopened Before", "reopened", width=125),
	]


def _chart(rows, label, key):
	top = rows[:10]
	if not top:
		return None
	return {
		"data": {
			"labels": [(r.get("full_name") or r.get("closer") or r.get("user")) for r in top],
			"datasets": [{"name": label, "values": [r[key] for r in top]}],
		},
		"type": "bar",
	}


def _summary(tickets, rows, closers):
	if closers:
		people = [r for r in rows if r["closer"] not in (AUTO_LABEL, UNKNOWN_LABEL)]
		auto = sum(r["closed"] for r in rows if r["closer"] == AUTO_LABEL)
		return [
			{"value": sum(r["closed"] for r in rows), "label": _("Closed"), "datatype": "Int", "indicator": "Blue"},
			{"value": sum(r["closed"] for r in people), "label": _("Closed By A Person"), "datatype": "Int", "indicator": "Green"},
			{"value": auto, "label": _("Closed Automatically"), "datatype": "Int", "indicator": "Gray"},
			{"value": len(people), "label": _("People Closing"), "datatype": "Int", "indicator": "Blue"},
		]
	total = len(tickets)
	reopened = sum(1 for t in tickets if flt(t.reopen_count) > 0)
	timed = [flt(t.resolution_time) for t in tickets if flt(t.resolution_time) > 0]
	return [
		{"value": total, "label": _("Resolved"), "datatype": "Int", "indicator": "Blue"},
		{"value": sum(1 for t in tickets if t.status == ticket.CLOSED), "label": _("Closed"), "datatype": "Int", "indicator": "Green"},
		{"value": sum(1 for t in tickets if flt(t.auto_closed)), "label": _("Closed Automatically"), "datatype": "Int", "indicator": "Gray"},
		{"value": _mean(_hours(s) for s in timed) or 0, "label": _("Avg Resolution (Hrs)"), "datatype": "Float", "indicator": "Blue"},
		{
			"value": round(reopened * 100.0 / total, 1) if total else 0,
			"label": _("Reopen %"),
			"datatype": "Percent",
			"indicator": "Red" if total and reopened * 4 >= total else "Green",
		},
	]
