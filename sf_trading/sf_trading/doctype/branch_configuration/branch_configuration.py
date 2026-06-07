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
			if old_doc.get("company"):
				delete_permission(user, "Company", old_doc.company)

			for w in old_doc.warehouse:
				delete_permission(user, "Warehouse", w.warehouse)

			for c in old_doc.cost_center:
				delete_permission(user, "Cost Center", c.cost_center)

			for m in old_doc.mode_of_payment:
				delete_permission(user, "Mode of Payment", m.mode_of_payment)

			old_role_profile = next(
				(u.get("role_profile") for u in old_doc.user if u.user == user), None
			)
			if old_role_profile:
				_maybe_remove_role_profile(user, old_role_profile, exclude_branch=self.name)

		# Remove mode of payment permissions for retained users when MOPs are removed
		old_mops = {m.mode_of_payment for m in old_doc.mode_of_payment if m.mode_of_payment}
		new_mops = {m.mode_of_payment for m in self.mode_of_payment if m.mode_of_payment}
		removed_mops = old_mops - new_mops
		if removed_mops:
			for user in old_users & new_users:
				for mop in removed_mops:
					delete_permission(user, "Mode of Payment", mop)

		# Remove old company permission for retained users when company changes
		old_company = old_doc.get("company")
		new_company = self.get("company")
		if old_company and old_company != new_company:
			for u in self.user:
				delete_permission(u.user, "Company", old_company)

	def on_update(self):
		self.create_permissions()

	def create_permissions(self):
		for u in self.user:
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
				_apply_role_profile(u.user, role_profile)


def create_permission(user, allow, value, is_default=0):
	if not value:
		return

	existing = frappe.db.exists("User Permission", {
		"user": user,
		"allow": allow,
		"for_value": value
	})

	if existing:
		if is_default and not _has_existing_default(user, allow, exclude_value=value):
			frappe.db.set_value("User Permission", existing, "is_default", 1)
	else:
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
	filters = {"user": user, "allow": allow, "is_default": 1}
	if exclude_value:
		filters["for_value"] = ["!=", exclude_value]
	return frappe.db.exists("User Permission", filters)


def delete_permission(user, allow, value):
	if not value:
		return

	for p in frappe.get_all("User Permission", filters={"user": user, "allow": allow, "for_value": value}, pluck="name"):
		frappe.delete_doc("User Permission", p, ignore_permissions=True)


def _ensure_system_user(user):
	current_type = frappe.db.get_value("User", user, "user_type")
	if current_type and current_type != "System User":
		frappe.db.set_value("User", user, "user_type", "System User")


def _apply_role_profile(user, role_profile):
	if not role_profile:
		return

	if not frappe.db.exists("Role Profile", role_profile):
		frappe.msgprint(
			f"Role Profile <b>{role_profile}</b> does not exist. Please create it in Setup > Role Profile.",
			indicator="orange",
			alert=True,
		)
		return

	_ensure_system_user(user)

	current = frappe.db.get_value("User", user, "role_profile")
	if current == role_profile:
		return

	user_doc = frappe.get_doc("User", user)
	user_doc.role_profile = role_profile
	user_doc.save(ignore_permissions=True)


def _maybe_remove_role_profile(user, role_profile, exclude_branch=None):
	"""Clear role profile from user only if no other Branch Configuration assigns the same profile to this user."""
	if not role_profile:
		return

	other_configs = frappe.get_all(
		"Branch Configuration User",
		filters={"user": user, "role_profile": role_profile},
		fields=["parent"],
	)

	has_profile_elsewhere = any(c.parent != exclude_branch for c in other_configs)
	if has_profile_elsewhere:
		return

	current = frappe.db.get_value("User", user, "role_profile")
	if current == role_profile:
		user_doc = frappe.get_doc("User", user)
		user_doc.role_profile = None
		user_doc.save(ignore_permissions=True)
