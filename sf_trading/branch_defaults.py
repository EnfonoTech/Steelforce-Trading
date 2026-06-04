import frappe


def item_permission_query(user=None):
	"""Restrict Item list/search to items that have an Item Default for the
	user's permitted company. Bypassed for System Manager and Administrator.
	"""
	user = user or frappe.session.user
	if user in ("Administrator", "Guest"):
		return ""

	if "System Manager" in frappe.get_roles(user):
		return ""

	company = (
		frappe.defaults.get_user_default("company")
		or frappe.defaults.get_user_default("Company")
	)
	if not company:
		return ""

	company_escaped = frappe.db.escape(company)
	return (
		"`tabItem`.`name` IN ("
		"  SELECT `parent` FROM `tabItem Default`"
		"  WHERE `company` = {company}"
		")".format(company=company_escaped)
	)


def override_cost_center_from_branch(doc, method=None):
	"""before_validate: replace any cost center the user cannot access with
	their default branch cost center — covers doc header, items, and tax rows.
	Bypassed for Administrator and Guest.
	"""
	if frappe.session.user in ("Administrator", "Guest"):
		return

	user_cost_center = _get_user_default_cost_center()
	if not user_cost_center:
		return

	def fix(cc):
		if cc and not _user_has_cost_center_access(cc):
			return user_cost_center
		if not cc:
			return user_cost_center
		return cc

	if "cost_center" in doc.as_dict():
		doc.cost_center = fix(doc.get("cost_center"))

	for item in doc.get("items") or []:
		if hasattr(item, "cost_center"):
			item.cost_center = fix(item.cost_center)

	for tax in doc.get("taxes") or []:
		if hasattr(tax, "cost_center"):
			tax.cost_center = fix(tax.cost_center)


def _get_user_default_cost_center():
	return frappe.db.get_value(
		"User Permission",
		{"user": frappe.session.user, "allow": "Cost Center", "is_default": 1},
		"for_value",
	)


def _user_has_cost_center_access(cost_center):
	if not cost_center:
		return True
	return frappe.db.exists(
		"User Permission",
		{"user": frappe.session.user, "allow": "Cost Center", "for_value": cost_center},
	)


def _resolve_user_branch(user, company=None):
	"""Return the most relevant Branch Configuration name for the user.

	Resolution order:
	  1. Branch Configuration whose company matches the doc's company.
	  2. First Branch Configuration the user belongs to (any company).
	"""
	branches = frappe.get_all(
		"Branch Configuration User",
		filters={"user": user},
		pluck="parent",
	)
	if not branches:
		return None

	if company:
		same_company = frappe.get_all(
			"Branch Configuration",
			filters={"name": ["in", branches], "company": company},
			pluck="name",
		)
		if same_company:
			return same_company[0]

	return branches[0]


def _branch_mops_by_type(branch_name):
	"""Return (cash_mops, bank_mops) lists for the branch, in child-table order."""
	cash, bank = [], []
	if not branch_name:
		return cash, bank

	rows = frappe.get_all(
		"Branch Configuration Mode of Payment",
		filters={"parent": branch_name, "parenttype": "Branch Configuration"},
		fields=["mode_of_payment"],
		order_by="idx asc",
	)
	if not rows:
		return cash, bank

	mop_names = [r.mode_of_payment for r in rows if r.mode_of_payment]
	if not mop_names:
		return cash, bank

	type_map = {
		m["name"]: (m.get("type") or "")
		for m in frappe.get_all(
			"Mode of Payment",
			filters={"name": ["in", mop_names]},
			fields=["name", "type"],
		)
	}
	for r in rows:
		t = type_map.get(r.mode_of_payment)
		if t == "Cash":
			cash.append(r.mode_of_payment)
		elif t == "Bank":
			bank.append(r.mode_of_payment)
	return cash, bank


def _mop_account_for_company(mop, company):
	if not (mop and company):
		return None
	return frappe.db.get_value(
		"Mode of Payment Account",
		{"parent": mop, "company": company},
		"default_account",
	)


def override_payment_accounts_from_branch(doc, method=None):
	"""before_validate on Sales Invoice: constrain payment rows to the
	user's Branch Configuration MoP allowlist.

	- Cash/Bank rows not in the branch list are swapped to the branch default.
	- If the branch has no MoPs of a type, rows of that type are dropped.
	- Other types (General, Phone, etc.) pass through untouched.
	- Bypassed for Administrator, Guest, System Manager, Sales Manager.
	"""
	user = frappe.session.user
	if user in ("Administrator", "Guest"):
		return

	roles = set(frappe.get_roles(user))
	if roles & {"System Manager", "Sales Manager", "Sales Master Manager"}:
		return

	payments = doc.get("payments") or []
	if not payments:
		return

	branch = _resolve_user_branch(user, company=doc.company)
	if not branch:
		return

	cash_mops, bank_mops = _branch_mops_by_type(branch)
	if not cash_mops and not bank_mops:
		# Branch has no MoP rows configured — opt-out, leave untouched
		return

	type_cache = {}

	def mop_type(name):
		if name not in type_cache:
			type_cache[name] = frappe.db.get_value("Mode of Payment", name, "type") or ""
		return type_cache[name]

	kept_rows = []
	for row in payments:
		mop = getattr(row, "mode_of_payment", None)
		if not mop:
			kept_rows.append(row)
			continue

		t = mop_type(mop)
		if t == "Cash":
			allowed = cash_mops
		elif t == "Bank":
			allowed = bank_mops
		else:
			kept_rows.append(row)
			continue

		if not allowed:
			# Branch opted out of this type — drop the row
			continue

		if mop not in allowed:
			row.mode_of_payment = allowed[0]

		new_account = _mop_account_for_company(row.mode_of_payment, doc.company)
		if new_account:
			row.account = new_account

		kept_rows.append(row)

	if len(kept_rows) != len(payments):
		doc.set("payments", [])
		for r in kept_rows:
			doc.append("payments", r.as_dict() if hasattr(r, "as_dict") else r)
