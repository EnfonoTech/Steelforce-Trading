"""Which Items belong to a Company.

An ordinary item declares its company through a row in its `Item Defaults` table
(`Item Default`, one row per company). Every company filter in this app was built
on that one table.

A fixed asset item can never have such a row. ERPNext hides the whole Accounting
tab on Item behind `depends_on: eval:!doc.is_fixed_asset`, and `item_defaults`
lives inside that tab — so for `is_fixed_asset = 1` there is no field to fill in.
A fixed asset carries its company through its Asset Category instead: the
mandatory `asset_category` link points at an `Asset Category` whose `accounts`
table (`Asset Category Account`) holds one row per company, keyed on `company_name`.

Both routes mean the same thing — "this item is used by this company" — so every
company filter on Item has to accept either, or fixed assets disappear from the
item list and from every item_code link field.
"""

import frappe


def asset_categories_for_companies(companies) -> list:
	"""Asset Categories that have an account row for any of `companies`."""
	companies = [c for c in (companies or []) if c]
	if not companies:
		return []

	return frappe.get_all(
		"Asset Category Account",
		filters={"company_name": ["in", companies], "parenttype": "Asset Category"},
		pluck="parent",
		ignore_permissions=True,
	)


def fixed_asset_codes_for_companies(companies) -> list:
	"""Item codes of fixed assets whose Asset Category covers any of `companies`."""
	categories = asset_categories_for_companies(companies)
	if not categories:
		return []

	return frappe.get_all(
		"Item",
		filters={"is_fixed_asset": 1, "asset_category": ["in", categories]},
		pluck="name",
		ignore_permissions=True,
	)


def item_codes_for_companies(companies) -> list:
	"""Every item code belonging to any of `companies`, by either route.

	Order is not significant; duplicates are removed.
	"""
	companies = [c for c in (companies or []) if c]
	if not companies:
		return []

	codes = frappe.get_all(
		"Item Default",
		filters={"company": ["in", companies]},
		pluck="parent",
		ignore_permissions=True,
	)
	codes += fixed_asset_codes_for_companies(companies)
	return list(dict.fromkeys(codes))


def company_condition_sql(companies, item_table: str = "`tabItem`") -> str:
	"""SQL predicate restricting `item_table` rows to items of `companies`.

	Returns "" when there is nothing to restrict by, so callers can hand the
	result straight to `permission_query_conditions`.
	"""
	companies = [c for c in (companies or []) if c]
	if not companies:
		return ""

	company_list = ", ".join(frappe.db.escape(c) for c in companies)
	return (
		"({item}.`name` IN ("
		"  SELECT `parent` FROM `tabItem Default`"
		"  WHERE `company` IN ({companies})"
		") OR ({item}.`is_fixed_asset` = 1 AND {item}.`asset_category` IN ("
		"  SELECT `parent` FROM `tabAsset Category Account`"
		"  WHERE `parenttype` = 'Asset Category' AND `company_name` IN ({companies})"
		")))"
	).format(item=item_table, companies=company_list)
