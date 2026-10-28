# sf_trading/sf_trading/doctype/party_master_rule_set/party_master_rule_set.py
# Copyright (c) 2026, Enfono Technologies and contributors
# For license information, please see license.txt

import re

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import cint, cstr


class PartyMasterRuleSet(Document):
	"""One set of rules per party type (see sf_trading/party_rules.py). Saved only when every row can be read."""

	def validate(self):
		self.check_required()
		self.check_formats()

	def check_required(self):
		master = frappe.get_meta(self.party_type)
		address = frappe.get_meta("Address")
		for row in self.get("required") or []:
			label = _("Required Details row #{0}").format(row.idx)
			field = cstr(row.fieldname).strip()
			if row.requirement == "Any Field":
				if not master.has_field(field):
					frappe.throw(_("{0}: {1} is not a field of {2}.").format(label, frappe.bold(field), _(self.party_type)))
			elif row.requirement == "Address Field":
				if not address.has_field(field):
					frappe.throw(_("{0}: {1} is not a field of Address.").format(label, frappe.bold(field)))
			if row.requirement == "Contact Numbers" and cint(row.min_count) < 1:
				frappe.throw(_("{0}: the minimum number of contact numbers is at least 1.").format(label))
			if not (cint(row.at_save) or cint(row.at_approval) or cint(row.at_voucher)) and cint(row.enabled):
				frappe.throw(_("{0}: tick At Save, At Approval or At Voucher, or untick Enabled.").format(label))

	def check_formats(self):
		for row in self.get("formats") or []:
			label = _("Format Checks row #{0}").format(row.idx)
			value = cstr(row.value).strip()
			if row.check == "Regular Expression":
				try:
					re.compile(value)
				except re.error as error:
					frappe.throw(_("{0}: the regular expression does not compile ({1}).").format(label, error))
			elif row.check in ("Exact Digits", "Minimum Digits") and not value.isdigit():
				frappe.throw(_("{0}: {1} needs a number of digits.").format(label, _(row.check)))
