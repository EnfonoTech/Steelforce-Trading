# sf_trading/sf_trading/doctype/sf_announcement/sf_announcement.py
# Copyright (c) 2026, Enfono Technologies and contributors
# For license information, please see license.txt

"""An update a manager posts for staff: an announcement, a new feature, a rule, a report, a notice.

Who sees it, how it is published and who has read it are all decided in sf_trading/announcements.py.
The form only offers buttons for them; the rules below hold however the document is saved.
"""

from frappe.model.document import Document

from sf_trading import announcements


class SFAnnouncement(Document):
	def validate(self):
		announcements.validate_announcement(self)

	def on_trash(self):
		announcements.clean_up(self)
