# sf_trading/sf_trading/doctype/sf_announcement_read/sf_announcement_read.py
# Copyright (c) 2026, Enfono Technologies and contributors
# For license information, please see license.txt

"""Who has read (and acknowledged) which announcement. One row per person per announcement.

The name is `<announcement>::<user>`, so the database itself refuses a second row for the same
pair -- two browser tabs marking the same update read at once cannot double-count it.
"""

from frappe.model.document import Document


class SFAnnouncementRead(Document):
	def autoname(self):
		self.name = f"{self.announcement}::{self.user}"
