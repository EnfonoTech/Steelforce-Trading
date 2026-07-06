import os

import frappe


@frappe.whitelist()
def get_user_guide_markdown():
	"""Return the contents of docs/USER_GUIDE.md so the desk page and the
	repo doc always show the same content."""
	# frappe.get_app_source_path() lowercases any extra path segments it's
	# given (it's built for scrubbed module names), so join the filename
	# ourselves to keep the "USER_GUIDE.md" casing intact.
	guide_path = os.path.join(frappe.get_app_source_path("sf_trading"), "docs", "USER_GUIDE.md")
	if not os.path.isfile(guide_path):
		frappe.throw(frappe._("User guide file not found on the server."))

	with open(guide_path, encoding="utf-8") as f:
		return f.read()
