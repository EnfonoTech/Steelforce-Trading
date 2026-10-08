# sf_trading/sf_trading/doctype/ticket/ticket.py
# Copyright (c) 2026, Enfono Technologies and contributors
# For license information, please see license.txt

"""A support ticket a desk user raises about the software.

Who may change what is decided here, on the server: the form's buttons are one way in, but
the API, the sidebar's Assign To and a manager editing a field directly all reach the same
save without any of the form's script running. The notifications, the permission hooks and
the actions behind the buttons live in sf_trading/ticket.py.
"""

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import cint, now_datetime, strip_html, time_diff_in_seconds

from sf_trading import ticket as tickets


class Ticket(Document):
	def before_validate(self):
		if not self.raised_by:
			self.raised_by = frappe.session.user
		self._drop_duplicate_watchers()

	def validate(self):
		before = self.get_doc_before_save()
		if not self.flags.ignore_ticket_rules:
			tickets.validate_change(self, before)
		if self.assignee and self.has_value_changed("assignee") and not frappe.db.exists(
			"User", {"name": self.assignee, "enabled": 1, "user_type": "System User"}
		):
			frappe.throw(_("{0} is not an active desk user, so the ticket cannot go to them.").format(self.assignee))
		if self.reference_name and not self.reference_doctype:
			frappe.throw(_("Pick the Document Type before the Document."))
		if self.status == tickets.RESOLVED and not strip_html(self.resolution_details or "").strip():
			frappe.throw(
				_("Write down how it was resolved before marking the ticket Resolved."),
				title=_("Resolution Missing"),
			)
		self._set_tracking(before)

	def on_update(self):
		tickets.after_save(self, self.get_doc_before_save())

	def _drop_duplicate_watchers(self):
		seen, rows = set(), []
		for row in self.get("watchers") or []:
			if row.user and row.user not in seen:
				seen.add(row.user)
				rows.append(row)
		self.set("watchers", rows)

	def _set_tracking(self, before):
		"""Opened, first response, resolved and closed -- the ticket's own clock."""
		now = now_datetime()
		self.opening_date = self.opening_date or now
		# a reply records first_response_on without touching `modified`, so a form opened before
		# that reply still carries the blank, and would wipe the value on its next save
		if before and before.first_response_on and not self.first_response_on:
			self.first_response_on = before.first_response_on

		previous = before.status if before else None
		if self.status == previous:
			return

		user = frappe.session.user
		if before and not self.first_response_on and user not in (self.raised_by, self.owner):
			self.first_response_on = now

		if previous in tickets.DONE and self.status not in tickets.DONE:
			self.reopen_count = cint(self.reopen_count) + 1
			self.resolved_on = self.resolved_by = self.closed_on = None
			self.resolution_time = 0

		if self.status == tickets.RESOLVED:
			self.resolved_on = now
			self.resolved_by = user
			self.resolution_time = time_diff_in_seconds(now, self.opening_date)
		elif self.status == tickets.CLOSED:
			self.closed_on = now
