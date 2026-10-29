# sf_trading/sf_trading/doctype/sf_motivation_seen/sf_motivation_seen.py
# Copyright (c) 2026, Enfono Technologies and contributors
# For license information, please see license.txt

"""The day a person was shown their motivation card. One row per person per day.

The name is `<user>::<day>`, so the database itself refuses a second row: two browser tabs opening
the desk at the same moment cannot both count as "the first time today".
"""

from frappe.model.document import Document


class SFMotivationSeen(Document):
	def autoname(self):
		self.name = f"{self.user}::{self.on_date}"
