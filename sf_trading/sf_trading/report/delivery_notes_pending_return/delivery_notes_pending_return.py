# Copyright (c) 2026, Enfono Technologies and contributors
# For license information, please see license.txt

"""Delivery Notes whose invoice was credited but whose goods were never returned to stock.

A Sales Return raised on the Sales Invoice (a credit note with Update Stock off) credits the
customer and writes NO stock ledger entry. When that invoice's goods left through a Delivery Note,
the stock only comes back through a return of the Delivery Note itself. Where the first was done
and the second was not, the books say the goods were returned while the warehouse still shows them
as delivered -- MAT-DN-2026-00071 / invoice 20010000667 / return 20010000754 is the pattern.

For each Delivery Note line linked to an invoice line (in either direction: the delivery made from
the invoice, `si_detail`, or the invoice made from the delivery, `dn_detail`):

    credited  = qty of that invoice line returned on submitted credit notes that restock nothing
    returned  = qty of this delivery line already returned by submitted Delivery Note returns
    pending   = credited not yet returned, never more than was delivered

Credit notes that restock themselves (Update Stock on) are not pending: the stock came back with
them. A credit note on an invoice with no delivery behind it has no Delivery Note to return, so it
is not listed here.
"""

from collections import defaultdict

import frappe
from frappe import _
from frappe.utils import flt

PENDING = "Pending Return"
RETURNED = "Returned"
TOLERANCE = 0.0005

LINE_WISE = "Line Wise"
DOCUMENT_WISE = "Document Wise"
ITEM_WISE = "Item Wise"

# Which User Permission narrows which column of the delivery line
PERMISSION_COLUMNS = (
	("Customer", "customer"),
	("Warehouse", "warehouse"),
	("Branch", "branch"),
	("Cost Center", "cost_center"),
)


def execute(filters=None):
	filters = frappe._dict(filters or {})
	frappe.has_permission("Delivery Note", "read", throw=True)
	if not filters.company:
		frappe.throw(_("Company is required"))

	rows = get_rows(filters)
	for r in rows:
		# SUM() comes back as a Decimal
		r.credited_qty = flt(r.credited_qty)
		r.returned_qty = flt(r.returned_qty)
	allocate_pending(rows)

	scope = get_permitted_scope()
	rows = [r for r in rows if is_permitted(r, scope)]
	if filters.get("show") != "All Credited":
		rows = [r for r in rows if r.status == PENDING]

	currency = frappe.get_cached_value("Company", filters.company, "default_currency")
	for r in rows:
		r.pending_value = flt(r.pending_qty * flt(r.rate), 3)
		r.currency = currency

	rows.sort(key=lambda r: (r.posting_date, r.delivery_note, r.dn_row))
	# the cards always describe the lines, whichever way they are laid out below
	summary = get_summary(rows, currency)
	view = filters.get("view") or LINE_WISE
	if view == DOCUMENT_WISE:
		rows = group_by_document(rows, currency)
	elif view == ITEM_WISE:
		rows = group_by_item(rows, currency)
	return get_columns(view), rows, None, None, summary


def get_rows(filters):
	"""One row per delivery line that has a credited invoice line behind it."""
	meta = frappe.get_meta("Delivery Note")
	item_meta = frappe.get_meta("Delivery Note Item")
	branch = "dn.branch" if meta.has_field("branch") else "NULL"
	cost_center = "dnr.cost_center" if item_meta.has_field("cost_center") else "NULL"

	conditions = ["dn.docstatus = 1", "dn.is_return = 0", "dn.company = %(company)s"]
	values = {"company": filters.company}
	if filters.get("delivery_note"):
		conditions.append("dnr.parent = %(delivery_note)s")
		values["delivery_note"] = filters.delivery_note
	if filters.get("invoice"):
		conditions.append("pair.invoice = %(invoice)s")
		values["invoice"] = filters.invoice
	if filters.get("credit_note"):
		# only the lines this credit note actually credited
		conditions.append(
			"""EXISTS (SELECT 1 FROM `tabSales Invoice Item` cn
				WHERE cn.parent = %(credit_note)s AND cn.sales_invoice_item = pair.si_row)"""
		)
		values["credit_note"] = filters.credit_note
	if filters.get("item_group"):
		# the group and everything under it, by the tree's own bounds (no long IN list)
		lft, rgt = frappe.db.get_value("Item Group", filters.item_group, ["lft", "rgt"]) or (0, 0)
		conditions.append("ig.lft >= %(ig_lft)s AND ig.rgt <= %(ig_rgt)s")
		values["ig_lft"], values["ig_rgt"] = lft, rgt
	if filters.get("from_date"):
		conditions.append("dn.posting_date >= %(from_date)s")
		values["from_date"] = filters.from_date
	if filters.get("to_date"):
		conditions.append("dn.posting_date <= %(to_date)s")
		values["to_date"] = filters.to_date
	if filters.get("customer"):
		conditions.append("dn.customer = %(customer)s")
		values["customer"] = filters.customer
	if filters.get("item_code"):
		conditions.append("dnr.item_code = %(item_code)s")
		values["item_code"] = filters.item_code

	return frappe.db.sql(
		f"""
		SELECT
			pair.dn_row,
			dnr.parent AS delivery_note,
			dn.posting_date,
			dn.customer,
			dn.customer_name,
			dnr.item_code,
			dnr.item_name,
			it.item_group,
			dnr.warehouse,
			dnr.qty AS delivered_qty,
			dnr.base_net_rate AS rate,
			{branch} AS branch,
			{cost_center} AS cost_center,
			pair.si_row,
			pair.invoice,
			cr.credited AS credited_qty,
			cr.credit_notes,
			COALESCE(rt.returned, 0) AS returned_qty,
			rt.return_notes
		FROM (
			SELECT dnr.name AS dn_row, sir.name AS si_row, sir.parent AS invoice
			FROM `tabDelivery Note Item` dnr
			INNER JOIN `tabSales Invoice Item` sir ON sir.name = dnr.si_detail
			WHERE IFNULL(dnr.si_detail, '') != ''
			UNION
			SELECT sir.dn_detail AS dn_row, sir.name AS si_row, sir.parent AS invoice
			FROM `tabSales Invoice Item` sir
			WHERE IFNULL(sir.dn_detail, '') != ''
		) pair
		INNER JOIN `tabDelivery Note Item` dnr ON dnr.name = pair.dn_row
		INNER JOIN `tabDelivery Note` dn ON dn.name = dnr.parent
		LEFT JOIN `tabItem` it ON it.name = dnr.item_code
		LEFT JOIN `tabItem Group` ig ON ig.name = it.item_group
		INNER JOIN `tabSales Invoice` si ON si.name = pair.invoice AND si.docstatus = 1 AND si.is_return = 0
		INNER JOIN (
			SELECT
				ret.sales_invoice_item AS si_row,
				SUM(ABS(ret.qty)) AS credited,
				GROUP_CONCAT(DISTINCT ret.parent ORDER BY ret.parent SEPARATOR ', ') AS credit_notes
			FROM `tabSales Invoice Item` ret
			INNER JOIN `tabSales Invoice` rs
				ON rs.name = ret.parent AND rs.docstatus = 1 AND rs.is_return = 1 AND rs.update_stock = 0
			WHERE IFNULL(ret.sales_invoice_item, '') != ''
			GROUP BY ret.sales_invoice_item
		) cr ON cr.si_row = pair.si_row
		LEFT JOIN (
			SELECT
				rdi.dn_detail AS dn_row,
				SUM(ABS(rdi.qty)) AS returned,
				GROUP_CONCAT(DISTINCT rdi.parent ORDER BY rdi.parent SEPARATOR ', ') AS return_notes
			FROM `tabDelivery Note Item` rdi
			INNER JOIN `tabDelivery Note` rd ON rd.name = rdi.parent AND rd.docstatus = 1 AND rd.is_return = 1
			WHERE IFNULL(rdi.dn_detail, '') != ''
			GROUP BY rdi.dn_detail
		) rt ON rt.dn_row = pair.dn_row
		WHERE {" AND ".join(conditions)}
		""",
		values,
		as_dict=True,
	)


def allocate_pending(rows):
	"""Work out `pending_qty` and `status` for every row, in place.

	The credit belongs to the invoice line, so when one invoice line was delivered in several
	Delivery Notes it is shared out oldest delivery first, after what those deliveries have
	already had returned. Nothing is ever pending beyond what was delivered less what came back.
	"""
	by_invoice_line = defaultdict(list)
	for row in rows:
		by_invoice_line[row.si_row].append(row)

	for group in by_invoice_line.values():
		group.sort(key=lambda r: (r.posting_date, r.dn_row))
		pool = flt(group[0].credited_qty) - sum(flt(r.returned_qty) for r in group)
		for row in group:
			room = flt(row.delivered_qty) - flt(row.returned_qty)
			take = max(0.0, min(pool, room))
			pool -= take
			row.pending_qty = flt(take, 3) if take > TOLERANCE else 0.0
			row.status = PENDING if row.pending_qty else RETURNED
	return rows


def _join(values):
	"""Distinct non-blank values of a comma-joined column, in order, as one string."""
	seen = []
	for value in values:
		for part in (value or "").split(","):
			part = part.strip()
			if part and part not in seen:
				seen.append(part)
	return ", ".join(seen)


def _roll_up(group, **identity):
	"""One row summarising a set of lines."""
	pending = flt(sum(r.pending_qty for r in group), 3)
	return frappe._dict(
		lines=len(group),
		delivered_qty=flt(sum(r.delivered_qty for r in group), 3),
		credited_qty=flt(sum(r.credited_qty for r in group), 3),
		returned_qty=flt(sum(r.returned_qty for r in group), 3),
		pending_qty=pending,
		pending_value=flt(sum(r.pending_value for r in group), 3),
		invoice=_join(r.invoice for r in group),
		credit_notes=_join(r.credit_notes for r in group),
		return_notes=_join(r.return_notes for r in group),
		status=PENDING if pending else RETURNED,
		**identity,
	)


def group_by_document(rows, currency=None):
	"""One row per Delivery Note."""
	groups = defaultdict(list)
	for r in rows:
		groups[r.delivery_note].append(r)
	out = []
	for delivery_note, group in groups.items():
		first = group[0]
		out.append(
			_roll_up(
				group,
				delivery_note=delivery_note,
				posting_date=first.posting_date,
				customer=first.customer,
				customer_name=first.customer_name,
				currency=currency,
			)
		)
	out.sort(key=lambda r: (r.posting_date, r.delivery_note))
	return out


def group_by_item(rows, currency=None):
	"""One row per item, across every Delivery Note it is pending on."""
	groups = defaultdict(list)
	for r in rows:
		groups[r.item_code].append(r)
	out = []
	for item_code, group in groups.items():
		first = group[0]
		out.append(
			_roll_up(
				group,
				item_code=item_code,
				item_name=first.item_name,
				item_group=first.item_group,
				delivery_notes=len({r.delivery_note for r in group}),
				currency=currency,
			)
		)
	out.sort(key=lambda r: (-r.pending_value, r.item_code))
	return out


def get_permitted_scope(user=None):
	"""doctype -> the values the user may see, for the doctypes they are restricted on.

	`frappe.db.sql` applies no permission filtering, so without this a user restricted to one
	branch, warehouse or customer set would see every delivery here while the desk list is
	restricted. A user with no User Permissions (Administrator, most head-office logins) gets an
	empty scope and is unaffected.
	"""
	from frappe.permissions import get_user_permissions

	permissions = get_user_permissions(user or frappe.session.user) or {}
	scope = {}
	for doctype, _fieldname in PERMISSION_COLUMNS:
		allowed = {row.get("doc") for row in (permissions.get(doctype) or []) if row.get("doc")}
		if allowed:
			scope[doctype] = allowed
	return scope


def is_permitted(row, scope):
	"""False when the line lies outside a restriction the user has. A blank value passes, the way
	frappe treats an empty link field under a User Permission."""
	for doctype, fieldname in PERMISSION_COLUMNS:
		allowed = scope.get(doctype)
		value = row.get(fieldname)
		if allowed and value and value not in allowed:
			return False
	return True


def get_columns(view=LINE_WISE):
	if view == DOCUMENT_WISE:
		return [
			{"label": _("Delivery Note"), "fieldname": "delivery_note", "fieldtype": "Link", "options": "Delivery Note", "width": 160},
			{"label": _("Date"), "fieldname": "posting_date", "fieldtype": "Date", "width": 100},
			{"label": _("Customer"), "fieldname": "customer", "fieldtype": "Link", "options": "Customer", "width": 160},
			{"label": _("Customer Name"), "fieldname": "customer_name", "fieldtype": "Data", "width": 180},
			{"label": _("Lines"), "fieldname": "lines", "fieldtype": "Int", "width": 70},
			{"label": _("Invoice"), "fieldname": "invoice", "fieldtype": "Data", "width": 150},
			{"label": _("Credit Note(s)"), "fieldname": "credit_notes", "fieldtype": "Data", "width": 150},
			{"label": _("Delivered Qty"), "fieldname": "delivered_qty", "fieldtype": "Float", "width": 100},
			{"label": _("Credited Qty"), "fieldname": "credited_qty", "fieldtype": "Float", "width": 100},
			{"label": _("Returned to Stock"), "fieldname": "returned_qty", "fieldtype": "Float", "width": 120},
			{"label": _("Pending Return Qty"), "fieldname": "pending_qty", "fieldtype": "Float", "width": 130},
			{"label": _("Pending Value"), "fieldname": "pending_value", "fieldtype": "Currency", "options": "currency", "width": 120},
			{"label": _("Delivery Return(s)"), "fieldname": "return_notes", "fieldtype": "Data", "width": 150},
			{"label": _("Status"), "fieldname": "status", "fieldtype": "Data", "width": 110},
		]

	if view == ITEM_WISE:
		return [
			{"label": _("Item"), "fieldname": "item_code", "fieldtype": "Link", "options": "Item", "width": 130},
			{"label": _("Item Name"), "fieldname": "item_name", "fieldtype": "Data", "width": 220},
			{"label": _("Item Group"), "fieldname": "item_group", "fieldtype": "Link", "options": "Item Group", "width": 140},
			{"label": _("Delivery Notes"), "fieldname": "delivery_notes", "fieldtype": "Int", "width": 110},
			{"label": _("Lines"), "fieldname": "lines", "fieldtype": "Int", "width": 70},
			{"label": _("Delivered Qty"), "fieldname": "delivered_qty", "fieldtype": "Float", "width": 100},
			{"label": _("Credited Qty"), "fieldname": "credited_qty", "fieldtype": "Float", "width": 100},
			{"label": _("Returned to Stock"), "fieldname": "returned_qty", "fieldtype": "Float", "width": 120},
			{"label": _("Pending Return Qty"), "fieldname": "pending_qty", "fieldtype": "Float", "width": 130},
			{"label": _("Pending Value"), "fieldname": "pending_value", "fieldtype": "Currency", "options": "currency", "width": 120},
			{"label": _("Status"), "fieldname": "status", "fieldtype": "Data", "width": 110},
		]

	return [
		{"label": _("Delivery Note"), "fieldname": "delivery_note", "fieldtype": "Link", "options": "Delivery Note", "width": 160},
		{"label": _("Date"), "fieldname": "posting_date", "fieldtype": "Date", "width": 100},
		{"label": _("Customer"), "fieldname": "customer", "fieldtype": "Link", "options": "Customer", "width": 160},
		{"label": _("Customer Name"), "fieldname": "customer_name", "fieldtype": "Data", "width": 180},
		{"label": _("Item"), "fieldname": "item_code", "fieldtype": "Link", "options": "Item", "width": 130},
		{"label": _("Item Name"), "fieldname": "item_name", "fieldtype": "Data", "width": 200},
		{"label": _("Item Group"), "fieldname": "item_group", "fieldtype": "Link", "options": "Item Group", "width": 130},
		{"label": _("Warehouse"), "fieldname": "warehouse", "fieldtype": "Link", "options": "Warehouse", "width": 120},
		{"label": _("Delivered Qty"), "fieldname": "delivered_qty", "fieldtype": "Float", "width": 100},
		{"label": _("Invoice"), "fieldname": "invoice", "fieldtype": "Link", "options": "Sales Invoice", "width": 130},
		{"label": _("Credit Note(s)"), "fieldname": "credit_notes", "fieldtype": "Data", "width": 140},
		{"label": _("Credited Qty"), "fieldname": "credited_qty", "fieldtype": "Float", "width": 100},
		{"label": _("Returned to Stock"), "fieldname": "returned_qty", "fieldtype": "Float", "width": 120},
		{"label": _("Pending Return Qty"), "fieldname": "pending_qty", "fieldtype": "Float", "width": 130},
		{"label": _("Rate"), "fieldname": "rate", "fieldtype": "Currency", "options": "currency", "width": 100},
		{"label": _("Pending Value"), "fieldname": "pending_value", "fieldtype": "Currency", "options": "currency", "width": 120},
		{"label": _("Delivery Return(s)"), "fieldname": "return_notes", "fieldtype": "Data", "width": 150},
		{"label": _("Status"), "fieldname": "status", "fieldtype": "Data", "width": 110},
	]


def get_summary(rows, currency):
	pending = [r for r in rows if r.status == PENDING]
	return [
		{
			"label": _("Delivery Notes Pending Return"),
			"value": len({r.delivery_note for r in pending}),
			"datatype": "Int",
			"indicator": "Red" if pending else "Green",
		},
		{"label": _("Lines Pending"), "value": len(pending), "datatype": "Int", "indicator": "Orange"},
		{
			"label": _("Pending Qty"),
			"value": flt(sum(r.pending_qty for r in pending), 3),
			"datatype": "Float",
			"indicator": "Orange",
		},
		{
			"label": _("Pending Value"),
			"value": flt(sum(r.pending_value for r in pending), 3),
			"datatype": "Currency",
			"currency": currency,
			"indicator": "Blue",
		},
	]
