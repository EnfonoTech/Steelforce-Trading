import frappe
from frappe.model.document import Document


class BranchConfiguration(Document):

	def validate(self):
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
			if old_doc.get("branch"):
				_safe_delete_permission(user, "Branch", old_doc.branch, exclude_branch=self.name)

			if old_doc.get("company"):
				_safe_delete_permission(user, "Company", old_doc.company, exclude_branch=self.name)

			for w in old_doc.warehouse:
				_safe_delete_permission(user, "Warehouse", w.warehouse, exclude_branch=self.name)

			for c in old_doc.cost_center:
				_safe_delete_permission(user, "Cost Center", c.cost_center, exclude_branch=self.name)

			for m in old_doc.mode_of_payment:
				_safe_delete_permission(user, "Mode of Payment", m.mode_of_payment, exclude_branch=self.name)

			old_role_profile = next(
				(u.get("role_profile") for u in old_doc.user if u.user == user), None
			)
			if old_role_profile:
				_maybe_remove_roles_from_profile(user, old_role_profile, exclude_branch=self.name)

		# Remove MoP permissions for retained users when MOPs are removed
		old_mops = {m.mode_of_payment for m in old_doc.mode_of_payment if m.mode_of_payment}
		new_mops = {m.mode_of_payment for m in self.mode_of_payment if m.mode_of_payment}
		removed_mops = old_mops - new_mops
		if removed_mops:
			for user in old_users & new_users:
				for mop in removed_mops:
					_safe_delete_permission(user, "Mode of Payment", mop, exclude_branch=self.name)

		# Remove old company permission for retained users when company changes
		old_company = old_doc.get("company")
		new_company = self.get("company")
		if old_company and old_company != new_company:
			for u in self.user:
				_safe_delete_permission(u.user, "Company", old_company, exclude_branch=self.name)

		# Remove old branch permission for retained users when branch changes
		old_branch = old_doc.get("branch")
		new_branch = self.get("branch")
		if old_branch and old_branch != new_branch:
			for u in self.user:
				_safe_delete_permission(u.user, "Branch", old_branch, exclude_branch=self.name)

	def on_update(self):
		self.create_permissions()

	def create_permissions(self):
		for u in self.user:
			if self.branch:
				create_permission(u.user, "Branch", self.branch, is_default=1)

			if self.company:
				create_permission(u.user, "Company", self.company, is_default=1)

			for idx, w in enumerate(self.warehouse):
				create_permission(u.user, "Warehouse", w.warehouse, is_default=1 if idx == 0 else 0)

			for idx, c in enumerate(self.cost_center):
				create_permission(u.user, "Cost Center", c.cost_center, is_default=1 if idx == 0 else 0)

			for idx, m in enumerate(self.mode_of_payment):
				create_permission(u.user, "Mode of Payment", m.mode_of_payment, is_default=1 if idx == 0 else 0)

			# Grant access to the company's root cost center (used in tax templates) without making it default
			if self.company:
				company_default_cc = frappe.db.get_value("Company", self.company, "cost_center")
				if company_default_cc:
					create_permission(u.user, "Cost Center", company_default_cc, is_default=0)

			role_profile = u.get("role_profile")
			if role_profile:
				_apply_roles_from_profile(u.user, role_profile)


# ---------------------------------------------------------------------------
# User Permission helpers
# ---------------------------------------------------------------------------

def create_permission(user, allow, value, is_default=0):
	if not value:
		return

	existing = frappe.db.exists("User Permission", {
		"user": user,
		"allow": allow,
		"for_value": value,
	})

	if existing:
		if is_default and not _has_existing_default(user, allow, exclude_value=value):
			frappe.db.set_value("User Permission", existing, "is_default", 1)
		return

	if is_default and _has_existing_default(user, allow):
		is_default = 0

	try:
		doc = frappe.new_doc("User Permission")
		doc.user = user
		doc.allow = allow
		doc.for_value = value
		doc.is_default = is_default
		doc.apply_to_all_doctypes = 1
		doc.insert(ignore_permissions=True)
	except frappe.exceptions.DuplicateEntryError:
		pass
	except Exception:
		frappe.log_error(frappe.get_traceback(), f"BranchConfig: create {allow} for {user}")


def _has_existing_default(user, allow, exclude_value=None):
	filters = {"user": user, "allow": allow, "is_default": 1}
	if exclude_value:
		filters["for_value"] = ["!=", exclude_value]
	return frappe.db.exists("User Permission", filters)


def delete_permission(user, allow, value):
	if not value:
		return
	for p in frappe.get_all("User Permission", filters={"user": user, "allow": allow, "for_value": value}, pluck="name"):
		frappe.delete_doc("User Permission", p, ignore_permissions=True)


def _safe_delete_permission(user, allow, value, exclude_branch=None):
	"""Delete a User Permission only if no other Branch Configuration still grants it."""
	if not value:
		return

	other_branches = [
		c.parent for c in frappe.get_all(
			"Branch Configuration User",
			filters={"user": user},
			fields=["parent"],
			ignore_permissions=True,
		)
		if c.parent != exclude_branch
	]

	if other_branches:
		still_granted = False
		if allow == "Branch":
			still_granted = frappe.db.exists(
				"Branch Configuration",
				{"name": ["in", other_branches], "branch": value},
			)
		elif allow == "Warehouse":
			still_granted = frappe.db.exists(
				"Branch Configuration Warehouse",
				{"parent": ["in", other_branches], "warehouse": value},
			)
		elif allow == "Cost Center":
			still_granted = frappe.db.exists(
				"Branch Configuration Cost Center",
				{"parent": ["in", other_branches], "cost_center": value},
			)
		elif allow == "Mode of Payment":
			still_granted = frappe.db.exists(
				"Branch Configuration Mode of Payment",
				{"parent": ["in", other_branches], "mode_of_payment": value},
			)
		elif allow == "Company":
			still_granted = frappe.db.exists(
				"Branch Configuration",
				{"name": ["in", other_branches], "company": value},
			)

		if still_granted:
			return

	delete_permission(user, allow, value)


# ---------------------------------------------------------------------------
# Role helpers — direct DB operations (no user_doc.save) so Frappe's
# User.validate role-profile sync cannot wipe accumulated roles.
# ---------------------------------------------------------------------------

def _ensure_system_user(user):
	current_type = frappe.db.get_value("User", user, "user_type")
	if current_type and current_type != "System User":
		frappe.db.set_value("User", user, "user_type", "System User")


def _roles_in_profile(role_profile):
	return set(frappe.get_all(
		"Has Role",
		filters={"parent": role_profile, "parenttype": "Role Profile"},
		pluck="role",
		ignore_permissions=True,
	))


def _apply_roles_from_profile(user, role_profile):
	"""Insert Has Role rows directly — bypasses user_doc.save() to prevent
	Frappe's User.validate from wiping accumulated roles across multiple Branch Configs."""
	if not role_profile:
		return

	if not frappe.db.exists("Role Profile", role_profile):
		frappe.msgprint(
			f"Role Profile <b>{role_profile}</b> does not exist.",
			indicator="orange",
			alert=True,
		)
		return

	_ensure_system_user(user)

	new_roles = _roles_in_profile(role_profile)
	if not new_roles:
		return

	existing_roles = set(frappe.db.get_all(
		"Has Role",
		filters={"parent": user, "parenttype": "User"},
		pluck="role",
		ignore_permissions=True,
	))

	for role in new_roles - existing_roles:
		try:
			frappe.db.sql(
				"""INSERT IGNORE INTO `tabHas Role`
				(name, creation, modified, modified_by, owner, docstatus, idx,
				 parent, parentfield, parenttype, role)
				VALUES (%s, NOW(), NOW(), %s, %s, 0, 0, %s, 'roles', 'User', %s)""",
				(
					frappe.generate_hash(length=10),
					frappe.session.user,
					frappe.session.user,
					user,
					role,
				),
			)
		except Exception:
			frappe.log_error(
				f"Branch Configuration: could not grant role {role!r} to user {user!r}",
				"BranchConfig role grant",
			)

	frappe.clear_cache(user=user)


def _maybe_remove_roles_from_profile(user, role_profile, exclude_branch=None):
	if not role_profile:
		return

	other_profiles = {
		c.role_profile for c in frappe.get_all(
			"Branch Configuration User",
			filters={"user": user},
			fields=["parent", "role_profile"],
			ignore_permissions=True,
		)
		if c.parent != exclude_branch and c.role_profile
	}

	roles_to_remove = _roles_in_profile(role_profile)
	roles_to_keep = set()
	for profile in other_profiles:
		roles_to_keep |= _roles_in_profile(profile)

	to_delete = roles_to_remove - roles_to_keep
	if not to_delete:
		return

	frappe.db.delete("Has Role", {
		"parent": user,
		"parenttype": "User",
		"role": ["in", list(to_delete)],
	})
	frappe.clear_cache(user=user)
