# Copyright (c) 2026, Enfono Technologies and contributors
# For license information, please see license.txt

"""Purchase Receipts whose invoice was debited but whose goods were never returned out of stock.

The purchase mirror of Delivery Notes Pending Return. A Purchase Return raised on the Purchase
Invoice (a debit note with Update Stock off) reduces what is owed to the supplier and writes NO stock
ledger entry. When that invoice's goods came in through a Purchase Receipt, the stock only goes back
out through a return of the Purchase Receipt itself. Where the first was done and the second was
not, the books say the goods went back while the warehouse still holds them.

For each Purchase Receipt line linked to an invoice line (in either direction: the invoice made
from the receipt, `pr_detail`, or the receipt made from the invoice, `purchase_invoice_item`):

    debited   = qty of that invoice line returned on submitted debit notes that move no stock
    returned  = qty of this receipt line already returned by submitted Purchase Receipt returns
    pending   = debited not yet returned, never more than was received

Debit notes that move stock themselves (Update Stock on) are not pending: the stock went out with
them. A debit note on an invoice with no receipt behind it has no Purchase Receipt to return, so it
is not listed here.
"""

from collections import defaultdict

import frappe
from frappe import _
from frappe.utils import flt

PENDING = "Pending Return"
RETURNED = "Returned"
TOLERANCE = 0.0005

# Which User Permission narrows which column of the receipt line
PERMISSION_COLUMNS = (
	("Supplier", "supplier"),
	("Warehouse", "warehouse"),
	("Branch", "branch"),
	("Cost Center", "cost_center"),
)


def execute(filters=None):
	filters = frappe._dict(filters or {})
	frappe.has_permission("Purchase Receipt", "read", throw=True)
	if not filters.company:
		frappe.throw(_("Company is required"))

	rows = get_rows(filters)
	for r in rows:
		# SUM() comes back as a Decimal
		r.debited_qty = flt(r.debited_qty)
		r.returned_qty = flt(r.returned_qty)
	allocate_pending(rows)

	scope = get_permitted_scope()
	rows = [r for r in rows if is_permitted(r, scope)]
	if filters.get("show") != "All Debited":
		rows = [r for r in rows if r.status == PENDING]

	currency = frappe.get_cached_value("Company", filters.company, "default_currency")
	for r in rows:
		r.pending_value = flt(r.pending_qty * flt(r.rate), 3)
		r.currency = currency

	rows.sort(key=lambda r: (r.posting_date, r.purchase_receipt, r.pr_row))
	return get_columns(), rows, None, None, get_summary(rows, currency)


def get_rows(filters):
	"""One row per receipt line that has a debited invoice line behind it."""
	meta = frappe.get_meta("Purchase Receipt")
	item_meta = frappe.get_meta("Purchase Receipt Item")
	branch = "p.branch" if meta.has_field("branch") else "NULL"
	cost_center = "prr.cost_center" if item_meta.has_field("cost_center") else "NULL"

	conditions = ["p.docstatus = 1", "p.is_return = 0", "p.company = %(company)s"]
	values = {"company": filters.company}
	if filters.get("from_date"):
		conditions.append("p.posting_date >= %(from_date)s")
		values["from_date"] = filters.from_date
	if filters.get("to_date"):
		conditions.append("p.posting_date <= %(to_date)s")
		values["to_date"] = filters.to_date
	if filters.get("supplier"):
		conditions.append("p.supplier = %(supplier)s")
		values["supplier"] = filters.supplier
	if filters.get("item_code"):
		conditions.append("prr.item_code = %(item_code)s")
		values["item_code"] = filters.item_code

	return frappe.db.sql(
		f"""
		SELECT
			pair.pr_row,
			prr.parent AS purchase_receipt,
			p.posting_date,
			p.supplier,
			p.supplier_name,
			prr.item_code,
			prr.item_name,
			prr.warehouse,
			prr.qty AS received_qty,
			prr.base_net_rate AS rate,
			{branch} AS branch,
			{cost_center} AS cost_center,
			pair.pi_row,
			pair.invoice,
			pi.bill_no,
			cr.debited AS debited_qty,
			cr.debit_notes,
			COALESCE(rt.returned, 0) AS returned_qty,
			rt.return_notes
		FROM (
			SELECT prr.name AS pr_row, pir.name AS pi_row, pir.parent AS invoice
			FROM `tabPurchase Receipt Item` prr
			INNER JOIN `tabPurchase Invoice Item` pir ON pir.name = prr.purchase_invoice_item
			WHERE IFNULL(prr.purchase_invoice_item, '') != ''
			UNION
			SELECT pir.pr_detail AS pr_row, pir.name AS pi_row, pir.parent AS invoice
			FROM `tabPurchase Invoice Item` pir
			WHERE IFNULL(pir.pr_detail, '') != ''
		) pair
		INNER JOIN `tabPurchase Receipt Item` prr ON prr.name = pair.pr_row
		INNER JOIN `tabPurchase Receipt` p ON p.name = prr.parent
		INNER JOIN `tabPurchase Invoice` pi ON pi.name = pair.invoice AND pi.docstatus = 1 AND pi.is_return = 0
		INNER JOIN (
			SELECT
				ret.purchase_invoice_item AS pi_row,
				SUM(ABS(ret.qty)) AS debited,
				GROUP_CONCAT(DISTINCT ret.parent ORDER BY ret.parent SEPARATOR ', ') AS debit_notes
			FROM `tabPurchase Invoice Item` ret
			INNER JOIN `tabPurchase Invoice` rs
				ON rs.name = ret.parent AND rs.docstatus = 1 AND rs.is_return = 1 AND rs.update_stock = 0
			WHERE IFNULL(ret.purchase_invoice_item, '') != ''
			GROUP BY ret.purchase_invoice_item
		) cr ON cr.pi_row = pair.pi_row
		LEFT JOIN (
			SELECT
				rri.purchase_receipt_item AS pr_row,
				SUM(ABS(rri.qty)) AS returned,
				GROUP_CONCAT(DISTINCT rri.parent ORDER BY rri.parent SEPARATOR ', ') AS return_notes
			FROM `tabPurchase Receipt Item` rri
			INNER JOIN `tabPurchase Receipt` rr ON rr.name = rri.parent AND rr.docstatus = 1 AND rr.is_return = 1
			WHERE IFNULL(rri.purchase_receipt_item, '') != ''
			GROUP BY rri.purchase_receipt_item
		) rt ON rt.pr_row = pair.pr_row
		WHERE {" AND ".join(conditions)}
		""",
		values,
		as_dict=True,
	)


def allocate_pending(rows):
	"""Work out `pending_qty` and `status` for every row, in place.

	The debit belongs to the invoice line, so when one invoice line was received in several
	Purchase Receipts it is shared out oldest receipt first, after what those receipts have
	already had returned. Nothing is ever pending beyond what was received less what went back.
	"""
	by_invoice_line = defaultdict(list)
	for row in rows:
		by_invoice_line[row.pi_row].append(row)

	for group in by_invoice_line.values():
		group.sort(key=lambda r: (r.posting_date, r.pr_row))
		pool = flt(group[0].debited_qty) - sum(flt(r.returned_qty) for r in group)
		for row in group:
			room = flt(row.received_qty) - flt(row.returned_qty)
			take = max(0.0, min(pool, room))
			pool -= take
			row.pending_qty = flt(take, 3) if take > TOLERANCE else 0.0
			row.status = PENDING if row.pending_qty else RETURNED
	return rows


def get_permitted_scope(user=None):
	"""doctype -> the values the user may see, for the doctypes they are restricted on.

	`frappe.db.sql` applies no permission filtering, so without this a user restricted to one
	branch, warehouse or supplier set would see every receipt here while the desk list is
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


def get_columns():
	return [
		{"label": _("Purchase Receipt"), "fieldname": "purchase_receipt", "fieldtype": "Link", "options": "Purchase Receipt", "width": 170},
		{"label": _("Date"), "fieldname": "posting_date", "fieldtype": "Date", "width": 100},
		{"label": _("Supplier"), "fieldname": "supplier", "fieldtype": "Link", "options": "Supplier", "width": 160},
		{"label": _("Supplier Name"), "fieldname": "supplier_name", "fieldtype": "Data", "width": 180},
		{"label": _("Item"), "fieldname": "item_code", "fieldtype": "Link", "options": "Item", "width": 130},
		{"label": _("Item Name"), "fieldname": "item_name", "fieldtype": "Data", "width": 200},
		{"label": _("Warehouse"), "fieldname": "warehouse", "fieldtype": "Link", "options": "Warehouse", "width": 120},
		{"label": _("Received Qty"), "fieldname": "received_qty", "fieldtype": "Float", "width": 100},
		{"label": _("Invoice"), "fieldname": "invoice", "fieldtype": "Link", "options": "Purchase Invoice", "width": 150},
		{"label": _("Supplier Invoice No"), "fieldname": "bill_no", "fieldtype": "Data", "width": 140},
		{"label": _("Debit Note(s)"), "fieldname": "debit_notes", "fieldtype": "Data", "width": 150},
		{"label": _("Debited Qty"), "fieldname": "debited_qty", "fieldtype": "Float", "width": 100},
		{"label": _("Returned from Stock"), "fieldname": "returned_qty", "fieldtype": "Float", "width": 130},
		{"label": _("Pending Return Qty"), "fieldname": "pending_qty", "fieldtype": "Float", "width": 130},
		{"label": _("Rate"), "fieldname": "rate", "fieldtype": "Currency", "options": "currency", "width": 100},
		{"label": _("Pending Value"), "fieldname": "pending_value", "fieldtype": "Currency", "options": "currency", "width": 120},
		{"label": _("Receipt Return(s)"), "fieldname": "return_notes", "fieldtype": "Data", "width": 150},
		{"label": _("Status"), "fieldname": "status", "fieldtype": "Data", "width": 110},
	]


def get_summary(rows, currency):
	pending = [r for r in rows if r.status == PENDING]
	return [
		{
			"label": _("Purchase Receipts Pending Return"),
			"value": len({r.purchase_receipt for r in pending}),
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
