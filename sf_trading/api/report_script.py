# sf_trading/api/report_script.py
"""Client-side extensions for other apps' query reports, shipped with the report's own script.

The desk fetches a report's JS through `frappe.desk.query_report.get_script` and evals it when
the report opens. Appending an extension to that response means it always runs right after the
report defines `frappe.query_reports[name]` -- no load-order tricks in app_include_js, and no
stale desk bundle in somebody's browser can leave it out.
"""

import frappe
from frappe.desk.query_report import get_script as _get_script

# report name -> file under sf_trading/public/js
EXTENSIONS = {
	# clickable counts that open "Customer Acquisition and Loyalty Detail"
	"Customer Acquisition and Loyalty": "customer_acquisition_drill.js",
}


@frappe.whitelist()
def get_script(report_name):
	out = _get_script(report_name)
	extension = EXTENSIONS.get(report_name)
	if extension:
		with open(frappe.get_app_path("sf_trading", "public", "js", extension)) as f:
			out["script"] = f"{out['script']}\n\n{f.read()}\n//# sourceURL={extension}"
	return out
