# User permissions are created for company, branch, warehouse and cost center.
# Company and first warehouse are set as is_default=1 so Frappe uses them as
# the user's Session Defaults instead of falling back to global defaults.
# The "Branch User" role is auto-assigned to users added here.

import frappe
from frappe.model.document import Document

BRANCH_USER_ROLE = "Branch User"


class BranchConfiguration(Document):

	def validate(self):
		# Ensure warehouse and cost center belong to the selected company
		if self.company:
			for w in self.warehouse:
				if w.warehouse:
					wh_company = frappe.db.get_value("Warehouse", w.warehouse, "company")
					if wh_company and wh_company != self.company:
						frappe.throw(
							f"Warehouse <b>{w.warehouse}</b> belongs to company <b>{wh_company}</b>, "
							f"not <b>{self.company}</b>. Please select a warehouse from the correct company."
						)

			for c in self.cost_center:
				if c.cost_center:
					cc_company = frappe.db.get_value("Cost Center", c.cost_center, "company")
					if cc_company and cc_company != self.company:
						frappe.throw(
							f"Cost Center <b>{c.cost_center}</b> belongs to company <b>{cc_company}</b>, "
							f"not <b>{self.company}</b>. Please select a cost center from the correct company."
						)

	def before_save(self):
		if self.is_new():
			return

		old_doc = self.get_doc_before_save()

		old_users = {d.user for d in old_doc.user}
		new_users = {d.user for d in self.user}

		removed_users = old_users - new_users

		for user in removed_users:
			# Delete company permission
			if old_doc.get("company"):
				delete_permission(user, "Company", old_doc.company)

			delete_permission(user, "Branch", old_doc.branch)

			for w in old_doc.warehouse:
				delete_permission(user, "Warehouse", w.warehouse)

			for c in old_doc.cost_center:
				delete_permission(user, "Cost Center", c.cost_center)

			# Remove role if user is not in any other Branch Configuration
			# Find what role they had in this branch
			old_role = None
			for old_u in old_doc.user:
				if old_u.user == user:
					old_role = old_u.get("role") or BRANCH_USER_ROLE
					break
			_maybe_remove_role(user, old_role or BRANCH_USER_ROLE, exclude_branch=self.name)

		# Handle company change — remove old company permission for remaining users
		old_company = old_doc.get("company")
		new_company = self.get("company")
		if old_company and old_company != new_company:
			for u in self.user:
				delete_permission(u.user, "Company", old_company)

	def on_update(self):
		self.create_permissions()

	def create_permissions(self):
		for u in self.user:
			# Create company permission (marked as default so Session Defaults picks it)
			if self.company:
				create_permission(u.user, "Company", self.company, is_default=1)

			create_permission(u.user, "Branch", self.branch)

			for idx, w in enumerate(self.warehouse):
				# First warehouse is the default
				create_permission(u.user, "Warehouse", w.warehouse, is_default=1 if idx == 0 else 0)

			for idx, c in enumerate(self.cost_center):
				# First cost center is the default
				create_permission(u.user, "Cost Center", c.cost_center, is_default=1 if idx == 0 else 0)

			# Also grant access to the company's default cost center (used in tax templates)
			# without marking it as default — the branch cost center stays as the user's default
			if self.company:
				company_default_cc = frappe.db.get_value("Company", self.company, "cost_center")
				if company_default_cc:
					create_permission(u.user, "Cost Center", company_default_cc, is_default=0)

			# Auto-assign the selected role (Branch User, Warehouse User, or Stock User)
			selected_role = u.get("role") or BRANCH_USER_ROLE
			_assign_role(u.user, selected_role)

			# Auto-set Module Profile to restrict sidebar modules
			if selected_role == BRANCH_USER_ROLE:
				_set_module_profile(u.user, "Branch User")
			elif selected_role == "Damage User":
				_set_module_profile(u.user, "Damage User")


def create_permission(user, allow, value, is_default=0):
	if not value:
		return

	existing = frappe.db.exists("User Permission", {
		"user": user,
		"allow": allow,
		"for_value": value
	})

	if existing:
		# Update is_default if needed — but only if no other default exists
		if is_default and not _has_existing_default(user, allow, exclude_value=value):
			frappe.db.set_value("User Permission", existing, "is_default", 1)
	else:
		# If we want to set default but one already exists, don't override it
		if is_default and _has_existing_default(user, allow):
			is_default = 0

		doc = frappe.new_doc("User Permission")
		doc.user = user
		doc.allow = allow
		doc.for_value = value
		doc.is_default = is_default
		doc.apply_to_all_doctypes = 1
		doc.insert(ignore_permissions=True)


def _has_existing_default(user, allow, exclude_value=None):
	"""Check if user already has a default User Permission for this allow type."""
	filters = {
		"user": user,
		"allow": allow,
		"is_default": 1,
	}
	if exclude_value:
		filters["for_value"] = ["!=", exclude_value]

	return frappe.db.exists("User Permission", filters)


def delete_permission(user, allow, value):
	if not value:
		return

	perms = frappe.get_all(
		"User Permission",
		filters={
			"user": user,
			"allow": allow,
			"for_value": value
		},
		pluck="name"
	)

	for p in perms:
		frappe.delete_doc("User Permission", p, ignore_permissions=True)


def _ensure_system_user(user):
	"""Website Users cannot hold desk roles. Upgrade to System User so role takes effect."""
	current_type = frappe.db.get_value("User", user, "user_type")
	if current_type and current_type != "System User":
		frappe.db.set_value("User", user, "user_type", "System User")


def _assign_role(user, role):
	"""Assign the specified role if not already assigned. Uses direct DB for reliability."""
	if not role or not frappe.db.exists("Role", role):
		return

	# Desk roles (Branch User / Stock User / Damage User / Stock Manager) require
	# System User user_type; Website Users silently lose role assignments.
	_ensure_system_user(user)

	# Check if role already assigned
	if frappe.db.exists("Has Role", {"parent": user, "role": role}):
		return

	# Direct insert — more reliable than user_doc.add_roles() which can fail
	# during Branch Configuration save context
	frappe.get_doc({
		"doctype": "Has Role",
		"parent": user,
		"parenttype": "User",
		"parentfield": "roles",
		"role": role,
	}).insert(ignore_permissions=True)


def _set_module_profile(user, profile_name):
	"""Set the Module Profile on a user if not already set."""
	if not frappe.db.exists("Module Profile", profile_name):
		return
	current = frappe.db.get_value("User", user, "module_profile")
	if current != profile_name:
		frappe.db.set_value("User", user, "module_profile", profile_name)


def _maybe_remove_role(user, role, exclude_branch=None):
	"""Remove role if user is not in any other Branch Configuration with the same role."""
	if not role:
		return

	other_configs = frappe.get_all(
		"Branch Configuration User",
		filters={"user": user},
		fields=["parent", "role"],
	)

	# Check if user has this same role in any other Branch Configuration
	has_role_elsewhere = any(
		c.parent != exclude_branch and (c.get("role") or BRANCH_USER_ROLE) == role
		for c in other_configs
	)

	if not has_role_elsewhere:
		user_doc = frappe.get_doc("User", user)
		if role in [r.role for r in user_doc.roles]:
			user_doc.remove_roles(role)
