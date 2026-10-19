# sf_trading/report_numbering.py
"""Every Report carries a number, so the team can say "RPT-0042" and everyone opens the same report.

A Report is known by its name alone, and names are long, translated, renamed and repeated across the
apps on a site (Steel Force carries more than three hundred). `custom_report_no` gives each one a short
number that is shown as a column in the Report list, can be filtered on, found in the search bar
(`boot_numbers` puts it in the boot; public/js/report_number_search.js matches what is typed), and never changes.

* **Who is numbered.** Every Report on the site, standard or custom, enabled or not. A new one is numbered
  on `Report.before_insert` with the next number; `after_migrate` numbers any that have none yet (the
  first migrate after this lands, and any report a sync inserted before the column existed), oldest first.
* **Numbers are never reused or moved.** The number is kept in a register of its own (`SF Report Number`,
  named after the report), not only on the report: a standard report whose JSON changed is deleted and
  inserted again by `bench migrate`, and a number held only on the report would be lost with it and a new
  one handed out. A report that comes back under its old name gets its old number. A rename carries the
  number to the new name; a deleted report's number is not given to anyone else.
* **Numbers are per site.** The same report can carry a different number on two sites, since each site
  numbers what it has in the order it got it.

A failure while numbering is logged and the report is made without a number: no report is ever held up by this.
"""

import frappe
from frappe import _
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields
from frappe.utils import cstr

REPORT = "Report"
REGISTER = "SF Report Number"
FIELD = "custom_report_no"
PREFIX = "RPT-"
SERIES = "SF-REPORT-NO"
DIGITS = 4


def ensure_custom_fields():
	"""after_migrate: the number's column on Report, then a number for every report that has none."""
	create_custom_fields(
		{
			REPORT: [
				{
					"fieldname": FIELD,
					"label": "Report No",
					"fieldtype": "Data",
					"insert_after": "report_name",
					"unique": 1,
					"read_only": 1,
					"no_copy": 1,
					"bold": 1,
					"in_list_view": 1,
					"in_standard_filter": 1,
					"description": "Quote this number to point a colleague to the report.",
				}
			]
		},
		ignore_validate=True,
		update=True,
	)
	number_existing_reports()


def _ready() -> bool:
	"""The column and the register both exist -- not so during the first migrate after this was added."""
	return bool(frappe.db.table_exists(REGISTER) and frappe.db.has_column(REPORT, FIELD))


def _next_number() -> str:
	"""The next free number. A number already in the register is skipped, so a series that lags behind
	(restored from a backup, say) can never hand out one twice."""
	from frappe.model.naming import getseries

	while True:
		number = PREFIX + getseries(SERIES, DIGITS)
		if not frappe.db.exists(REGISTER, {"report_no": number}):
			return number


def number_for(report_name: str) -> str:
	"""The number of a report, from the register; handed out and registered on first sight."""
	have = frappe.db.get_value(REGISTER, report_name, "report_no")
	if have:
		return have
	number = _next_number()
	frappe.get_doc({"doctype": REGISTER, "report": report_name, "report_no": number}).insert(ignore_permissions=True)
	return number


def number_new_report(doc, method=None):
	"""Report.before_insert: a report made with no number gets its own, or its old one back."""
	if doc.doctype != REPORT or cstr(doc.get(FIELD)).strip() or not _ready():
		return
	# before_insert runs ahead of naming; a Report is named after its report_name
	name = cstr(doc.get("report_name") or doc.get("name")).strip()
	if not name:
		return
	try:
		doc.set(FIELD, number_for(name))
	except Exception:
		frappe.log_error(title=_("Report number could not be given"), message=frappe.get_traceback())


def follow_rename(doc, method=None, old=None, new=None, merge=False):
	"""Report.after_rename: the number goes with the report. A merge leaves the surviving report its own."""
	if doc.doctype != REPORT or not old or old == new or not frappe.db.table_exists(REGISTER):
		return
	try:
		if not frappe.db.exists(REGISTER, old):
			return
		if merge or frappe.db.exists(REGISTER, new):
			frappe.delete_doc(REGISTER, old, ignore_permissions=True, force=1)
		else:
			# the model-level function: `frappe.rename_doc` has no ignore_permissions, and the register is read-only to users
			from frappe.model.rename_doc import rename_doc

			rename_doc(REGISTER, old, new, force=True, ignore_permissions=True, show_alert=False)
	except Exception:
		frappe.log_error(title=_("Report number could not follow a rename"), message=frappe.get_traceback())


def boot_numbers(bootinfo):
	"""boot_session: each report the user may open carries its number, so the search bar finds it by "RPT-0042"
	(public/js/report_number_search.js). The boot is cached per user, so a report numbered later shows up
	with the next clear-cache, as a new report's name does."""
	reports = (bootinfo.get("user") or {}).get("all_reports") or {}
	if not reports or not _ready():
		return
	numbers = dict(frappe.get_all(REPORT, filters={FIELD: ["is", "set"]}, fields=["name", FIELD], as_list=True))
	for name, info in reports.items():
		if name in numbers and isinstance(info, dict):
			info["report_no"] = numbers[name]


def number_existing_reports() -> int:
	"""Give every report with no number its own, oldest first. Safe to run again; returns how many it numbered."""
	if not _ready():
		return 0
	todo = frappe.get_all(REPORT, filters={FIELD: ["is", "not set"]}, fields=["name"], order_by="creation asc, name asc")
	for row in todo:
		frappe.db.set_value(REPORT, row.name, FIELD, number_for(row.name), update_modified=False)
	return len(todo)
