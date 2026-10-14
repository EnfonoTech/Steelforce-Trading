# sf_trading/switches.py
"""The on/off switches of SF Trading Settings, read the way a hook needs them.

A hook that runs on every Customer / Supplier / Account save cannot use `frappe.db.get_single_value` for a
switch that is meant to be ON until somebody unticks it:

* it casts a Check that was never stored to 0, so "never set" reads OFF, exactly the opposite of the default
  (the DocField default only ever reaches a Single that is created after the field, never one saved before);
* it raises "Field ... does not exist" for a field the DocType does not have yet, and the code is pulled before
  `bench migrate` creates the field (and on sites of a shared bench that are never migrated).

So the stored row is read directly: no row means nobody has set it, and a switch nobody set is `default`.
A value somebody ticked (1) or unticked (0) is what it says. A read that fails counts as the default, because a
house-standard switch must never be what stops a Customer from being saved.
"""

import frappe
from frappe.utils import cint

SETTINGS = "SF Trading Settings"


def is_on(fieldname: str, default: bool = True) -> bool:
	"""The Check `fieldname` of SF Trading Settings, `default` when it is not stored (or cannot be read)."""
	try:
		rows = frappe.db.sql(
			"SELECT `value` FROM `tabSingles` WHERE `doctype` = %s AND `field` = %s", (SETTINGS, fieldname)
		)
	except Exception:
		return default
	if not rows or rows[0][0] in (None, ""):
		return default
	return bool(cint(rows[0][0]))
