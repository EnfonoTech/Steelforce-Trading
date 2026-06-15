import frappe
from frappe.utils import flt


def permission_query_conditions_for_customer(user):
	"""
	Additional SQL WHERE condition for Customer list/link queries.

	- System Manager: unrestricted
	- User WITH Branch in User Permissions: sees non-credit customers freely,
	  but credit customers only if their branch is in Customer Branch Access
	- User WITHOUT Branch in User Permissions: no additional condition
	  (company filter from standard User Permissions on custom_company handles it)
	"""
	if not user:
		user = frappe.session.user

	if "System Manager" in frappe.get_roles(user):
		return ""

	branches = frappe.get_all(
		"User Permission",
		filters={"user": user, "allow": "Branch"},
		pluck="for_value",
	)

	if not branches:
		return ""

	branch_list = ", ".join([frappe.db.escape(b) for b in branches])

	return """(
		`tabCustomer`.name NOT IN (
			SELECT parent FROM `tabCustomer Credit Limit` WHERE credit_limit > 0
		)
		OR `tabCustomer`.name IN (
			SELECT parent FROM `tabCustomer Branch Access` WHERE branch IN ({0})
		)
	)""".format(branch_list)


def auto_add_branch_on_credit_limit(doc, method):
	"""
	Customer before_save: auto-add the current user's branches to
	custom_branch_access ONLY when a credit limit row is newly set (new row
	or value changed from 0 → >0). Manual edits to custom_branch_access on
	subsequent saves are preserved.
	"""
	if doc.is_new():
		newly_set = any(flt(row.get("credit_limit")) > 0 for row in (doc.credit_limits or []))
	else:
		old_rows = frappe.get_all(
			"Customer Credit Limit",
			filters={"parent": doc.name},
			fields=["name", "credit_limit"],
		)
		old_credit_map = {r.name: flt(r.credit_limit) for r in old_rows}

		newly_set = False
		for row in (doc.credit_limits or []):
			if flt(row.get("credit_limit")) > 0:
				# new child row (no name yet) or previously was 0
				if not row.get("name") or old_credit_map.get(row.get("name"), 0) <= 0:
					newly_set = True
					break

	if not newly_set:
		return

	user = frappe.session.user
	branches = frappe.get_all(
		"User Permission",
		filters={"user": user, "allow": "Branch"},
		pluck="for_value",
	)

	if not branches:
		return

	existing = {row.branch for row in (doc.custom_branch_access or [])}
	for branch in branches:
		if branch not in existing:
			doc.append("custom_branch_access", {"branch": branch})
			existing.add(branch)
