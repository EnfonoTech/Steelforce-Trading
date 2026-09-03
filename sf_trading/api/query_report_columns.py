# sf_trading/api/query_report_columns.py
"""Adding a column to a big report, without the request dying on the way out.

"Add Column" on a query report sends every row's link value to
`frappe.desk.query_report.get_data_for_custom_field`, which reads them in ONE
`{"name": ("in", [...])}` query. Since frappe 15.114 that query is handed to `sqlparse` for
validation, and sqlparse gives up past 10,000 tokens -- about five thousand names. On production
a 5,055-invoice report answered every "Add Column" with

    Server Error
    sqlparse.exceptions.SQLParseError: Maximum number of tokens exceeded (10000).

while the report itself had already rendered, which is what made it look like a report bug.

Same function, same return value, read in batches. Two doors have to be covered:

  * the desk calls the endpoint over HTTP    -> `override_whitelisted_methods` in hooks.py
  * `get_data_for_custom_report` calls the module-level function directly, for a SAVED custom
    report and for an export       -> `install()`, wired to `before_request` and `before_job`

An override alone would fix the first and leave the second failing exactly as before.
"""

import frappe
from frappe import _

from sf_trading.query import IN_BATCH, fetch_in


@frappe.whitelist()
def get_data_for_custom_field(doctype: str, field: str, names=None) -> dict:
	"""Values of `field` for `names`, as {name: value} -- frappe's own contract, batched.

	The permission check, the JSON-string form of `names`, and the no-names case (read what the
	user may see, unfiltered) are all kept as core has them; only the reading is different.
	"""
	if not frappe.has_permission(doctype, "read"):
		frappe.throw(_("Not Permitted to read {0}").format(_(doctype)), frappe.PermissionError)

	if isinstance(names, str | bytearray):
		names = frappe.json.loads(names)

	if not names:
		return frappe._dict(frappe.get_list(doctype, filters={}, fields=["name", field], as_list=1))

	pairs = fetch_in(
		doctype,
		names,
		fields=["name", field],
		as_list=True,
		permissions=True,
		batch=IN_BATCH,
	)
	return frappe._dict(pairs)


def install(*args, **kwargs) -> None:
	"""Point frappe's own module attribute at the batched version.

	Needed because `get_data_for_custom_report` calls `get_data_for_custom_field` by module
	attribute, and a whitelist override only redirects calls that arrive over HTTP. Idempotent,
	and cheap enough to sit on `before_request`: after the first call it is one attribute read.
	"""
	from frappe.desk import query_report

	if getattr(query_report.get_data_for_custom_field, "__module__", "") == __name__:
		return

	query_report.get_data_for_custom_field = get_data_for_custom_field
