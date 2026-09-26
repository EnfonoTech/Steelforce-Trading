import frappe
from frappe import _
from frappe.utils import flt, fmt_money


def permission_query_conditions_for_customer(user):
	"""
	Additional SQL WHERE condition for Customer list/link queries.

	- No Company User Permission: show all customers (no restriction)
	- Has Company User Permission: ERPNext standard permissions filter by company;
	  additionally hide customers with no custom_company set, and restrict
	  credit customers to branches the user has access to.
	"""
	if not user:
		user = frappe.session.user

	companies = frappe.get_all(
		"User Permission",
		filters={"user": user, "allow": "Company"},
		pluck="for_value",
	)
	if not companies:
		return ""

	# Hide customers with no company set
	conditions = [
		"`tabCustomer`.custom_company IS NOT NULL",
		"`tabCustomer`.custom_company != ''",
	]

	# Restrict credit customers to branches the user has access to
	branches = frappe.get_all(
		"User Permission",
		filters={"user": user, "allow": "Branch"},
		pluck="for_value",
	)
	if branches:
		branch_list = ", ".join([frappe.db.escape(b) for b in branches])
		conditions.append("""(
			`tabCustomer`.name NOT IN (
				SELECT parent FROM `tabCustomer Credit Limit` WHERE credit_limit > 0
			)
			OR `tabCustomer`.name IN (
				SELECT parent FROM `tabCustomer Branch Access` WHERE branch IN ({0})
			)
		)""".format(branch_list))

	return " AND ".join(conditions)


def validate_credit_branch_access(doc, _method=None):
	"""Require at least one branch access row when a credit limit is set."""
	has_credit = any(flt(row.get("credit_limit")) > 0 for row in (doc.credit_limits or []))
	if has_credit and not doc.get("custom_branch_access"):
		frappe.throw(
			_("At least one Branch must be added in Branch Access when a Credit Limit is set."),
			title=_("Branch Access Required"),
		)


def _sum_positive_branch_credit_limits(doc) -> float:
	"""Sum of every Branch Access row that carries its OWN sub-limit. A 0/blank row opts that
	branch out of a sub-cap (it can then draw on the full company-wide limit), so it contributes
	nothing here -- only branches the client deliberately capped count toward the allocated total."""
	return sum(
		flt(row.get("credit_limit")) for row in (doc.custom_branch_access or []) if flt(row.get("credit_limit")) > 0
	)


def _sum_company_credit_limits(doc) -> float:
	return sum(flt(row.get("credit_limit")) for row in (doc.credit_limits or []))


def validate_branch_credit_limit_allocation(doc, _method=None):
	"""Customer validate: the branches that DO carry their own sub-limit may not collectively
	promise more credit than the customer's own company-wide Credit Limit allows (GS Issue 19 --
	the "5,000 at one branch + 5,000 at another, missed" case). Branches left at 0 are not part of
	this sum (see _sum_positive_branch_credit_limits) -- they stay uncapped individually and are
	still bounded only by the company-wide check sf_trading.credit_limit already runs.

	This is a setup-time sanity check only. The actual point-of-sale enforcement -- refusing a
	Sales Order/Invoice that would push one branch's own live exposure past its sub-limit -- is
	sf_trading.credit_limit.check_branch_credit_limit, called from both override classes'
	check_credit_limit().
	"""
	allocated = _sum_positive_branch_credit_limits(doc)
	if allocated <= 0:
		return

	total = _sum_company_credit_limits(doc)
	if allocated > total:
		frappe.throw(
			_("Branch Credit Limits add up to %s, more than this customer's own company-wide Credit "
				"Limit of %s. Lower a branch's Credit Limit, or raise the Credit Limit above.")
			% (fmt_money(allocated), fmt_money(total)),
			title=_("Branch Credit Limits Exceed Total"),
		)


@frappe.whitelist()
@frappe.validate_and_sanitize_search_inputs
def customer_query_credit_branch(doctype, txt, searchfield, start, page_len, filters, as_dict=False):
	"""Custom search query for the customer link on Sales Invoice.

	Returns credit customers (credit_limit > 0), narrowed to the invoice's own branch once one is
	chosen. Before that -- a fresh Sales Invoice starts with Branch blank -- this used to return
	nothing at all for every credit customer, with no hint why (confirmed live: an existing,
	fully-branch-accessed customer produced an empty dropdown, "Filters applied for Company = ..."
	and no rows, on a brand new invoice). Branch-scoped enforcement still runs at submit time
	regardless (sf_trading.credit_limit.check_branch_credit_limit); this is only the search box,
	and an empty Branch here should widen the search, not zero it out.
	"""
	branch = (filters or {}).get("branch") or ""
	company = (filters or {}).get("company") or ""

	if branch:
		branch_cond = "AND `tabCustomer`.name IN (SELECT parent FROM `tabCustomer Branch Access` WHERE branch = %(branch)s)"
	else:
		branch_cond = ""

	if company:
		company_cond = "AND `tabCustomer`.custom_company = %(company)s"
	else:
		company_cond = ""

	return frappe.db.sql(
		"""
		SELECT `tabCustomer`.name, `tabCustomer`.customer_name
		FROM `tabCustomer`
		WHERE EXISTS (
			SELECT 1 FROM `tabCustomer Credit Limit`
			WHERE parent = `tabCustomer`.name AND credit_limit > 0
		)
		AND `tabCustomer`.custom_company IS NOT NULL
		AND `tabCustomer`.custom_company != ''
		AND (`tabCustomer`.{searchfield} LIKE %(txt)s OR `tabCustomer`.customer_name LIKE %(txt)s)
		{branch_cond}
		{company_cond}
		ORDER BY `tabCustomer`.name
		LIMIT {start}, {page_len}
		""".format(
			searchfield=searchfield,
			branch_cond=branch_cond,
			company_cond=company_cond,
			start=int(start),
			page_len=int(page_len),
		),
		{"txt": f"%{txt}%", "branch": branch, "company": company},
		as_dict=as_dict,
	)


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
