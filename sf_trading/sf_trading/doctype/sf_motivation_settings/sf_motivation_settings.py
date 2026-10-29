# sf_trading/sf_trading/doctype/sf_motivation_settings/sf_motivation_settings.py
# Copyright (c) 2026, Enfono Technologies and contributors
# For license information, please see license.txt

from frappe.model.document import Document


class SFMotivationSettings(Document):
	def validate(self):
		self.card_title = (self.card_title or "").strip() or "Thought for today"
