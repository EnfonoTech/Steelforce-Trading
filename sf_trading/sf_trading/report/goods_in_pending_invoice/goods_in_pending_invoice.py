# Copyright (c) 2026, Enfono Technologies and contributors
# For license information, please see license.txt

"""Goods In Pending Invoice: goods recorded as received on a Goods In, how much of each has been
invoiced from it, and how much is still waiting for the supplier's invoice. See
sf_trading/goods_in.py."""

import frappe
from frappe import _
from frappe.utils import cint, flt


def execute(filters=None):
	filters = frappe._dict(filters or {})
	# the rows are Goods Ins, so the right to read them gates the report as well
	frappe.has_permission("Goods In", "read", throw=True)
	return get_columns(), get_data(filters)


# Which User Permission narrows which column, on the Goods In or on its row.
PERMISSION_COLUMNS = (
	("Company", "parent", "company"),
	("Supplier", "parent", "supplier"),
	("Purchase Order", "parent", "purchase_order"),
	("Warehouse", "row", "warehouse"),
	("Item", "row", "item_code"),
)


def get_permitted_scope(user=None):
	"""doctype -> the values the user may see, for the doctypes they are restricted on.

	`frappe.qb` applies no permission filtering, so without this a user restricted to one company
	would see every company's Goods In here while the desk list is restricted. A user with no User
	Permissions (Administrator, most head-office logins) gets an empty scope and is unaffected.
	"""
	from frappe.permissions import get_user_permissions

	permissions = get_user_permissions(user or frappe.session.user) or {}
	scope = {}
	for doctype, _table, _fieldname in PERMISSION_COLUMNS:
		allowed = {r.get("doc") for r in (permissions.get(doctype) or []) if r.get("doc")}
		if allowed:
			scope[doctype] = allowed
	return scope


def get_data(filters):
	gi = frappe.qb.DocType("Goods In")
	row = frappe.qb.DocType("Goods In Item")
	query = (
		frappe.qb.from_(row)
		.inner_join(gi)
		.on(gi.name == row.parent)
		.select(
			gi.name.as_("goods_in"),
			gi.posting_date,
			gi.supplier,
			gi.supplier_name,
			gi.purchase_order,
			row.item_code,
			row.item_name,
			row.warehouse,
			row.uom,
			row.ordered_qty,
			row.qty.as_("received_qty"),
			row.invoiced_qty,
		)
		.where(gi.docstatus == 1)
		.orderby(gi.posting_date)
		.orderby(gi.name)
		.orderby(row.idx)
	)
	for field in ("company", "supplier", "purchase_order"):
		if filters.get(field):
			query = query.where(gi[field] == filters.get(field))
	if filters.get("item_code"):
		query = query.where(row.item_code == filters.item_code)
	if filters.get("from_date"):
		query = query.where(gi.posting_date >= filters.from_date)
	if filters.get("to_date"):
		query = query.where(gi.posting_date <= filters.to_date)

	# a blank value passes, the way frappe treats an empty link field under a User Permission
	scope = get_permitted_scope()
	for doctype, table, fieldname in PERMISSION_COLUMNS:
		if scope.get(doctype):
			column = (gi if table == "parent" else row)[fieldname]
			query = query.where(column.isnull() | (column == "") | column.isin(list(scope[doctype])))

	data = []
	for r in query.run(as_dict=True):
		r.pending_qty = max(flt(r.received_qty) - flt(r.invoiced_qty), 0)
		if cint(filters.get("pending_only")) and not r.pending_qty:
			continue
		# the order's quantity repeats on every Goods In of that row, so it must not be summed
		# into the total row: shown as text, which the total skips
		r.ordered_qty = frappe.format(r.ordered_qty, {"fieldtype": "Float"})
		data.append(r)
	return data


def get_columns():
	return [
		{"label": _("Goods In"), "fieldname": "goods_in", "fieldtype": "Link", "options": "Goods In", "width": 150},
		{"label": _("Receipt Date"), "fieldname": "posting_date", "fieldtype": "Date", "width": 105},
		{"label": _("Supplier"), "fieldname": "supplier", "fieldtype": "Link", "options": "Supplier", "width": 150},
		{"label": _("Supplier Name"), "fieldname": "supplier_name", "fieldtype": "Data", "width": 170},
		{"label": _("Purchase Order"), "fieldname": "purchase_order", "fieldtype": "Link", "options": "Purchase Order", "width": 150},
		{"label": _("Item"), "fieldname": "item_code", "fieldtype": "Link", "options": "Item", "width": 140},
		{"label": _("Item Name"), "fieldname": "item_name", "fieldtype": "Data", "width": 180},
		{"label": _("Warehouse"), "fieldname": "warehouse", "fieldtype": "Link", "options": "Warehouse", "width": 120},
		{"label": _("UOM"), "fieldname": "uom", "fieldtype": "Link", "options": "UOM", "width": 70},
		{"label": _("Ordered Qty"), "fieldname": "ordered_qty", "fieldtype": "Data", "width": 100, "align": "right"},
		{"label": _("Received Qty (Goods In)"), "fieldname": "received_qty", "fieldtype": "Float", "width": 150},
		{"label": _("Invoiced Qty"), "fieldname": "invoiced_qty", "fieldtype": "Float", "width": 105},
		{"label": _("Pending Invoice Qty"), "fieldname": "pending_qty", "fieldtype": "Float", "width": 140},
	]
