# sf_trading/sf_trading/doctype/sf_motivation/sf_motivation.py
# Copyright (c) 2026, Enfono Technologies and contributors
# For license information, please see license.txt

"""One line of daily motivation. Which one a person sees, and when, is decided in sf_trading/motivation.py."""

import frappe
from frappe import _
from frappe.model.document import Document

from sf_trading import announcements, motivation


class SFMotivation(Document):
	def validate(self):
		self.message = (self.message or "").strip()
		self.author = (self.author or "").strip() or None
		if not self.message:
			frappe.throw(_("Write the message."))
		if len(self.message) > motivation.MESSAGE_MAX:
			frappe.throw(
				_("The message is {0} characters. Keep it under {1}: a short line is read, a long one is skipped.").format(
					len(self.message), motivation.MESSAGE_MAX
				)
			)
		announcements.check_audience(self)
