# Copyright (c) 2026, Enfono Technologies and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import cint, flt


class SFTradingSettings(Document):
	"""Steel Force's own switches, in Steel Force's own app.

	These started life as fields on permission_manager's PM Settings, which is shared by every
	client running that app -- so a change made for this client shipped everywhere and had to be
	written defensively. They live here instead: one Single, in the client's own app, holding the
	policy while permission_manager keeps only the approval machinery.
	"""

	def validate(self):
		if cint(self.restrict_sales_return) and cint(self.sales_return_days) < 0:
			frappe.throw(_("Days Allowed for a Return cannot be negative."))

		if cint(self.si_return_approval_enabled) and cint(self.si_return_amount_restriction):
			if flt(self.si_return_approval_threshold) < 0:
				frappe.throw(_("Approval Threshold cannot be negative."))

	def on_update(self):
		# the approval gate decides whether a Sales Invoice workflow governs a document, and the
		# desk learns that at boot — so a change here has to invalidate both caches
		frappe.clear_cache(doctype="SF Trading Settings")
		try:
			from permission_manager.permission_manager.workflow import clear_workflow_doctype_cache

			clear_workflow_doctype_cache()
		except Exception:
			# permission_manager is not required for the return window half of this doctype
			pass
