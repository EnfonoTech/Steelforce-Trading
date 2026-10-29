# sf_trading/motivation.py
# Copyright (c) 2026, Enfono Technologies and contributors
# For license information, please see license.txt

"""A daily motivation: one short line, shown once, the first time each person opens the desk that day.

A manager keeps a bank of **SF Motivation** messages. Each has an optional "Show On" date and an
optional audience (the same role / department / branch / person lines an announcement has; empty
means everyone). The day's message is chosen like this:

1. a message whose "Show On" is today wins (the first by name, if there are several);
2. otherwise the messages with no date take turns: "In order" walks the list one a day, "Random"
   picks one a day. Both are worked out from the date alone, so the choice is the same for everyone
   in that audience and does not change when the page is reloaded.

The card appears on the first desk load of the day because the boot payload carries it until the
browser reports it shown (`mark_motivation_seen`), which writes one **SF Motivation Seen** row per
person per day. That row is also what keeps My Updates showing the very message the person saw, even
if the bank was edited later that day.
"""

import random
import re

import frappe
from frappe import _
from frappe.utils import add_days, cint, cstr, getdate, nowdate

from sf_trading import announcements as ann

DOCTYPE = "SF Motivation"
SETTINGS = "SF Motivation Settings"
SEEN = "SF Motivation Seen"

MESSAGE_MAX = 280
AUTHOR_MAX = 140
BULK_MAX = 200
KEEP_SEEN_DAYS = 60
DEFAULT_TITLE = "Thought for today"
IN_ORDER, RANDOM = "In order", "Random"

# "message — who said it": an em dash, an en dash or a pipe with spaces around it
_AUTHOR_SEPARATOR = re.compile(r"\s+[—–|]\s+")
_BULLET = re.compile(r"^\s*(?:[-*•]|\d+[.)])\s+")


def settings() -> frappe._dict:
	"""The switches, with their defaults when the settings were never saved."""
	doc = frappe.get_cached_doc(SETTINGS)
	return frappe._dict(
		enabled=cint(doc.enabled),
		rotation=doc.rotation or IN_ORDER,
		card_title=cstr(doc.card_title).strip() or DEFAULT_TITLE,
		show_in_hub=cint(doc.show_in_hub),
	)


def _seen_name(user, day) -> str:
	return f"{user}::{day}"


# ── Choosing the day's message ──────────────────────────────────────────────────────────

def _pool(user) -> list:
	"""The enabled messages meant for this person: no audience lines at all, or a line that fits them."""
	rows = frappe.get_all(
		DOCTYPE,
		filters={"enabled": 1},
		fields=["name", "message", "author", "show_on"],
		order_by="creation asc, name asc",
	)
	if not rows:
		return []
	names = [row.name for row in rows]
	audience = frappe.qb.DocType(ann.AUDIENCE)
	limited = set(
		frappe.get_all(ann.AUDIENCE, filters={"parenttype": DOCTYPE, "parent": ["in", names]}, pluck="parent")
	)
	fitting = set(
		frappe.qb.from_(audience)
		.select(audience.parent)
		.distinct()
		.where(audience.parenttype == DOCTYPE)
		.where(audience.parent.isin(names))
		.where(ann.audience_fits(audience, user, ann.user_profile(user)))
		.run(pluck=True)
	)
	return [row for row in rows if row.name not in limited or row.name in fitting]


def todays_message(user, day=None):
	"""The message this person is meant to see on `day` (today by default), or None when there is none."""
	day = getdate(day or nowdate())
	pool = _pool(user)

	dated = sorted((row for row in pool if row.show_on and getdate(row.show_on) == day), key=lambda row: row.name)
	if dated:
		return dated[0]

	rotation = [row for row in pool if not row.show_on]
	if not rotation:
		return None
	if settings().rotation == RANDOM:
		# seeded by the date, so the draw is the same all day and for everyone in the same pool
		index = random.Random(f"{day.isoformat()}:{len(rotation)}").randrange(len(rotation))
	else:
		index = day.toordinal() % len(rotation)
	return rotation[index]


def _shape(row, options) -> dict:
	return {
		"name": row.name,
		"message": row.message,
		"author": row.author,
		"title": options.card_title,
		"show_in_hub": options.show_in_hub,
	}


# ── What the desk asks for ──────────────────────────────────────────────────────────────

def _mark_seen(user, motivation) -> bool:
	"""Use up today for this person. True only for the call that did it, so two tabs cannot both show the card."""
	today = nowdate()
	if frappe.db.exists(SEEN, _seen_name(user, today)):
		return False
	try:
		frappe.get_doc({"doctype": SEEN, "user": user, "on_date": today, "motivation": motivation}).insert(
			ignore_permissions=True
		)
	except frappe.DuplicateEntryError:
		# another tab got there first
		frappe.clear_last_message()
		return False
	return True


def claim(user):
	"""Today's message for the first desk load of the day, and today is used up in the same step.

	Asked of the server on every load and never read from the boot payload: Frappe caches that payload per
	person, so a message put in it came back on every refresh until the cache happened to clear.
	"""
	options = settings()
	if not options.enabled:
		return None
	row = todays_message(user)
	if not row or not _mark_seen(user, row.name):
		return None
	return _shape(row, options)


@frappe.whitelist(methods=["POST"])
def claim_motivation():
	"""The desk's first load of the day gets the card; every later load, in any tab, gets None."""
	return claim(ann._desk_user())


@frappe.whitelist()
def get_motivation():
	"""Today's message for My Updates and the preview button: the one the person was shown, else the one due."""
	user = ann._desk_user()
	options = settings()
	if not options.enabled:
		return None
	row = None
	shown = frappe.db.get_value(SEEN, _seen_name(user, nowdate()), "motivation")
	if shown:
		row = frappe.db.get_value(DOCTYPE, shown, ["name", "message", "author"], as_dict=True)
	row = row or todays_message(user)
	return _shape(row, options) if row else None


@frappe.whitelist()
def mark_motivation_seen(motivation: str) -> dict:
	"""Kept for a browser still running the old script: today is used up for this person. Safe to call twice."""
	user = ann._desk_user()
	if not frappe.db.exists(DOCTYPE, motivation):
		frappe.throw(_("That message no longer exists."), frappe.DoesNotExistError)
	return {"first": 1 if _mark_seen(user, motivation) else 0}


# ── Filling the bank ────────────────────────────────────────────────────────────────────

def _split_line(raw):
	"""One pasted line into (message, author): a leading bullet or number is dropped, " — Name" or " | Name" is the author."""
	line = _BULLET.sub("", cstr(raw)).strip()
	found = list(_AUTHOR_SEPARATOR.finditer(line))
	if found:
		author = line[found[-1].end() :].strip()
		if author and len(author) <= AUTHOR_MAX:
			return line[: found[-1].start()].strip(), author
	return line, None


@frappe.whitelist()
def add_motivations(lines: str) -> dict:
	"""Add many messages at once, one per line. A line already in the bank, or too long, is skipped and counted."""
	ann._require_manager()
	rows = [row for row in cstr(lines).splitlines() if row.strip()]
	if len(rows) > BULK_MAX:
		frappe.throw(_("Add at most {0} messages at a time. You pasted {1} lines.").format(BULK_MAX, len(rows)))

	have = {cstr(message).strip().lower() for message in frappe.get_all(DOCTYPE, pluck="message")}
	added = duplicates = too_long = 0
	for raw in rows:
		message, author = _split_line(raw)
		if not message:
			continue
		if len(message) > MESSAGE_MAX:
			too_long += 1
			continue
		if message.lower() in have:
			duplicates += 1
			continue
		frappe.get_doc({"doctype": DOCTYPE, "message": message, "author": author, "enabled": 1}).insert(
			ignore_permissions=True
		)
		have.add(message.lower())
		added += 1
	return {"added": added, "duplicates": duplicates, "too_long": too_long}


def prune_seen():
	"""daily: only today's row is ever read, so keep a couple of months of "who was shown what" and drop the rest."""
	if not frappe.db.table_exists(SEEN):
		return
	frappe.db.delete(SEEN, {"on_date": ["<", add_days(nowdate(), -KEEP_SEEN_DAYS)]})
