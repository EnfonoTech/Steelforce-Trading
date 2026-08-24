# apps/sf_trading/sf_trading/patches/v0_1/adopt_sales_return_settings.py
"""Carry the sales return settings over from PM Settings to SF Trading Settings.

The window and the approval threshold spent a few hours living on permission_manager's PM
Settings, which every client running that app shares. They belong to this client, so they moved
here -- and whatever was configured in the meantime has to come with them, or a site wakes up
with the feature silently off.

Reads the old values straight out of `tabSingles` rather than through the doctype: by the time
this runs, permission_manager has already dropped the fields, so the meta no longer knows them
while the rows are still there. Also brings the override rows across.

Anything the old settings never held falls back to what the new doctype ships as, so a site that
never touched them ends up exactly as a fresh install would.
"""

import frappe
from frappe.utils import cint, flt

FIELDS = (
	"restrict_sales_return",
	"sales_return_days",
	"sales_return_days_from",
	"si_return_approval_enabled",
	"si_return_amount_restriction",
	"si_return_approval_threshold",
	"si_return_requires_workflow",
)

DEFAULTS = {
	"sales_return_days_from": "Original Invoice Date",
	"si_return_approval_threshold": 500,
	"si_return_requires_workflow": 1,
}


def execute():
	if not frappe.db.exists("DocType", "SF Trading Settings"):
		return

	settings = frappe.get_single("SF Trading Settings")
	old = _old_values()

	for field in FIELDS:
		if _already_set(field):
			continue
		if field in old:
			settings.set(field, old[field])
		elif field in DEFAULTS:
			settings.set(field, DEFAULTS[field])

	_adopt_overrides(settings)
	settings.flags.ignore_permissions = True
	settings.save()

	frappe.clear_cache(doctype="SF Trading Settings")
	frappe.db.commit()
	frappe.logger().info(
		"sf_trading: sales return settings adopted from PM Settings %s" % (old or "(nothing to carry)")
	)


def _old_values() -> dict:
	"""What PM Settings still holds for these fields, read out of tabSingles."""
	if not frappe.db.exists("DocType", "PM Settings"):
		return {}

	rows = frappe.db.get_all(
		"Singles",
		filters={"doctype": "PM Settings", "field": ["in", list(FIELDS)]},
		fields=["field", "value"],
	)

	values = {}
	for row in rows:
		if row.value in (None, ""):
			continue
		if row.field in ("sales_return_days_from",):
			values[row.field] = row.value
		elif row.field == "si_return_approval_threshold":
			values[row.field] = flt(row.value)
		else:
			values[row.field] = cint(row.value)
	return values


def _already_set(field: str) -> bool:
	"""Leave alone anything already chosen on the new doctype."""
	return bool(
		frappe.db.exists("Singles", {"doctype": "SF Trading Settings", "field": field})
	)


def _adopt_overrides(settings):
	"""Copy the role / user override rows across, if the old table still has any."""
	if settings.get("sales_return_overrides"):
		return
	if not frappe.db.table_exists("PM Advance Block Override"):
		return

	rows = frappe.db.get_all(
		"PM Advance Block Override",
		filters={"parenttype": "PM Settings", "parentfield": "sales_return_overrides"},
		fields=["override_type", "override"],
		order_by="idx asc",
	)
	for row in rows:
		if row.override:
			settings.append(
				"sales_return_overrides",
				{"override_type": row.override_type, "override": row.override},
			)
