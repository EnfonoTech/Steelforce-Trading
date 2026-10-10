# Copyright (c) 2026, Enfono Technologies and contributors
# For license information, please see license.txt

"""Item-wise Cash Flow: how much money each item brought in and took out, and its share of the total.

Cash is collected per invoice, not per item, so each invoice's collection is spread over its lines
in proportion to their net amount -- the same split the invoice's own tax and total follow. A
collection is real money only: what a credit note took off an invoice is a return, not cash, so it
is left out of "collected" on both documents (the invoice it reduced, and the note itself).

For every document, as on the To Date (Payment Ledger, the ledger Accounts Receivable reads):

    collected  = total - outstanding - credit notes netted against it

and on the purchase side, the same with "paid". Returns appear as negative lines of their own.

Ratios on every row:

* share of total net sales / collections / net purchases / payments  -- the item against the whole
* collection ratio = collected / billed, payment ratio = paid / billed  -- the item against itself
* net cash = collected - paid, and its share of the total net cash
* sales to purchases = net sales / net purchases
"""

from collections import defaultdict

import frappe
from frappe import _
from frappe.utils import cint, flt, get_first_day, getdate, nowdate

PERMISSION_COLUMNS = (("Customer", "customer"), ("Supplier", "supplier"), ("Branch", "branch"))
BATCH = 500
GROUP_ITEM = "Item"
GROUP_ITEM_GROUP = "Item Group"

LINES_SQL = {
	"Sales Invoice": """
		select doc.name as document, doc.posting_date, doc.customer as party, doc.branch, doc.is_return,
			doc.base_net_total, doc.base_grand_total, doc.base_rounded_total, doc.disable_rounded_total,
			doc.custom_sales_person as sales_person,
			line.item_code, line.item_name, line.item_group, line.stock_qty, line.base_net_amount
		from `tabSales Invoice Item` line
		join `tabSales Invoice` doc on doc.name = line.parent
		where doc.docstatus = 1 and doc.company = %(company)s
			and doc.posting_date between %(from_date)s and %(to_date)s
			and (%(party)s = '' or doc.customer = %(party)s)
			and (%(item_code)s = '' or line.item_code = %(item_code)s)
			and (%(sales_person)s = '' or doc.custom_sales_person = %(sales_person)s)
	""",
	"Purchase Invoice": """
		select doc.name as document, doc.posting_date, doc.supplier as party, doc.branch, doc.is_return,
			doc.base_net_total, doc.base_grand_total, doc.base_rounded_total, doc.disable_rounded_total,
			null as sales_person,
			line.item_code, line.item_name, line.item_group, line.stock_qty, line.base_net_amount
		from `tabPurchase Invoice Item` line
		join `tabPurchase Invoice` doc on doc.name = line.parent
		where doc.docstatus = 1 and doc.company = %(company)s
			and doc.posting_date between %(from_date)s and %(to_date)s
			and (%(party)s = '' or doc.supplier = %(party)s)
			and (%(item_code)s = '' or line.item_code = %(item_code)s)
	""",
}

OUTSTANDING_SQL = """
	select against_voucher_no as document, sum(amount_in_account_currency) as outstanding
	from `tabPayment Ledger Entry`
	where delinked = 0 and against_voucher_type = %(doctype)s and against_voucher_no in %(documents)s
		and posting_date <= %(as_on)s
	group by against_voucher_no
"""

NETTED_SQL = """
	select voucher_no, against_voucher_no, amount_in_account_currency as amount
	from `tabPayment Ledger Entry`
	where delinked = 0 and voucher_type = %(doctype)s and against_voucher_type = %(doctype)s
		and voucher_no != against_voucher_no and posting_date <= %(as_on)s
		and (voucher_no in %(documents)s or against_voucher_no in %(documents)s)
"""


def execute(filters=None):
	filters = frappe._dict(filters or {})
	frappe.has_permission("Sales Invoice", "read", throw=True)
	if not filters.company:
		frappe.throw(_("Company is required"))
	filters.from_date = getdate(filters.from_date or get_first_day(nowdate()))
	filters.to_date = getdate(filters.to_date or nowdate())
	if filters.from_date > filters.to_date:
		frappe.throw(_("From Date cannot be after To Date"))
	currency = frappe.get_cached_value("Company", filters.company, "default_currency")
	group_by = filters.group_by or GROUP_ITEM

	sales = item_flows("Sales Invoice", filters, filters.customer)
	purchases = item_flows("Purchase Invoice", filters, filters.supplier) if cint(filters.get("include_purchases", 1)) else {}

	rows = combine(sales, purchases, group_by)
	show = filters.show or "All"
	if show == "With Sales":
		rows = [r for r in rows if r["net_sales"] or r["collected"]]
	elif show == "With Purchases":
		rows = [r for r in rows if r["net_purchases"] or r["paid"]]
	add_ratios(rows)
	rows.sort(key=lambda r: (-abs(r["collected"]), -abs(r["net_sales"]), r["key"] or ""))
	for r in rows:
		r["currency"] = currency
	return columns(group_by), rows, None, chart(rows), summary(rows, currency)


# ── per document ────────────────────────────────────────────────────────────────────────────────


def document_total(doc) -> float:
	"""The payable face value: rounded_total only when rounding is on and it is set."""
	if cint(doc.disable_rounded_total) or not flt(doc.base_rounded_total):
		return flt(doc.base_grand_total, 3)
	return flt(doc.base_rounded_total, 3)


def item_groups(item_group) -> set:
	lft, rgt = frappe.db.get_value("Item Group", item_group, ["lft", "rgt"]) or (0, 0)
	return set(frappe.get_all("Item Group", filters={"lft": [">=", lft], "rgt": ["<=", rgt]}, pluck="name"))


def get_permitted_scope():
	from frappe.permissions import get_user_permissions

	permissions = get_user_permissions(frappe.session.user) or {}
	scope = {}
	for doctype, _fieldname in PERMISSION_COLUMNS:
		allowed = {row.get("doc") for row in (permissions.get(doctype) or []) if row.get("doc")}
		if allowed:
			scope[doctype] = allowed
	return scope


def visible(line, doctype, scope, branches, groups) -> bool:
	if branches and line.branch not in branches:
		return False
	if groups is not None and line.item_group not in groups:
		return False
	party_doctype = "Customer" if doctype == "Sales Invoice" else "Supplier"
	for scoped, value in ((party_doctype, line.party), ("Branch", line.branch)):
		allowed = scope.get(scoped)
		if allowed and value and value not in allowed:
			return False
	return True


def settlement(doctype, documents, as_on) -> dict:
	"""document -> money actually settled on it as on `as_on` (collected or paid).

	total - outstanding - what credit/debit notes netted against it; a note that netted itself
	against another invoice settled nothing in money either (see the module docstring).
	"""
	outstanding, netted = defaultdict(float), defaultdict(float)
	names = list(documents)
	for start in range(0, len(names), BATCH):
		chunk = tuple(names[start : start + BATCH])
		params = {"doctype": doctype, "documents": chunk, "as_on": as_on}
		for r in frappe.db.sql(OUTSTANDING_SQL, params, as_dict=True):
			outstanding[r.document] = flt(r.outstanding)
		for r in frappe.db.sql(NETTED_SQL, params, as_dict=True):
			amount = flt(r.amount)
			# the note's own row against another document: it moved -amount onto that document
			netted[r.against_voucher_no] -= amount
			netted[r.voucher_no] += amount
	return {name: flt(doc.total - outstanding.get(name, 0.0) - netted.get(name, 0.0), 3) for name, doc in documents.items()}


def item_flows(doctype, filters, party) -> dict:
	"""(item_code) -> qty, net amount, billed (incl. tax share), settled money, for one side."""
	params = {
		"company": filters.company, "from_date": filters.from_date, "to_date": filters.to_date,
		"party": party or "", "item_code": filters.item_code or "", "sales_person": filters.sales_person or "",
	}
	lines = frappe.db.sql(LINES_SQL[doctype], params, as_dict=True)
	scope = get_permitted_scope()
	branches = set(filters.get("branch") or [])
	groups = item_groups(filters.item_group) if filters.get("item_group") else None
	lines = [line for line in lines if visible(line, doctype, scope, branches, groups)]
	if not lines:
		return {}

	documents = {}
	for line in lines:
		doc = documents.setdefault(line.document, frappe._dict(total=document_total(line),
			net_total=flt(line.base_net_total), lines=0))
		doc.lines += 1
	settled = settlement(doctype, documents, filters.to_date)

	flows = defaultdict(lambda: frappe._dict(qty=0.0, net=0.0, billed=0.0, settled=0.0, documents=set()))
	for line in lines:
		doc = documents[line.document]
		share = flt(line.base_net_amount) / doc.net_total if doc.net_total else 1.0 / doc.lines
		f = flows[line.item_code]
		f.item_name = line.item_name
		f.item_group = line.item_group
		f.qty += flt(line.stock_qty)
		f.net += flt(line.base_net_amount)
		f.billed += doc.total * share
		f.settled += settled.get(line.document, 0.0) * share
		f.documents.add(line.document)
	return flows


# ── rows ────────────────────────────────────────────────────────────────────────────────────────


def combine(sales, purchases, group_by) -> list:
	rows = {}
	for side, flows in (("sales", sales), ("purchases", purchases)):
		for item_code, f in flows.items():
			key = f.item_group if group_by == GROUP_ITEM_GROUP else item_code
			row = rows.setdefault(key, {
				"key": key, "item_code": None if group_by == GROUP_ITEM_GROUP else item_code,
				"item_name": None if group_by == GROUP_ITEM_GROUP else f.item_name, "item_group": f.item_group,
				"qty_sold": 0.0, "net_sales": 0.0, "billed_sales": 0.0, "collected": 0.0, "invoices": set(),
				"qty_purchased": 0.0, "net_purchases": 0.0, "billed_purchases": 0.0, "paid": 0.0, "bills": set(),
			})
			if side == "sales":
				row["qty_sold"] += f.qty
				row["net_sales"] += f.net
				row["billed_sales"] += f.billed
				row["collected"] += f.settled
				row["invoices"] |= f.documents
			else:
				row["qty_purchased"] += f.qty
				row["net_purchases"] += f.net
				row["billed_purchases"] += f.billed
				row["paid"] += f.settled
				row["bills"] |= f.documents
	out = []
	for row in rows.values():
		row["invoice_count"] = len(row.pop("invoices"))
		row["bill_count"] = len(row.pop("bills"))
		for key in ("qty_sold", "net_sales", "billed_sales", "collected", "qty_purchased", "net_purchases",
			"billed_purchases", "paid"):
			row[key] = flt(row[key], 3)
		row["to_collect"] = flt(row["billed_sales"] - row["collected"], 3)
		row["to_pay"] = flt(row["billed_purchases"] - row["paid"], 3)
		row["net_cash"] = flt(row["collected"] - row["paid"], 3)
		out.append(row)
	return out


def pct(part, whole):
	return flt(part * 100.0 / whole, 2) if whole else None


def add_ratios(rows):
	totals = {key: sum(r[key] for r in rows) for key in ("net_sales", "collected", "net_purchases", "paid", "net_cash")}
	for r in rows:
		r["sales_share"] = pct(r["net_sales"], totals["net_sales"])
		r["collected_share"] = pct(r["collected"], totals["collected"])
		r["purchase_share"] = pct(r["net_purchases"], totals["net_purchases"])
		r["paid_share"] = pct(r["paid"], totals["paid"])
		r["net_cash_share"] = pct(r["net_cash"], totals["net_cash"])
		r["collection_ratio"] = pct(r["collected"], r["billed_sales"])
		r["payment_ratio"] = pct(r["paid"], r["billed_purchases"])
		r["sales_to_purchases"] = pct(r["net_sales"], r["net_purchases"])


def money(fieldname, label, width=115):
	return {"fieldname": fieldname, "label": label, "fieldtype": "Currency", "options": "currency", "width": width}


def percent(fieldname, label, width=85):
	return {"fieldname": fieldname, "label": label, "fieldtype": "Percent", "width": width}


def columns(group_by):
	head = [{"fieldname": "item_group", "label": _("Item Group"), "fieldtype": "Link", "options": "Item Group", "width": 140}]
	if group_by != GROUP_ITEM_GROUP:
		head = [
			{"fieldname": "item_code", "label": _("Item"), "fieldtype": "Link", "options": "Item", "width": 130},
			{"fieldname": "item_name", "label": _("Item Name"), "fieldtype": "Data", "width": 200},
		] + head
	return head + [
		{"fieldname": "qty_sold", "label": _("Qty Sold"), "fieldtype": "Float", "width": 85},
		money("net_sales", _("Net Sales")),
		percent("sales_share", _("% of Sales")),
		money("billed_sales", _("Billed (incl. VAT)")),
		money("collected", _("Collected")),
		percent("collected_share", _("% of Collections")),
		percent("collection_ratio", _("Collection Ratio")),
		money("to_collect", _("To Collect")),
		{"fieldname": "qty_purchased", "label": _("Qty Purchased"), "fieldtype": "Float", "width": 95},
		money("net_purchases", _("Net Purchases")),
		percent("purchase_share", _("% of Purchases")),
		money("paid", _("Paid")),
		percent("paid_share", _("% of Payments")),
		percent("payment_ratio", _("Payment Ratio")),
		money("to_pay", _("To Pay")),
		money("net_cash", _("Net Cash (Collected - Paid)"), 140),
		percent("net_cash_share", _("% of Net Cash")),
		percent("sales_to_purchases", _("Sales / Purchases")),
		{"fieldname": "invoice_count", "label": _("Invoices"), "fieldtype": "Int", "width": 75},
		{"fieldname": "bill_count", "label": _("Bills"), "fieldtype": "Int", "width": 65},
		{"fieldname": "currency", "label": _("Currency"), "fieldtype": "Link", "options": "Currency", "hidden": 1},
	]


def summary(rows, currency):
	def card(label, value, indicator):
		return {"label": label, "value": flt(value, 3), "datatype": "Currency", "currency": currency, "indicator": indicator}

	sales = sum(r["billed_sales"] for r in rows)
	collected = sum(r["collected"] for r in rows)
	paid = sum(r["paid"] for r in rows)
	return [
		card(_("Net Sales"), sum(r["net_sales"] for r in rows), "Blue"),
		card(_("Collected"), collected, "Green"),
		{"label": _("Collection Ratio"), "value": pct(collected, sales) or 0, "datatype": "Percent", "indicator": "Green"},
		card(_("Paid"), paid, "Red"),
		card(_("Net Cash"), collected - paid, "Green" if collected >= paid else "Red"),
	]


def chart(rows):
	top = rows[:10]
	if not top:
		return None
	return {
		"data": {
			"labels": [r["item_code"] or r["item_group"] for r in top],
			"datasets": [
				{"name": _("Collected"), "values": [r["collected"] for r in top]},
				{"name": _("Paid"), "values": [r["paid"] for r in top]},
			],
		},
		"type": "bar",
		"colors": ["#2e7d32", "#c62828"],
		"fieldtype": "Currency",
	}
