# sf_trading/api/duplicate_check.py
"""Live "N similar records found" search for GS Issue 15 -- warn while typing, on every master.

Non-blocking: this is UX only, surfacing near-matches before the user finishes typing. It does not
replace or relax the real uniqueness rule (party_completeness.py's CR/VAT check, or the
Property Setter GS Issue 4 will eventually add once Bahrain's duplicate customers are cleaned).

Query stays narrow on purpose -- one field, exact-prefix match, capped at 5 rows -- so this never
approaches the sqlparse 10,000-token cap this bench's frappe 15.114 enforces on `in` filters (that
cap is about `in` filters specifically; a plain LIKE with a low LIMIT never builds one, but the
discipline of keeping every ad-hoc query small is the same lesson). The prefix value is passed as an
ordinary filter dict value -- frappe.get_list parameterises it, this never touches raw SQL.
"""

from __future__ import annotations

import frappe
from frappe import _

#: doctype -> the field a user is actually typing into, for each master this popup covers.
_SEARCH_FIELD = {
	"Item": "item_name",
	"Customer": "customer_name",
	"Supplier": "supplier_name",
	"Account": "account_name",
}


@frappe.whitelist()
def similar_records(doctype: str, value: str) -> list[dict]:
	"""Up to 5 existing rows whose search field starts with `value`. Empty list = nothing close."""
	if doctype not in _SEARCH_FIELD:
		frappe.throw(_("Duplicate check is not configured for %s.") % doctype)

	value = (value or "").strip()
	if len(value) < 3:
		return []

	if not frappe.has_permission(doctype, "read"):
		return []

	field = _SEARCH_FIELD[doctype]
	prefix = value + "%"
	return frappe.get_list(
		doctype,
		filters={field: ["like", prefix]},
		fields=["name", field + " as label"],
		limit_page_length=5,
		order_by=field + " asc",
	)
