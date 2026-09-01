# sf_trading/system_defaults.py
"""Every System Settings Select/Data field needs a matching global default row.

On production, saving System Settings failed with "Time Zone is required" while
`tabSingles` held `time_zone = Asia/Bahrain` and every user had their own
`tabDefaultValue` row for it. What was missing was the ONE row that matters:
`__default`.

Frappe's own form does this on load (frappe/core/doctype/system_settings/system_settings.js):

    frappe.call({method: "...system_settings.load", callback: function (data) {
        $.each(data.message.defaults, function (key, val) {
            frm.set_value(key, val, null, true);

and `load()` builds `defaults` for EVERY Select/Data field straight out of
`frappe.db.get_defaults()`. A field with no `__default` row therefore arrives as None, the
form writes that None over the stored value, the document goes dirty on its own, and a
mandatory field cannot be saved at all. The setting is not lost -- it is being blanked by
the form each time it opens.

Saving System Settings would repair it (`SystemSettings.on_update` writes every default),
which is the circular part: the save is what the missing default prevents.

The rows normally come from the setup wizard. A site that arrived by restore or migration
instead can be missing one, so this fills what is missing and never touches what is there:
a default that disagrees with the stored value is somebody's decision, and on this site
`app_name` is exactly that.
"""

import frappe

DOCTYPE = "System Settings"
SYNCED_FIELDTYPES = ("Select", "Data")


def missing_defaults() -> dict:
	"""Fields whose value is stored on the Single but has no `__default` row behind it."""
	settings = frappe.get_single(DOCTYPE)
	defaults = frappe.db.get_defaults()

	missing = {}
	for df in frappe.get_meta(DOCTYPE).get("fields"):
		if df.fieldtype not in SYNCED_FIELDTYPES:
			continue
		value = settings.get(df.fieldname)
		if value in (None, ""):
			continue
		if defaults.get(df.fieldname) not in (None, ""):
			continue
		missing[df.fieldname] = value
	return missing


def sync_system_settings_defaults():
	"""after_migrate: give every stored System Settings value its global default row."""
	filled = missing_defaults()
	for fieldname, value in filled.items():
		frappe.db.set_default(fieldname, value)

	if filled:
		frappe.logger().info(
			"sf_trading: filled missing System Settings defaults -> %s" % ", ".join(sorted(filled))
		)
	return filled
