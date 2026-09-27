"""
Customer overrides: require attachment when VAT Registration Number is set.
"""

from __future__ import annotations

import frappe
from frappe import _
from frappe.utils import cstr

from sf_trading.party_completeness import only_status_fields_changed


def validate(doc, _method=None):
	"""Require at least one attachment when the customer has a VAT Registration Number.

	Skipped outright whenever the customer is being frozen or disabled (2026-09-27, client call:
	a blanket exemption, not just an "only status changed" one -- a frozen/disabled customer's
	master must be saveable regardless of what else is being edited in the same save) -- freezing
	a customer is exactly the action someone takes BECAUSE the VAT document is still missing, so
	it must never be the thing that blocks the freeze. See party_completeness.
	only_status_fields_changed -- shared with this app's other Customer gates.
	"""
	vat_number = cstr(doc.get("custom_vat_registration_number") or "").strip()
	if not vat_number or vat_number == "0":
		return

	# Skip on first save — attachment section only appears after the doc exists
	if doc.flags.get("in_insert"):
		return

	if only_status_fields_changed(doc):
		return

	attachments = frappe.get_all(
		"File",
		filters={"attached_to_doctype": "Customer", "attached_to_name": doc.name},
		limit=1,
	)
	if not attachments:
		frappe.throw(
			_(
				"Customer {0} has a VAT Registration Number ({1}). "
				"Please attach the required VAT document before saving."
			).format(doc.customer_name or doc.name, vat_number)
		)
