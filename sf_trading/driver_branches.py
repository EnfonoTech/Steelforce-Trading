# sf_trading/driver_branches.py
"""Which branches a Delivery Person serves -- a table on the Driver, not one Branch field.

A delivery person used to name a single branch (`custom_branch`), while on production three of
them already deliver for both branches. The Driver's existing Branch Cash Limits table now carries
that list, relabelled **Branches**: a row means "delivers for this branch", and its Cash Limit stays
what it always was -- an optional cap for that branch, 0 for none. One table, so the list of
branches and the per-branch caps can never disagree.

Everything that read the single field reads the table instead:

* the Delivery Person pickers on Sales Invoice and Sales Order filter on it (a child-table filter
  that frappe's own link search honours -- see public/js/sales_invoice.js);
* ``validate_driver_serves_branch`` applies the same rule server-side, because the form is not the
  only way a document is saved (API, the order-to-invoice mapper, a branch changed after the
  person was picked);
* who may see a delivery person. A Branch user permission used to gate Driver through
  `custom_branch`, and still would -- a hidden field keeps its value -- so a delivery person
  shared by two branches would stay invisible to the second branch's users. That field now
  ignores user permissions, and ``permission_query_conditions`` / ``has_permission`` below apply
  the same rule to the table: a branch user sees the people who serve one of their branches, plus
  anyone with no branch at all, which is exactly what the single field gave them.

`custom_branch` itself stays, hidden and read-only, so its old value remains on record. The patch
`v0_1.driver_branches_table` copied it into the table.
"""

from __future__ import annotations

import frappe
from frappe import _
from frappe.utils import cint

BRANCHES_FIELD = "custom_branch_cash_limits"
BRANCH_ROW = "Driver Branch Cash Limit"


def driver_branches(driver: str | None) -> list[str]:
	"""The branches this delivery person serves, in table order. Reads past permissions on
	purpose: the callers are rules that must see the whole table, not one user's view of it."""
	if not driver:
		return []
	return frappe.get_all(
		BRANCH_ROW,
		filters={"parenttype": "Driver", "parentfield": BRANCHES_FIELD, "parent": driver},
		pluck="branch",
		order_by="idx asc",
	)


def permitted_branches(user: str | None = None) -> list[str] | None:
	"""The branches a Branch user permission limits this user to on Driver, or None for no limit.

	Read the way frappe reads it: a permission counts for Driver unless its Applicable For names
	some other doctype.
	"""
	user = user or frappe.session.user
	if user == "Administrator":
		return None
	from frappe.core.doctype.user_permission.user_permission import get_user_permissions

	perms = (get_user_permissions(user) or {}).get("Branch") or []
	values = sorted(
		{p.get("doc") for p in perms if p.get("doc") and (p.get("applicable_for") or "Driver") == "Driver"}
	)
	return values or None


def _served(doc) -> set[str]:
	return {row.get("branch") for row in (doc.get(BRANCHES_FIELD) or []) if row.get("branch")}


def permission_query_conditions(user=None, doctype=None):
	"""permission_query_conditions for Driver: a branch user sees the delivery people who serve one
	of their branches, and anyone not tied to a branch yet."""
	branches = permitted_branches(user)
	if not branches:
		return ""
	# frappe takes this hook's answer as SQL text, so it cannot be a bound parameter. The only
	# values in it are the user's own permitted Branch names, each quoted by the database driver's
	# own escape -- the same construction sf_trading.ticket and frappe's ToDo/Event hooks use.
	values = ", ".join(frappe.db.escape(branch) for branch in branches)
	return f"""(not exists (select 1 from `tab{BRANCH_ROW}` dbr
			where dbr.parenttype = 'Driver' and dbr.parent = `tabDriver`.`name`)
		or exists (select 1 from `tab{BRANCH_ROW}` dbr
			where dbr.parenttype = 'Driver' and dbr.parent = `tabDriver`.`name`
			and dbr.branch in ({values})))"""


def has_permission(doc, ptype=None, user=None, debug=False):
	"""has_permission for Driver -- the form's twin of the list rule above. A controller hook can
	only refuse, so None means "no opinion" and leaves frappe's own role check standing."""
	branches = permitted_branches(user)
	if not branches:
		return None
	served = _served(doc)
	if not served:
		return None
	return bool(served & set(branches))


def validate_branches(doc, _method=None):
	"""Driver validate: one row per branch, and a branch user adds only branches they hold.

	The second rule is what a Branch user permission did to the single field, whose picker only
	offered the user's own branches. The table's Branch column ignores user permissions (it has to:
	frappe also checks child rows when deciding whether a user may open a document, so a shared
	delivery person would otherwise be unreadable), so the rule is applied here instead -- to rows
	the user is adding, never to rows someone else already put there.

	A new delivery person with an empty table starts in their Employee's branch -- what the old
	field did for them through its `fetch_from: employee.branch`.
	"""
	if doc.is_new() and not doc.get(BRANCHES_FIELD):
		home = doc.get("custom_branch") or (
			doc.get("employee") and frappe.db.get_value("Employee", doc.employee, "branch")
		)
		if home:
			doc.append(BRANCHES_FIELD, {"branch": home, "cash_limit": 0})

	seen = set()
	for row in doc.get(BRANCHES_FIELD) or []:
		branch = row.get("branch")
		if not branch:
			continue
		if branch in seen:
			frappe.throw(
				_("Branch {0} is listed twice in Branches. Keep one row per branch.").format(frappe.bold(branch)),
				title=_("Duplicate Branch"),
			)
		seen.add(branch)

	allowed = permitted_branches()
	if not allowed:
		return
	before = doc.get_doc_before_save() if hasattr(doc, "get_doc_before_save") else None
	already = _served(before) if before else set()
	refused = sorted(branch for branch in seen - already if branch not in allowed)
	if refused:
		frappe.throw(
			_("You can add only your own branches ({0}) to a delivery person. Ask an administrator to add {1}.").format(
				", ".join(allowed), ", ".join(refused)
			),
			title=_("Branch Not Permitted"),
		)


def validate_driver_serves_branch(doc, _method=None):
	"""Sales Invoice / Sales Order validate: the delivery person must serve this document's branch.

	The pickers already offer only those people; this is the same rule for every other way in. A
	return follows its original invoice, and a delivery person with no branches at all is not
	checked -- nothing says where they belong.
	"""
	if cint(doc.get("is_return")):
		return
	driver, branch = doc.get("custom_driver"), doc.get("branch")
	if not (driver and branch):
		return
	served = driver_branches(driver)
	if not served or branch in served:
		return
	name = frappe.db.get_value("Driver", driver, "full_name") or driver
	frappe.throw(
		_(
			"Delivery Person {0} does not deliver for branch {1}; they serve {2}. "
			"Pick another delivery person, or add {1} to their Branches."
		).format(frappe.bold(name), frappe.bold(branch), ", ".join(served)),
		title=_("Delivery Person Not in This Branch"),
	)


@frappe.whitelist()
def get_driver_branches(driver: str) -> list[str]:
	"""The branches a delivery person serves, for the Sales Order form's branch-change check."""
	if not driver:
		return []
	if not frappe.has_permission("Driver", "read", doc=driver):
		frappe.throw(_("Not permitted to read Delivery Person {0}.").format(driver), frappe.PermissionError)
	return driver_branches(driver)
