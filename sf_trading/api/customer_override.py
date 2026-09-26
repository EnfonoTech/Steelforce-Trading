"""
Customer overrides: require attachment when VAT Registration Number is set.
"""

from __future__ import annotations

import frappe
from frappe import _
from frappe.utils import cstr, flt

#: Fields an account-status save is allowed to touch without tripping the VAT-document
#: requirement below -- see _only_status_fields_changed.
_STATUS_FIELDS = ("is_frozen", "disabled")

#: Fieldtypes compared numerically (flt) rather than as strings -- see _fields_equal.
_NUMERIC_FIELDTYPES = ("Int", "Float", "Currency", "Percent", "Check")


def _fields_equal(fieldtype: str, a, b) -> bool:
	"""Same value, tolerant of two representation mismatches that are NOT real changes:

	- None vs "" -- an unset Link/Data field reads back as ``None`` from get_doc_before_save()'s
	  fresh DB fetch but as ``""`` on an in-memory doc built from a plain dict (e.g.
	  ``represents_company``, on every single customer, verified live).
	- int vs float -- a form submits a whole-number Float/Currency/Percent field as a bare JSON
	  number, which Python parses as ``int`` 0, while the DB's own column reads back a ``float``
	  0.0 -- ``cstr(0) == "0" != "0.0" == cstr(0.0)`` even though the VALUE never changed
	  (``default_commission_rate``, live on prod, confirmed the exemption below never actually
	  fired because of exactly this).

	cstr() alone fixes the first; flt() is needed for the second, since cstr(0) and cstr(0.0)
	are different strings despite being the same number.
	"""
	if fieldtype in _NUMERIC_FIELDTYPES:
		return flt(a) == flt(b)
	return cstr(a) == cstr(b)


def _only_status_fields_changed(doc) -> bool:
	"""True if every OTHER field on the doc (child tables excluded) is identical to the version
	before this save -- i.e. this save touches nothing but is_frozen/disabled. A doc with no
	before-save snapshot (shouldn't happen once in_insert is already excluded) is treated as "not
	status-only", the safer default."""
	before = doc.get_doc_before_save()
	if not before:
		return False

	for df in doc.meta.fields:
		if df.fieldname in _STATUS_FIELDS or df.fieldtype in ("Table", "Table MultiSelect"):
			continue
		if not _fields_equal(df.fieldtype, doc.get(df.fieldname), before.get(df.fieldname)):
			return False
	return True


def validate(doc, _method=None):
	"""Require at least one attachment when the customer has a VAT Registration Number.

	Skipped when the save changes ONLY is_frozen/disabled -- an admin toggling the customer's
	account status is doing something unrelated to VAT compliance, and must not be blocked by a
	document that was never attached (freezing a customer is exactly the action someone takes
	BECAUSE the VAT document is still missing). Any OTHER field changed in the same save is still
	blocked as before -- this is not a blanket exemption for the whole document.
	"""
	vat_number = frappe.utils.cstr(doc.get("custom_vat_registration_number") or "").strip()
	if not vat_number or vat_number == "0":
		return

	# Skip on first save — attachment section only appears after the doc exists
	if doc.flags.get("in_insert"):
		return

	if _only_status_fields_changed(doc):
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
