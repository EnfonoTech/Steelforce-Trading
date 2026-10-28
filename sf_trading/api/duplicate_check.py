# sf_trading/api/duplicate_check.py
"""Live "N similar records found" search for GS Issue 15 -- warn while typing, on every master.

Non-blocking: this is UX only, surfacing near-matches before the user finishes typing. It does not
replace or relax the real uniqueness rule (party_completeness.py's CR/VAT check, or the
Property Setter GS Issue 4 will eventually add once Bahrain's duplicate customers are cleaned).

Shown only while SF Trading Settings > "Show Similar Names While Creating Masters" is ticked: off,
this answers an empty list and the form shows nothing.

A record matches when its name holds every word typed, in any order ("steel workshop" finds "Gulf Star
Steel Workshop"), the way the item search reads words -- a prefix match missed every name whose first
word differed. Each match also carries its group and disabled flag, for the list under the field.

Query stays narrow on purpose -- one field, a few LIKE words, capped at 8 rows -- so this never
approaches the sqlparse 10,000-token cap this bench's frappe 15.114 enforces on `in` filters (that
cap is about `in` filters specifically; a plain LIKE with a low LIMIT never builds one, but the
discipline of keeping every ad-hoc query small is the same lesson). The prefix value is passed as an
ordinary filter dict value -- frappe.get_list parameterises it, this never touches raw SQL.
"""

from __future__ import annotations

import frappe
from frappe import _

#: doctype -> (the field a user is actually typing into, a field shown beside each match to tell
#: look-alikes apart, the active flag: "disabled" / "enabled" / "status" (Active or not) / None).
#: Keep in step with FIELD_BY_DOCTYPE / NAME_PARTS_BY_DOCTYPE in public/js/duplicate_check_popup.js.
_MASTERS = {
	"Item": ("item_name", "item_group", "disabled"),
	"Customer": ("customer_name", "customer_group", "disabled"),
	"Supplier": ("supplier_name", "supplier_group", "disabled"),
	"Account": ("account_name", "root_type", "disabled"),
	"Item Group": ("item_group_name", "parent_item_group", None),
	"Customer Group": ("customer_group_name", "parent_customer_group", None),
	"Supplier Group": ("supplier_group_name", "parent_supplier_group", None),
	"Warehouse": ("warehouse_name", "parent_warehouse", "disabled"),
	"Cost Center": ("cost_center_name", "parent_cost_center", "disabled"),
	"Territory": ("territory_name", "parent_territory", None),
	"Sales Person": ("sales_person_name", "parent_sales_person", "enabled"),
	"Brand": ("brand", None, None),
	"UOM": ("uom_name", None, "enabled"),
	# typed as first / middle / last name; the form joins them into employee_name, as save does
	"Employee": ("employee_name", "department", "status"),
}

SETTINGS = "SF Trading Settings"
SWITCH = "suggest_similar_masters"
LIMIT = 8


def enabled() -> bool:
	"""The SF Trading Settings switch; off (and harmless) while migrate has not added it yet."""
	try:
		return bool(frappe.db.get_single_value(SETTINGS, SWITCH))
	except Exception:
		return False


@frappe.whitelist()
def similar_records(doctype: str, value: str) -> list[dict]:
	"""Up to 8 existing rows whose search field holds every word of `value`. Empty list = nothing close,
	or the SF Trading Settings switch is off."""
	if doctype not in _MASTERS:
		frappe.throw(_("Duplicate check is not configured for %s.") % doctype)

	value = (value or "").strip()
	if len(value) < 3 or not enabled():
		return []

	if not frappe.has_permission(doctype, "read"):
		return []

	field, group, flag = _MASTERS[doctype]
	words = [w for w in value.replace("%", " ").replace("_", " ").split() if len(w) >= 2]
	if not words:
		return []
	fields = ["name", field + " as label"]
	if group:
		fields.append(group + " as `group`")
	if flag:
		fields.append(flag)
	rows = frappe.get_list(
		doctype,
		filters=[[field, "like", "%" + w + "%"] for w in words],
		fields=fields,
		limit_page_length=LIMIT,
		order_by=field + " asc",
	)
	for row in rows:
		# one shape for the popup whichever way round the master keeps its flag
		if flag == "enabled":
			row["disabled"] = not row.pop("enabled", 1)
		elif flag == "status":
			row["disabled"] = row.pop("status", "Active") != "Active"
		else:
			row["disabled"] = bool(row.get("disabled"))
	return rows
