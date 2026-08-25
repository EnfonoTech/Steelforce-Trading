# apps/sf_trading/sf_trading/patches/v0_1/branch_price_lists_to_branch_config.py
"""Move the branch/price-list mapping from Price List onto Branch Configuration.

It started as an "Applicable for Branches" table on Price List, mirroring ERPNext's Applicable for
Countries. Branch Configuration is the better home: it is already the one place a branch is
described and the one place its access is controlled, and it can hold several lists per branch with
one marked default -- which a field on Price List could not express.

Anything configured in the meantime is carried across, then the old field and its child doctype are
taken away. The first list a branch names becomes its default, because that is what the old model
meant: one list per branch.
"""

import frappe

OLD_CHILD = "Price List Branch"
OLD_CUSTOM_FIELD = "Price List-custom_branches"
NEW_CHILD = "Branch Configuration Price List"


def execute():
	if frappe.db.table_exists(OLD_CHILD):
		_carry_rows_across()

	if frappe.db.exists("Custom Field", OLD_CUSTOM_FIELD):
		frappe.delete_doc("Custom Field", OLD_CUSTOM_FIELD, force=True, ignore_permissions=True)
		frappe.clear_cache(doctype="Price List")

	if frappe.db.exists("DocType", OLD_CHILD):
		frappe.delete_doc("DocType", OLD_CHILD, force=True, ignore_permissions=True)

	frappe.db.commit()


def _carry_rows_across():
	rows = frappe.db.sql(
		"""select parent as price_list, branch from `tab{0}`
		   where parenttype = 'Price List' and ifnull(branch, '') != ''""".format(OLD_CHILD),
		as_dict=True,
	)
	if not rows:
		return

	by_branch = {}
	for row in rows:
		by_branch.setdefault(row.branch, []).append(row.price_list)

	for branch, price_lists in by_branch.items():
		if not frappe.db.exists("Branch Configuration", branch):
			frappe.logger().info(
				"sf_trading: branch %s has no Branch Configuration, price lists %s not carried"
				% (branch, price_lists)
			)
			continue

		config = frappe.get_doc("Branch Configuration", branch)
		existing = {row.price_list for row in (config.get("price_list") or [])}
		for idx, price_list in enumerate(price_lists):
			if price_list in existing:
				continue
			config.append(
				"price_list",
				{"price_list": price_list, "is_default": 1 if idx == 0 and not existing else 0},
			)
		config.flags.ignore_permissions = True
		config.flags.ignore_validate = True
		config.save()
		frappe.logger().info("sf_trading: branch %s now names price lists %s" % (branch, price_lists))
