import frappe
from frappe import _


@frappe.whitelist()
def get_items_with_stock(company=None, price_list=None, item_code=None, limit=200):
	"""
	Return items that have stock in any non-group warehouse of the company,
	one row per (item, warehouse) combination, with the selling rate from
	the given price list when available.

	Honours User Permissions on Warehouse: if the current user is restricted
	to one or more warehouses, only those will be returned.
	"""
	from frappe.core.doctype.user_permission.user_permission import get_permitted_documents

	if not company:
		company = frappe.defaults.get_user_default("company")

	if not company:
		return []

	limit = int(limit or 200)

	# Only show items configured for this company in Item Defaults
	allowed_items = frappe.get_all(
		"Item Default",
		filters={"company": company},
		pluck="parent",
		ignore_permissions=True,
	)
	if not allowed_items:
		return []

	params = {"company": company, "limit": limit, "allowed_items": allowed_items}
	where = [
		"bin.actual_qty > 0",
		"item.is_sales_item = 1",
		"item.disabled = 0",
		"wh.company = %(company)s",
		"wh.disabled = 0",
		"wh.is_group = 0",
		"bin.item_code IN %(allowed_items)s",
	]

	if item_code:
		params["item_code"] = item_code
		where.append("bin.item_code = %(item_code)s")

	# Restrict to warehouses the user is permitted to access via User Permissions.
	# Administrator / users with no Warehouse permissions set see all warehouses.
	permitted_warehouses = get_permitted_documents("Warehouse")
	if permitted_warehouses:
		params["permitted_warehouses"] = permitted_warehouses
		where.append("bin.warehouse IN %(permitted_warehouses)s")

	price_join = ""
	price_select = "NULL AS selling_rate, NULL AS price_currency"
	if price_list:
		params["price_list"] = price_list
		price_join = (
			"LEFT JOIN `tabItem Price` ip "
			"ON ip.item_code = bin.item_code "
			"AND ip.price_list = %(price_list)s "
			"AND ip.selling = 1"
		)
		price_select = "ip.price_list_rate AS selling_rate, ip.currency AS price_currency"

	where_sql = " AND ".join(where)

	rows = frappe.db.sql(
		"""
		SELECT
			bin.item_code,
			item.item_name,
			item.stock_uom,
			bin.warehouse,
			bin.actual_qty AS stock_qty,
			{price_select}
		FROM `tabBin` bin
		INNER JOIN `tabItem` item ON item.name = bin.item_code
		INNER JOIN `tabWarehouse` wh ON wh.name = bin.warehouse
		{price_join}
		WHERE {where_sql}
		ORDER BY item.item_name, bin.warehouse
		LIMIT %(limit)s
		""".format(
			price_select=price_select,
			price_join=price_join,
			where_sql=where_sql,
		),
		params,
		as_dict=True,
	)

	return rows
