# Copyright (c) 2026, Enfono Technologies and contributors
# For license information, please see license.txt

"""What each credit customer owes, branch by branch, against the limits that govern them.

Outstanding is read from the Payment Ledger -- the ledger ERPNext's own Accounts Receivable reads --
as on the chosen date, one figure per open document. A document's branch is the branch of the
document itself, not of the payment that settled part of it: a receipt taken at SFSS against an SFSB
invoice reduces SFSB's outstanding, which is what SFSB's credit limit is about. Money received but
not yet allocated to an invoice (an advance, a standalone credit note) counts at the branch that
received it, as Unallocated Credit.

Exposure is what the credit checks on Sales Order / Sales Invoice count (sf_trading.credit_limit):
the outstanding plus every open Sales Order's unbilled amount. A customer's company-wide Credit
Limit is set on the Customer; a branch's own sub-limit, when it has one, on the Customer's Branch
Access row.
"""

from collections import defaultdict

import frappe
from frappe import _
from frappe.utils import cint, date_diff, flt, getdate, today

VIEW_TREE = "Customer and Branch"
VIEW_BRANCH = "Branch Summary"
VIEW_DOCUMENT = "Document Detail"

STATUS_OVER = "Over Limit"
STATUS_OVERDUE = "Overdue"
STATUS_WITHIN = "Within Limit"
STATUS_NO_LIMIT = "No Limit"

NO_BRANCH = "(No Branch)"
TOLERANCE = 0.0005
PERMISSION_COLUMNS = (("Customer", "customer"), ("Branch", "branch"))
AMOUNT_KEYS = ("outstanding", "invoiced", "unallocated", "overdue", "unbilled", "not_due", "open_documents")


def execute(filters=None):
	filters = frappe._dict(filters or {})
	frappe.has_permission("Sales Invoice", "read", throw=True)
	if not filters.company:
		frappe.throw(_("Company is required"))

	as_on = getdate(filters.as_on_date or today())
	ranges = parse_ranges(filters.range)
	currency = frappe.get_cached_value("Company", filters.company, "default_currency")

	limits = credit_limits(filters.company)
	documents = open_documents(filters, as_on)
	orders = unbilled_orders(filters, as_on)

	scope = get_permitted_scope()
	branches = set(filters.get("branch") or [])

	def keep(row):
		return (
			is_permitted(row, scope)
			and (not branches or row.branch in branches)
			and (not cint(filters.credit_customers_only) or row.customer in limits)
		)

	documents = [d for d in documents if keep(d)]
	orders = {k: v for k, v in orders.items() if keep(frappe._dict(customer=k[0], branch=k[1]))}

	if filters.sales_person:
		# a sales person owns invoices, not orders or advances
		documents = [d for d in documents if d.sales_person == filters.sales_person]
		orders = {}

	for doc in documents:
		age_doc(doc, as_on, ranges, filters.ageing_based_on)

	customers = build_customers(documents, orders, limits, filters)
	customers = [c for c in customers if wanted(c, filters)]
	kept = {c.customer for c in customers}
	documents = [d for d in documents if d.customer in kept]

	summary = get_summary(customers, currency)
	view = filters.view or VIEW_TREE
	if view == VIEW_DOCUMENT:
		return document_columns(), document_rows(documents, currency), None, None, summary
	if view == VIEW_BRANCH:
		return branch_columns(ranges), branch_rows(customers, currency), None, branch_chart(customers), summary
	return tree_columns(ranges), tree_rows(customers, currency), None, None, summary


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

	A credit (a negative balance) is not aged: it is shown as Unallocated Credit, so the buckets add
	up to what is owed on invoices. On Due Date ageing, a document not yet due is Not Due.
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


def credit_limits(company) -> dict:
	"""customer -> {credit_limit, credit_days, bypass}, for customers with a limit at this company."""
	fields = ["parent", "credit_limit", "bypass_credit_limit_check"]
	if frappe.db.has_column("Customer Credit Limit", "custom_credit_days"):
		fields.append("custom_credit_days")
	rows = frappe.get_all(
		"Customer Credit Limit",
		filters={"parenttype": "Customer", "company": company, "credit_limit": [">", 0]},
		fields=fields,
	)
	return {
		r.parent: frappe._dict(
			credit_limit=flt(r.credit_limit),
			credit_days=cint(r.get("custom_credit_days")),
			bypass=cint(r.bypass_credit_limit_check),
		)
		for r in rows
	}


def branch_limits() -> dict:
	"""(customer, branch) -> the branch's own credit sub-limit, where one is set."""
	return {
		(r.parent, r.branch): flt(r.credit_limit)
		for r in frappe.get_all(
			"Customer Branch Access",
			filters={"parenttype": "Customer", "credit_limit": [">", 0]},
			fields=["parent", "branch", "credit_limit"],
		)
	}


OPEN_DOCUMENTS_SQL = """
	select ple.party as customer, cust.customer_name, cust.customer_group,
		ple.against_voucher_type as voucher_type, ple.against_voucher_no as voucher_no,
		sum(ple.amount) as outstanding,
		max(case when ple.voucher_no = ple.against_voucher_no then ple.branch end) as branch,
		max(case when ple.voucher_no = ple.against_voucher_no then ple.posting_date end) as posting_date,
		max(case when ple.voucher_no = ple.against_voucher_no then ple.due_date end) as due_date
	from `tabPayment Ledger Entry` ple
	join `tabCustomer` cust on cust.name = ple.party
	where ple.company = %(company)s
		and ple.party_type = 'Customer'
		and ple.account_type = 'Receivable'
		and ple.delinked = 0
		and ple.posting_date <= %(as_on)s
		and (%(customer)s = '' or ple.party = %(customer)s)
		and (%(customer_group)s = '' or cust.customer_group = %(customer_group)s)
	group by ple.party, ple.against_voucher_type, ple.against_voucher_no
	having abs(sum(ple.amount)) >= %(tolerance)s
"""


def open_documents(filters, as_on) -> list:
	"""One row per document with a balance on the Payment Ledger as on `as_on`.

	Grouped on the document the balance belongs to (against_voucher). Its own ledger row -- the one
	where voucher and against_voucher are the same document -- gives the branch, posting date and
	due date; payments allocated to it carry the paying document's own branch, which is ignored.
	"""
	rows = frappe.db.sql(
		OPEN_DOCUMENTS_SQL,
		{
			"company": filters.company,
			"as_on": as_on,
			"customer": filters.customer or "",
			"customer_group": filters.customer_group or "",
			"tolerance": TOLERANCE,
		},
		as_dict=True,
	)

	invoice_info = {}
	invoices = [r.voucher_no for r in rows if r.voucher_type == "Sales Invoice"]
	if invoices:
		from sf_trading.query import fetch_in_map

		invoice_info = fetch_in_map(
			"Sales Invoice", invoices, ["name", "custom_sales_person", "branch", "posting_date", "due_date"]
		)
	for row in rows:
		row.outstanding = flt(row.outstanding, 3)
		info = invoice_info.get(row.voucher_no) if row.voucher_type == "Sales Invoice" else None
		if info:
			# an invoice whose own ledger row is missing from the group still has its own fields
			row.branch = row.branch or info.branch
			row.posting_date = row.posting_date or info.posting_date
			row.due_date = row.due_date or info.due_date
		row.sales_person = info.custom_sales_person if info else None
		row.branch = row.branch or NO_BRANCH
	return rows


UNBILLED_ORDERS_SQL = """
	select so.customer, ifnull(so.branch, '') as branch,
		sum(so.base_grand_total * (100 - so.per_billed) / 100) as unbilled
	from `tabSales Order` so
	where so.company = %(company)s and so.docstatus = 1 and so.per_billed < 100
		and so.status != 'Closed' and so.transaction_date <= %(as_on)s
		and (%(customer)s = '' or so.customer = %(customer)s)
	group by so.customer, so.branch
"""


def unbilled_orders(filters, as_on) -> dict:
	"""(customer, branch) -> unbilled amount of open Sales Orders, as the credit checks count it."""
	rows = frappe.db.sql(
		UNBILLED_ORDERS_SQL,
		{"company": filters.company, "as_on": as_on, "customer": filters.customer or ""},
		as_dict=True,
	)
	return {(r.customer, r.branch or NO_BRANCH): flt(r.unbilled, 3) for r in rows if flt(r.unbilled) > 0}


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


def build_customers(documents, orders, limits, filters) -> list:
	sub_limits = branch_limits()
	per_branch = defaultdict(blank_amounts)
	for doc in documents:
		add_document(per_branch[(doc.customer, doc.branch)], doc)
	for key, amount in orders.items():
		per_branch[key].unbilled += amount

	names = {customer for customer, _branch in per_branch}
	if cint(filters.show_zero):
		credit = set(limits)
		names |= ({filters.customer} & credit) if filters.customer else credit
	if not names:
		return []

	info = {
		r.name: r
		for r in frappe.get_all(
			"Customer",
			filters={"name": ["in", list(names)]},
			fields=["name", "customer_name", "customer_group"]
			+ (["custom_approval_status"] if frappe.db.has_column("Customer", "custom_approval_status") else []),
		)
	}
	if filters.customer_group:
		names = {n for n in names if (info.get(n) or {}).get("customer_group") == filters.customer_group}

	branches_of = defaultdict(list)
	for (customer, branch), amounts in per_branch.items():
		amounts.branch = branch
		amounts.exposure = flt(amounts.outstanding + amounts.unbilled, 3)
		amounts.limit = sub_limits.get((customer, branch), 0)
		amounts.available = flt(amounts.limit - amounts.exposure, 3) if amounts.limit else None
		amounts.over = bool(amounts.limit and amounts.exposure > amounts.limit + TOLERANCE)
		amounts.status = STATUS_OVER if amounts.over else (STATUS_OVERDUE if amounts.overdue > TOLERANCE else "")
		branches_of[customer].append(amounts)

	customers = []
	for name in names:
		row = info.get(name) or frappe._dict(customer_name=name)
		limit = limits.get(name) or frappe._dict(credit_limit=0, credit_days=0)
		total = blank_amounts()
		branch_list = sorted(branches_of.get(name, []), key=lambda b: b.branch)
		for b in branch_list:
			roll_up(total, b)
		total.update(
			customer=name,
			customer_name=row.get("customer_name") or name,
			customer_group=row.get("customer_group"),
			approval_status=row.get("custom_approval_status"),
			credit_days=limit.credit_days,
			limit=limit.credit_limit,
			branches=branch_list,
		)
		total.exposure = flt(total.outstanding + total.unbilled, 3)
		total.available = flt(total.limit - total.exposure, 3) if total.limit else None
		total.utilisation = flt(total.exposure / total.limit * 100, 1) if total.limit else None
		total.over = bool(total.limit and total.exposure > total.limit + TOLERANCE) or any(b.over for b in branch_list)
		if total.over:
			total.status = STATUS_OVER
		elif total.overdue > TOLERANCE:
			total.status = STATUS_OVERDUE
		else:
			total.status = STATUS_WITHIN if total.limit else STATUS_NO_LIMIT
		customers.append(total)
	customers.sort(key=lambda c: (-c.exposure, (c.customer_name or "").lower()))
	return customers


def wanted(customer, filters) -> bool:
	status = filters.status or "All"
	if status == STATUS_OVER and not customer.over:
		return False
	if status == STATUS_OVERDUE and customer.overdue <= TOLERANCE:
		return False
	if status == STATUS_WITHIN and (customer.over or not customer.limit):
		return False
	if not cint(filters.show_zero) and abs(customer.exposure) < TOLERANCE and not customer.unallocated:
		return False
	return True


# ── rows ────────────────────────────────────────────────────────────────────────────────────────


def amount_fields(source, currency) -> dict:
	fields = {key: flt(source[key], 3) for key in AMOUNT_KEYS if key not in ("open_documents", "invoiced")}
	for i in range(1, 7):
		fields["range%s" % i] = flt(source["range%s" % i], 3)
	fields.update(
		exposure=flt(source.exposure, 3),
		open_documents=source.open_documents,
		oldest_due=source.oldest_due,
		currency=currency,
	)
	return fields


def tree_rows(customers, currency) -> list:
	rows = []
	for c in customers:
		rows.append(
			dict(
				amount_fields(c, currency),
				name=c.customer, parent=None, indent=0, label=c.customer_name,
				customer=c.customer, customer_group=c.customer_group, branch=None,
				approval_status=c.approval_status, credit_days=c.credit_days or None,
				credit_limit=c.limit or None, available=c.available, utilisation=c.utilisation,
				status=c.status,
			)
		)
		for b in c.branches:
			rows.append(
				dict(
					amount_fields(b, currency),
					name="%s::%s" % (c.customer, b.branch), parent=c.customer, indent=1, label=b.branch,
					customer=c.customer, customer_group=c.customer_group, branch=b.branch,
					credit_limit=b.limit or None, available=b.available,
					utilisation=flt(b.exposure / b.limit * 100, 1) if b.limit else None,
					status=b.status,
				)
			)
	return rows


def branch_rows(customers, currency) -> list:
	totals = defaultdict(blank_amounts)
	counts = defaultdict(lambda: frappe._dict(customers=set(), over=set(), overdue=set(), limits=0.0))
	for c in customers:
		for b in c.branches:
			roll_up(totals[b.branch], b)
			totals[b.branch].exposure = totals[b.branch].get("exposure", 0) + b.exposure
			k = counts[b.branch]
			k.customers.add(c.customer)
			k.limits += b.limit or 0
			if b.over:
				k.over.add(c.customer)
			if b.overdue > TOLERANCE:
				k.overdue.add(c.customer)
	rows = []
	for branch in sorted(totals):
		t, k = totals[branch], counts[branch]
		rows.append(
			dict(
				amount_fields(t, currency), branch=branch, customers=len(k.customers),
				overdue_customers=len(k.overdue), over_limit_customers=len(k.over),
				branch_limits=flt(k.limits, 3) or None,
			)
		)
	return rows


def document_rows(documents, currency) -> list:
	rows = []
	for d in sorted(documents, key=lambda d: ((d.customer_name or d.customer).lower(), d.branch, str(d.posting_date or ""))):
		if d.outstanding <= 0:
			ageing = _("Credit")
		elif d.bucket == "not_due":
			ageing = _("Not Due")
		else:
			ageing = _("Overdue") if (d.due_date and d.age > 0) else _("Due")
		rows.append(
			{
				"customer": d.customer, "customer_name": d.customer_name, "branch": d.branch,
				"voucher_type": d.voucher_type, "voucher_no": d.voucher_no,
				"posting_date": d.posting_date, "due_date": d.due_date, "age": d.age,
				"ageing": ageing, "outstanding": d.outstanding, "sales_person": d.sales_person,
				"currency": currency,
			}
		)
	return rows


# ── columns ─────────────────────────────────────────────────────────────────────────────────────


def money(fieldname, label, width=115):
	return {"fieldname": fieldname, "label": label, "fieldtype": "Currency", "options": "currency", "width": width}


def bucket_columns(ranges):
	cols = [money("not_due", _("Not Due"), 105)]
	for index, label in enumerate(bucket_labels(ranges)):
		cols.append(money("range%s" % (index + 1), _("{0} Days").format(label), 105))
	return cols


CURRENCY_COLUMN = {"fieldname": "currency", "label": _("Currency"), "fieldtype": "Link", "options": "Currency", "hidden": 1}


def tree_columns(ranges):
	return [
		{"fieldname": "label", "label": _("Customer / Branch"), "fieldtype": "Data", "width": 230},
		{"fieldname": "customer", "label": _("Customer"), "fieldtype": "Link", "options": "Customer", "width": 120},
		{"fieldname": "branch", "label": _("Branch"), "fieldtype": "Link", "options": "Branch", "width": 80},
		{"fieldname": "status", "label": _("Status"), "fieldtype": "Data", "width": 100},
		money("credit_limit", _("Credit Limit")),
		money("outstanding", _("Outstanding")),
		money("overdue", _("Overdue")),
		*bucket_columns(ranges),
		money("unallocated", _("Unallocated Credit")),
		money("unbilled", _("Unbilled Orders")),
		money("exposure", _("Exposure")),
		money("available", _("Available")),
		{"fieldname": "utilisation", "label": _("Used %"), "fieldtype": "Percent", "width": 80},
		{"fieldname": "open_documents", "label": _("Open Invoices"), "fieldtype": "Int", "width": 90},
		{"fieldname": "oldest_due", "label": _("Oldest Due"), "fieldtype": "Date", "width": 95},
		{"fieldname": "credit_days", "label": _("Credit Days"), "fieldtype": "Int", "width": 80},
		{"fieldname": "approval_status", "label": _("Credit Approval"), "fieldtype": "Data", "width": 110},
		{"fieldname": "customer_group", "label": _("Customer Group"), "fieldtype": "Link", "options": "Customer Group", "width": 120},
		CURRENCY_COLUMN,
	]


def branch_columns(ranges):
	return [
		{"fieldname": "branch", "label": _("Branch"), "fieldtype": "Link", "options": "Branch", "width": 110},
		{"fieldname": "customers", "label": _("Customers"), "fieldtype": "Int", "width": 90},
		money("outstanding", _("Outstanding")),
		money("overdue", _("Overdue")),
		*bucket_columns(ranges),
		money("unallocated", _("Unallocated Credit")),
		money("unbilled", _("Unbilled Orders")),
		money("exposure", _("Exposure")),
		money("branch_limits", _("Branch Sub-limits")),
		{"fieldname": "overdue_customers", "label": _("Customers Overdue"), "fieldtype": "Int", "width": 120},
		{"fieldname": "over_limit_customers", "label": _("Over Branch Limit"), "fieldtype": "Int", "width": 120},
		{"fieldname": "open_documents", "label": _("Open Invoices"), "fieldtype": "Int", "width": 90},
		CURRENCY_COLUMN,
	]


def document_columns():
	return [
		{"fieldname": "customer", "label": _("Customer"), "fieldtype": "Link", "options": "Customer", "width": 120},
		{"fieldname": "customer_name", "label": _("Customer Name"), "fieldtype": "Data", "width": 180},
		{"fieldname": "branch", "label": _("Branch"), "fieldtype": "Link", "options": "Branch", "width": 80},
		{"fieldname": "voucher_type", "label": _("Voucher Type"), "fieldtype": "Data", "width": 110},
		{"fieldname": "voucher_no", "label": _("Voucher"), "fieldtype": "Dynamic Link", "options": "voucher_type", "width": 150},
		{"fieldname": "posting_date", "label": _("Posting Date"), "fieldtype": "Date", "width": 95},
		{"fieldname": "due_date", "label": _("Due Date"), "fieldtype": "Date", "width": 95},
		{"fieldname": "age", "label": _("Age (Days)"), "fieldtype": "Int", "width": 85},
		{"fieldname": "ageing", "label": _("Ageing"), "fieldtype": "Data", "width": 90},
		money("outstanding", _("Outstanding")),
		{"fieldname": "sales_person", "label": _("Sales Person"), "fieldtype": "Link", "options": "Sales Person", "width": 120},
		CURRENCY_COLUMN,
	]


# ── cards / chart ───────────────────────────────────────────────────────────────────────────────


def get_summary(customers, currency):
	def card(label, value, indicator):
		return {"label": label, "value": flt(value, 3), "datatype": "Currency", "currency": currency, "indicator": indicator}

	over = sum(1 for c in customers if c.over)
	return [
		{"label": _("Customers"), "value": len(customers), "datatype": "Int", "indicator": "Blue"},
		card(_("Outstanding"), sum(c.outstanding for c in customers), "Blue"),
		card(_("Overdue"), sum(c.overdue for c in customers), "Red"),
		card(_("Unbilled Orders"), sum(c.unbilled for c in customers), "Orange"),
		card(_("Exposure"), sum(c.exposure for c in customers), "Blue"),
		{"label": _("Over Limit"), "value": over, "datatype": "Int", "indicator": "Red" if over else "Green"},
	]


def branch_chart(customers):
	totals = defaultdict(lambda: [0.0, 0.0])
	for c in customers:
		for b in c.branches:
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
