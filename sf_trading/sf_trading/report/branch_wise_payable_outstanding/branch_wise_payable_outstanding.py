# Copyright (c) 2026, Enfono Technologies and contributors
# For license information, please see license.txt

"""What the company owes each supplier, branch by branch -- the payable twin of Branch-wise Credit Outstanding.

Outstanding is read from the Payment Ledger -- the ledger ERPNext's own Accounts Payable reads -- as on
the chosen date, one figure per open document. A document's branch is the branch of the document itself,
not of the payment that settled part of it: a payment made at SFSS against an SFSB purchase invoice
reduces SFSB's payable. Money paid but not yet allocated to an invoice (an advance, a standalone debit
note) counts at the branch that paid it, as Unallocated Advance / Debit.

Open Orders is what a credit check would add to the picture: the unbilled amount of every open Purchase
Order, i.e. what the company has committed to buy and will owe once the supplier invoices it. Exposure is
Outstanding plus Open Orders.

A supplier's own terms (payment terms, group, country) come from the Supplier. Suppliers have no credit
limit in ERPNext, so there is nothing to measure against; the report is about how much is owed, how late,
and what is already committed.
"""

from collections import defaultdict

import frappe
from frappe import _
from frappe.utils import cint, date_diff, flt, getdate, today

VIEW_TREE = "Supplier and Branch"
VIEW_BRANCH = "Branch Summary"
VIEW_DOCUMENT = "Document Detail"

STATUS_OVERDUE = "Overdue"
STATUS_NOT_DUE = "Not Yet Due"
STATUS_ADVANCE = "Advance Held"
STATUS_SETTLED = "Settled"

NO_BRANCH = "(No Branch)"
TOLERANCE = 0.0005
PERMISSION_COLUMNS = (("Supplier", "supplier"), ("Branch", "branch"))
AMOUNT_KEYS = ("outstanding", "invoiced", "unallocated", "overdue", "orders", "not_due", "open_documents")


def execute(filters=None):
	filters = frappe._dict(filters or {})
	frappe.has_permission("Purchase Invoice", "read", throw=True)
	if not filters.company:
		frappe.throw(_("Company is required"))

	as_on = getdate(filters.as_on_date or today())
	ranges = parse_ranges(filters.range)
	currency = frappe.get_cached_value("Company", filters.company, "default_currency")
	home_country = frappe.get_cached_value("Company", filters.company, "country")

	suppliers_info = supplier_info(filters, home_country)
	documents = open_documents(filters, as_on)
	orders = open_orders(filters, as_on)

	scope = get_permitted_scope()
	branches = set(filters.get("branch") or [])

	def keep(row):
		return row.supplier in suppliers_info and is_permitted(row, scope) and (not branches or row.branch in branches)

	documents = [d for d in documents if keep(d)]
	orders = {k: v for k, v in orders.items() if keep(frappe._dict(supplier=k[0], branch=k[1]))}

	for doc in documents:
		age_doc(doc, as_on, ranges, filters.ageing_based_on)

	suppliers = build_suppliers(documents, orders, suppliers_info, filters)
	suppliers = [s for s in suppliers if wanted(s, filters)]
	kept = {s.supplier for s in suppliers}
	documents = [d for d in documents if d.supplier in kept]

	summary = get_summary(suppliers, currency)
	view = filters.view or VIEW_TREE
	if view == VIEW_DOCUMENT:
		return document_columns(), document_rows(documents, suppliers, currency), None, None, summary
	if view == VIEW_BRANCH:
		return branch_columns(ranges), branch_rows(suppliers, currency), None, branch_chart(suppliers), summary
	return tree_columns(ranges), tree_rows(suppliers, currency), None, None, summary


# ── ageing ──────────────────────────────────────────────────────────────────────────────────────


def parse_ranges(text) -> list:
	"""'30, 60, 90, 120' -> [30, 60, 90, 120]; anything unreadable falls back to that default.
	Up to five ranges, so at most six buckets."""
	values = []
	for part in (text or "30, 60, 90, 120").split(","):
		number = cint(part.strip())
		if number > 0 and (not values or number > values[-1]):
			values.append(number)
	return (values or [30, 60, 90, 120])[:5]


def bucket_labels(ranges) -> list:
	labels, low = [], 0
	for high in ranges:
		labels.append("%s-%s" % (low, high))
		low = high + 1
	labels.append("%s+" % low)
	return labels


def age_doc(doc, as_on, ranges, based_on):
	"""Days past the reference date and the bucket the outstanding falls in.

	A debit (a negative payable) is not aged: it is shown as Unallocated Advance / Debit, so the buckets
	add up to what is owed on invoices. On Due Date ageing, a document not yet due is Not Due.
	"""
	by_posting = based_on == "Posting Date"
	reference = doc.posting_date if by_posting else (doc.due_date or doc.posting_date)
	doc.age = date_diff(as_on, reference) if reference else 0
	doc.bucket = None
	if doc.outstanding <= 0:
		return
	if not by_posting and doc.age < 0:
		doc.bucket = "not_due"
		return
	for index, high in enumerate(ranges):
		if doc.age <= high:
			doc.bucket = "range%s" % (index + 1)
			return
	doc.bucket = "range%s" % (len(ranges) + 1)


# ── data ────────────────────────────────────────────────────────────────────────────────────────


def supplier_info(filters, home_country) -> dict:
	"""supplier -> master data, for the suppliers the filters allow."""
	conditions = {}
	if filters.supplier:
		conditions["name"] = filters.supplier
	if filters.supplier_group:
		conditions["supplier_group"] = filters.supplier_group
	if cint(filters.credit_suppliers_only):
		conditions["payment_terms"] = ["!=", ""]
	rows = frappe.get_all(
		"Supplier",
		filters=conditions,
		fields=["name", "supplier_name", "supplier_group", "payment_terms", "country", "disabled"],
		limit_page_length=0,
	)
	origin = filters.supplier_origin or "All"
	info = {}
	for r in rows:
		local = (r.country or home_country) == home_country
		if origin == "Local" and not local:
			continue
		if origin == "Import" and local:
			continue
		r.origin = _("Local") if local else _("Import")
		info[r.name] = r
	return info


OPEN_DOCUMENTS_SQL = """
	select ple.party as supplier, ple.against_voucher_type as voucher_type, ple.against_voucher_no as voucher_no,
		sum(ple.amount) as outstanding,
		max(case when ple.voucher_no = ple.against_voucher_no then ple.branch end) as branch,
		max(case when ple.voucher_no = ple.against_voucher_no then ple.posting_date end) as posting_date,
		max(case when ple.voucher_no = ple.against_voucher_no then ple.due_date end) as due_date
	from `tabPayment Ledger Entry` ple
	where ple.company = %(company)s
		and ple.party_type = 'Supplier'
		and ple.account_type = 'Payable'
		and ple.delinked = 0
		and ple.posting_date <= %(as_on)s
		and (%(supplier)s = '' or ple.party = %(supplier)s)
	group by ple.party, ple.against_voucher_type, ple.against_voucher_no
	having abs(sum(ple.amount)) >= %(tolerance)s
"""


def open_documents(filters, as_on) -> list:
	"""One row per document with a balance on the Payment Ledger as on `as_on`.

	Grouped on the document the balance belongs to (against_voucher). Its own ledger row -- the one where
	voucher and against_voucher are the same document -- gives the branch, posting date and due date;
	payments allocated to it carry the paying document's own branch, which is ignored. A purchase invoice
	says its own branch, supplier bill number and bill date on the invoice itself, which is more reliable
	than the ledger row (a third of the ledger rows carry no branch).
	"""
	rows = frappe.db.sql(
		OPEN_DOCUMENTS_SQL,
		{"company": filters.company, "as_on": as_on, "supplier": filters.supplier or "", "tolerance": TOLERANCE},
		as_dict=True,
	)

	invoice_info = {}
	invoices = [r.voucher_no for r in rows if r.voucher_type == "Purchase Invoice"]
	if invoices:
		from sf_trading.query import fetch_in_map

		invoice_info = fetch_in_map(
			"Purchase Invoice", invoices, ["name", "branch", "posting_date", "due_date", "bill_no", "bill_date"]
		)
	for row in rows:
		row.outstanding = flt(row.outstanding, 3)
		info = invoice_info.get(row.voucher_no) if row.voucher_type == "Purchase Invoice" else None
		if info:
			row.branch = info.branch or row.branch
			row.posting_date = row.posting_date or info.posting_date
			row.due_date = row.due_date or info.due_date
		row.bill_no = info.bill_no if info else None
		row.bill_date = info.bill_date if info else None
		row.branch = row.branch or NO_BRANCH
	return rows


OPEN_ORDERS_SQL = """
	select po.supplier, ifnull(po.branch, '') as branch,
		sum(po.base_grand_total * (100 - po.per_billed) / 100) as open_amount
	from `tabPurchase Order` po
	where po.company = %(company)s and po.docstatus = 1 and po.per_billed < 100
		and po.status not in ('Closed', 'Completed', 'Cancelled', 'On Hold') and po.transaction_date <= %(as_on)s
		and (%(supplier)s = '' or po.supplier = %(supplier)s)
	group by po.supplier, po.branch
"""


def open_orders(filters, as_on) -> dict:
	"""(supplier, branch) -> unbilled amount of open Purchase Orders: what is committed and not yet owed."""
	rows = frappe.db.sql(
		OPEN_ORDERS_SQL,
		{"company": filters.company, "as_on": as_on, "supplier": filters.supplier or ""},
		as_dict=True,
	)
	return {(r.supplier, r.branch or NO_BRANCH): flt(r.open_amount, 3) for r in rows if flt(r.open_amount) > 0}


def get_permitted_scope(user=None):
	"""doctype -> values the user may see, for the doctypes they are restricted on. Raw SQL applies
	no permission filtering, so a branch user would otherwise see every branch."""
	from frappe.permissions import get_user_permissions

	permissions = get_user_permissions(user or frappe.session.user) or {}
	scope = {}
	for doctype, _fieldname in PERMISSION_COLUMNS:
		allowed = {row.get("doc") for row in (permissions.get(doctype) or []) if row.get("doc")}
		if allowed:
			scope[doctype] = allowed
	return scope


def is_permitted(row, scope):
	for doctype, fieldname in PERMISSION_COLUMNS:
		allowed = scope.get(doctype)
		value = row.get(fieldname)
		if allowed and value and value != NO_BRANCH and value not in allowed:
			return False
	return True


# ── assembly ────────────────────────────────────────────────────────────────────────────────────


def blank_amounts():
	amounts = frappe._dict({key: 0.0 for key in AMOUNT_KEYS})
	amounts.open_documents = 0
	amounts.oldest_due = None
	for i in range(1, 7):
		amounts["range%s" % i] = 0.0
	return amounts


def add_document(target, doc):
	target.outstanding += doc.outstanding
	if doc.outstanding <= 0:
		target.unallocated += doc.outstanding
		return
	target.invoiced += doc.outstanding
	target.open_documents += 1
	if doc.bucket:
		target[doc.bucket] += doc.outstanding
	if doc.due_date and doc.bucket != "not_due" and doc.age > 0:
		target.overdue += doc.outstanding
	due = doc.due_date or doc.posting_date
	if due and (not target.oldest_due or getdate(due) < getdate(target.oldest_due)):
		target.oldest_due = due


def roll_up(target, source):
	for key in AMOUNT_KEYS:
		target[key] += source[key]
	for i in range(1, 7):
		target["range%s" % i] += source["range%s" % i]
	if source.oldest_due and (not target.oldest_due or getdate(source.oldest_due) < getdate(target.oldest_due)):
		target.oldest_due = source.oldest_due


def status_of(amounts) -> str:
	if amounts.overdue > TOLERANCE:
		return STATUS_OVERDUE
	if amounts.invoiced > TOLERANCE:
		return STATUS_NOT_DUE
	if amounts.unallocated < -TOLERANCE:
		return STATUS_ADVANCE
	return STATUS_SETTLED


def build_suppliers(documents, orders, suppliers_info, filters) -> list:
	per_branch = defaultdict(blank_amounts)
	for doc in documents:
		add_document(per_branch[(doc.supplier, doc.branch)], doc)
	for key, amount in orders.items():
		per_branch[key].orders += amount

	names = {supplier for supplier, _branch in per_branch}
	if cint(filters.show_zero):
		names |= set(suppliers_info) if not filters.supplier else ({filters.supplier} & set(suppliers_info))
	names &= set(suppliers_info)
	if not names:
		return []

	branches_of = defaultdict(list)
	for (supplier, branch), amounts in per_branch.items():
		amounts.branch = branch
		amounts.exposure = flt(amounts.outstanding + amounts.orders, 3)
		amounts.status = status_of(amounts)
		branches_of[supplier].append(amounts)

	suppliers = []
	for name in names:
		row = suppliers_info[name]
		total = blank_amounts()
		branch_list = sorted(branches_of.get(name, []), key=lambda b: b.branch)
		for b in branch_list:
			roll_up(total, b)
		total.update(
			supplier=name, supplier_name=row.supplier_name or name, supplier_group=row.supplier_group,
			payment_terms=row.payment_terms, origin=row.origin, country=row.country, disabled=cint(row.disabled),
			branches=branch_list,
		)
		total.exposure = flt(total.outstanding + total.orders, 3)
		total.status = status_of(total)
		suppliers.append(total)
	suppliers.sort(key=lambda s: (-s.exposure, (s.supplier_name or "").lower()))
	return suppliers


def wanted(supplier, filters) -> bool:
	status = filters.status or "All"
	if status != "All" and supplier.status != status:
		return False
	minimum = flt(filters.min_outstanding)
	if minimum and supplier.invoiced < minimum:
		return False
	if not cint(filters.show_zero) and abs(supplier.exposure) < TOLERANCE and not supplier.unallocated:
		return False
	return True


# ── rows ────────────────────────────────────────────────────────────────────────────────────────


def amount_fields(source, currency) -> dict:
	fields = {key: flt(source[key], 3) for key in AMOUNT_KEYS if key not in ("open_documents", "invoiced")}
	for i in range(1, 7):
		fields["range%s" % i] = flt(source["range%s" % i], 3)
	fields.update(
		exposure=flt(source.exposure, 3), open_documents=source.open_documents, oldest_due=source.oldest_due,
		currency=currency,
	)
	return fields


def tree_rows(suppliers, currency) -> list:
	rows = []
	for s in suppliers:
		rows.append(
			dict(
				amount_fields(s, currency), name=s.supplier, parent=None, indent=0, label=s.supplier_name,
				supplier=s.supplier, supplier_group=s.supplier_group, branch=None, payment_terms=s.payment_terms,
				origin=s.origin, status=s.status,
			)
		)
		for b in s.branches:
			rows.append(
				dict(
					amount_fields(b, currency), name="%s::%s" % (s.supplier, b.branch), parent=s.supplier, indent=1,
					label=b.branch, supplier=s.supplier, supplier_group=s.supplier_group, branch=b.branch,
					status=b.status,
				)
			)
	return rows


def branch_rows(suppliers, currency) -> list:
	totals = defaultdict(blank_amounts)
	counts = defaultdict(lambda: frappe._dict(suppliers=set(), overdue=set()))
	for s in suppliers:
		for b in s.branches:
			roll_up(totals[b.branch], b)
			totals[b.branch].exposure = totals[b.branch].get("exposure", 0) + b.exposure
			k = counts[b.branch]
			k.suppliers.add(s.supplier)
			if b.overdue > TOLERANCE:
				k.overdue.add(s.supplier)
	rows = []
	for branch in sorted(totals):
		t, k = totals[branch], counts[branch]
		rows.append(
			dict(amount_fields(t, currency), branch=branch, suppliers=len(k.suppliers), overdue_suppliers=len(k.overdue))
		)
	return rows


def document_rows(documents, suppliers, currency) -> list:
	names = {s.supplier: s.supplier_name for s in suppliers}
	rows = []
	for d in sorted(documents, key=lambda d: (names.get(d.supplier) or d.supplier, d.branch, str(d.posting_date or ""))):
		if d.outstanding <= 0:
			ageing = _("Advance / Debit")
		elif d.bucket == "not_due":
			ageing = _("Not Due")
		else:
			ageing = _("Overdue") if (d.due_date and d.age > 0) else _("Due")
		rows.append(
			{
				"supplier": d.supplier, "supplier_name": names.get(d.supplier), "branch": d.branch,
				"voucher_type": d.voucher_type, "voucher_no": d.voucher_no, "bill_no": d.bill_no,
				"bill_date": d.bill_date, "posting_date": d.posting_date, "due_date": d.due_date, "age": d.age,
				"ageing": ageing, "outstanding": d.outstanding, "currency": currency,
			}
		)
	return rows


# ── columns ─────────────────────────────────────────────────────────────────────────────────────


def money(fieldname, label, width=115):
	return {"fieldname": fieldname, "label": label, "fieldtype": "Currency", "options": "currency", "width": width}


def bucket_columns(ranges):
	cols = [money("not_due", _("Not Due"), 105)]
	for index, label in enumerate(bucket_labels(ranges)):
		cols.append(money("range%s" % (index + 1), label + " " + _("Days"), 105))
	return cols


CURRENCY_COLUMN = {"fieldname": "currency", "label": _("Currency"), "fieldtype": "Link", "options": "Currency", "hidden": 1}


def tree_columns(ranges):
	return [
		{"fieldname": "label", "label": _("Supplier / Branch"), "fieldtype": "Data", "width": 240},
		{"fieldname": "supplier", "label": _("Supplier"), "fieldtype": "Link", "options": "Supplier", "width": 120},
		{"fieldname": "branch", "label": _("Branch"), "fieldtype": "Link", "options": "Branch", "width": 80},
		{"fieldname": "status", "label": _("Status"), "fieldtype": "Data", "width": 105},
		money("outstanding", _("Outstanding")),
		money("overdue", _("Overdue")),
		*bucket_columns(ranges),
		money("unallocated", _("Unallocated Advance / Debit")),
		money("orders", _("Open Orders (Unbilled)")),
		money("exposure", _("Exposure")),
		{"fieldname": "open_documents", "label": _("Open Invoices"), "fieldtype": "Int", "width": 90},
		{"fieldname": "oldest_due", "label": _("Oldest Due"), "fieldtype": "Date", "width": 95},
		{"fieldname": "payment_terms", "label": _("Payment Terms"), "fieldtype": "Link", "options": "Payment Terms Template", "width": 120},
		{"fieldname": "origin", "label": _("Local / Import"), "fieldtype": "Data", "width": 90},
		{"fieldname": "supplier_group", "label": _("Supplier Group"), "fieldtype": "Link", "options": "Supplier Group", "width": 120},
		CURRENCY_COLUMN,
	]


def branch_columns(ranges):
	return [
		{"fieldname": "branch", "label": _("Branch"), "fieldtype": "Link", "options": "Branch", "width": 110},
		{"fieldname": "suppliers", "label": _("Suppliers"), "fieldtype": "Int", "width": 90},
		money("outstanding", _("Outstanding")),
		money("overdue", _("Overdue")),
		*bucket_columns(ranges),
		money("unallocated", _("Unallocated Advance / Debit")),
		money("orders", _("Open Orders (Unbilled)")),
		money("exposure", _("Exposure")),
		{"fieldname": "overdue_suppliers", "label": _("Suppliers Overdue"), "fieldtype": "Int", "width": 120},
		{"fieldname": "open_documents", "label": _("Open Invoices"), "fieldtype": "Int", "width": 90},
		CURRENCY_COLUMN,
	]


def document_columns():
	return [
		{"fieldname": "supplier", "label": _("Supplier"), "fieldtype": "Link", "options": "Supplier", "width": 120},
		{"fieldname": "supplier_name", "label": _("Supplier Name"), "fieldtype": "Data", "width": 190},
		{"fieldname": "branch", "label": _("Branch"), "fieldtype": "Link", "options": "Branch", "width": 80},
		{"fieldname": "voucher_type", "label": _("Voucher Type"), "fieldtype": "Data", "width": 110},
		{"fieldname": "voucher_no", "label": _("Voucher"), "fieldtype": "Dynamic Link", "options": "voucher_type", "width": 150},
		{"fieldname": "bill_no", "label": _("Supplier Invoice No"), "fieldtype": "Data", "width": 130},
		{"fieldname": "bill_date", "label": _("Bill Date"), "fieldtype": "Date", "width": 95},
		{"fieldname": "posting_date", "label": _("Posting Date"), "fieldtype": "Date", "width": 95},
		{"fieldname": "due_date", "label": _("Due Date"), "fieldtype": "Date", "width": 95},
		{"fieldname": "age", "label": _("Age (Days)"), "fieldtype": "Int", "width": 85},
		{"fieldname": "ageing", "label": _("Ageing"), "fieldtype": "Data", "width": 105},
		money("outstanding", _("Outstanding")),
		CURRENCY_COLUMN,
	]


# ── cards / chart ───────────────────────────────────────────────────────────────────────────────


def get_summary(suppliers, currency):
	def card(label, value, indicator):
		return {"label": label, "value": flt(value, 3), "datatype": "Currency", "currency": currency, "indicator": indicator}

	overdue = sum(1 for s in suppliers if s.overdue > TOLERANCE)
	return [
		{"label": _("Suppliers"), "value": len(suppliers), "datatype": "Int", "indicator": "Blue"},
		card(_("Outstanding"), sum(s.outstanding for s in suppliers), "Blue"),
		card(_("Overdue"), sum(s.overdue for s in suppliers), "Red"),
		card(_("Unallocated Advance / Debit"), sum(s.unallocated for s in suppliers), "Orange"),
		card(_("Open Orders (Unbilled)"), sum(s.orders for s in suppliers), "Orange"),
		card(_("Exposure"), sum(s.exposure for s in suppliers), "Blue"),
		{"label": _("Suppliers Overdue"), "value": overdue, "datatype": "Int", "indicator": "Red" if overdue else "Green"},
	]


def branch_chart(suppliers):
	totals = defaultdict(lambda: [0.0, 0.0])
	for s in suppliers:
		for b in s.branches:
			totals[b.branch][0] += b.outstanding
			totals[b.branch][1] += b.overdue
	labels = sorted(totals)
	if not labels:
		return None
	return {
		"data": {
			"labels": labels,
			"datasets": [
				{"name": _("Outstanding"), "values": [flt(totals[b][0], 3) for b in labels]},
				{"name": _("Overdue"), "values": [flt(totals[b][1], 3) for b in labels]},
			],
		},
		"type": "bar",
		"fieldtype": "Currency",
	}
