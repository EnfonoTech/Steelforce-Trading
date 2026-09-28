# sf_trading/journal_entry_attachment.py
"""Journal Entry before_submit: at least one attachment required.

Client call, 2026-09-28: Journal Entries were submitting with no supporting document at all.
before_submit, not validate -- a brand-new JE has no name yet on its first save, so there is
nowhere to attach a file until after that first insert; the natural flow is create -> attach ->
submit, same reasoning as api/customer_override.py's own in_insert skip.

Exempt whenever is_system_generated is set -- the SAME flag this account's other Journal Entry
gates already use (PM workflow's before_submit approval check, PM backdate control) so period-close
JEs, automated GL corrections, and migration-era posts are never blocked by a rule aimed at manual
entries. See reference_frappe_je_guards_and_exemptions.
"""

from __future__ import annotations

import frappe
from frappe import _
from frappe.utils import cint


def validate_attachment_required(doc, method=None):
	if cint(doc.get("is_system_generated")):
		return

	if frappe.get_all(
		"File",
		filters={"attached_to_doctype": "Journal Entry", "attached_to_name": doc.name},
		limit=1,
	):
		return

	frappe.throw(
		_("At least one attachment (supporting document) is required before submitting a Journal Entry."),
		title=_("Attachment Required"),
	)
