# sf_trading/sf_trading/report/landed_cost_reconciliation/landed_cost_reconciliation.py
"""Landed Cost Reconciliation: expenses booked as landed costs against the charges that absorbed them.

Three views (sf_trading.landed_cost explains the link between the two sides):

* Expense Entries -- every invoice, journal or payment debiting a landed-cost account, with what
  the charges naming it have absorbed: Unallocated, Partly Allocated, Pending (only drafts take
  it), Allocated, Over-allocated.
* Landed Cost Charges -- every Landed Cost Voucher charge and every Valuation charge on a Purchase
  Invoice / Receipt, matched to its expense entry or not: Matched, Unmatched (names none),
  Pending (still a draft), Account Mismatch, Expense Cancelled.
* Account Summary -- per landed-cost account: expenses booked, absorbed into stock, the ledger
  balance, and what is unallocated or unmatched.

A landed-cost account is any account a charge has absorbed into, plus the company's Expenses
Included In Valuation account.
"""

from collections import defaultdict

import frappe
from frappe import _
from frappe.query_builder.functions import Sum
from frappe.utils import add_months, cint, date_diff, flt, getdate, nowdate

from sf_trading import landed_cost as lc

EXPENSES = "Expense Entries"
CHARGES = "Landed Cost Charges"
SUMMARY = "Account Summary"
TOL = lc.TOLERANCE

UNALLOCATED, PARTLY, PENDING, ALLOCATED, OVER = "Unallocated", "Partly Allocated", "Pending", "Allocated", "Over-allocated"
MATCHED, UNMATCHED, MISMATCH, CANCELLED = "Matched", "Unmatched", "Account Mismatch", "Expense Cancelled"


def execute(filters=None):
	filters = frappe._dict(filters or {})
	if not filters.get("company"):
		frappe.throw(_("Please choose a company."))
	filters.from_date = getdate(filters.get("from_date") or add_months(getdate(nowdate()), -6))
	filters.to_date = getdate(filters.get("to_date") or nowdate())
	currency = frappe.get_cached_value("Company", filters.company, "default_currency")
	accounts = landed_cost_accounts(filters.company)
	if filters.get("account"):
		accounts = [a for a in accounts if a == filters.account] or [filters.account]
	if not accounts:
		return expense_columns(), [], _("No landed-cost accounts found for this company.")

	view = filters.get("view") or EXPENSES
	expenses = expense_lines(filters, accounts) if view in (EXPENSES, SUMMARY) else []
	charges = charge_lines(filters, accounts) if view in (CHARGES, SUMMARY) else []
	if view == SUMMARY:
		data = summary_rows(filters, accounts, expenses, charges, currency)
		return summary_columns(), data, None, None, cards(expenses, charges, currency)

	rows = expenses if view == EXPENSES else charges
	if filters.get("status"):
		rows = [r for r in rows if r.status == filters.status]
	for r in rows:
		r.currency = currency
	cols = expense_columns() if view == EXPENSES else charge_columns()
	return cols, rows, None, None, cards(expenses if view == EXPENSES else [], charges if view == CHARGES else [], currency)


def landed_cost_accounts(company) -> list:
	"""Accounts a landed cost lands on, for this company."""
	names = set()
	eiv = frappe.get_cached_value("Company", company, "expenses_included_in_valuation")
	if eiv:
		names.add(eiv)
	names.update(frappe.get_all(lc.LCV_CHARGES, filters={"parenttype": "Landed Cost Voucher"}, pluck="expense_account",
		distinct=True))
	names.update(frappe.get_all(lc.PURCHASE_CHARGES, filters={"parenttype": ["in", lc.CHARGE_PARENTS],
		"category": ["in", lc.VALUATION_CATEGORIES]}, pluck="account_head", distinct=True))
	names.discard(None)
	if not names:
		return []
	return frappe.get_all("Account", filters={"name": ["in", list(names)], "company": company, "is_group": 0},
		pluck="name", order_by="name")


# ─── expense side ────────────────────────────────────────────────────────────────────────────────


def expense_lines(filters, accounts) -> list:
	gl = frappe.qb.DocType("GL Entry")
	total = Sum(gl.debit - gl.credit)
	query = (
		frappe.qb.from_(gl)
		.select(gl.voucher_type, gl.voucher_no, gl.account, gl.posting_date, gl.branch, total.as_("booked"))
		.where(
			(gl.company == filters.company)
			& (gl.is_cancelled == 0)
			& gl.account.isin(accounts)
			& gl.voucher_type.isin(list(lc.EXPENSE_DOCTYPES))
			& (gl.posting_date >= filters.from_date)
			& (gl.posting_date <= filters.to_date)
		)
		.groupby(gl.voucher_type, gl.voucher_no, gl.account)
		.having(total > TOL)
	)
	if filters.get("expense_doctype"):
		query = query.where(gl.voucher_type == filters.expense_doctype)
	rows = query.run(as_dict=True)
	if not rows:
		return []

	by_type = defaultdict(set)
	for r in rows:
		by_type[r.voucher_type].add(r.voucher_no)
	details = {}
	for doctype, names in by_type.items():
		fields = ["name", lc.EXCLUDE_FIELD] if frappe.db.has_column(doctype, lc.EXCLUDE_FIELD) else ["name"]
		fields += {"Purchase Invoice": ["supplier as party", "bill_no as reference"],
			"Payment Entry": ["party", "reference_no as reference"],
			"Journal Entry": ["cheque_no as reference", "user_remark as remark"]}[doctype]
		for d in frappe.get_all(doctype, filters={"name": ["in", list(names)]}, fields=fields):
			details[(doctype, d.name)] = d

	allocation = allocations()
	today = getdate(nowdate())
	out = []
	for r in rows:
		d = details.get((r.voucher_type, r.voucher_no)) or frappe._dict()
		if cint(d.get(lc.EXCLUDE_FIELD)) and not cint(filters.get("include_excluded")):
			continue
		if filters.get("supplier") and d.get("party") != filters.supplier:
			continue
		if filters.get("branch") and r.branch != filters.branch:
			continue
		a = allocation.get((r.voucher_type, r.voucher_no, r.account)) or frappe._dict(submitted=0.0, draft=0.0, documents=[])
		unallocated = flt(r.booked - a.submitted - a.draft, 3)
		if unallocated < -TOL:
			status = OVER
		elif abs(unallocated) <= TOL and a.draft <= TOL:
			status = ALLOCATED
		elif a.submitted <= TOL and a.draft <= TOL:
			status = UNALLOCATED
		elif a.submitted <= TOL:
			status = PENDING
		else:
			status = PARTLY
		out.append(frappe._dict(
			expense_doctype=r.voucher_type, expense_entry=r.voucher_no, posting_date=r.posting_date,
			party=d.get("party"), reference=d.get("reference") or (d.get("remark") or "")[:80],
			account=r.account, branch=r.branch, booked=flt(r.booked, 3), allocated=a.submitted, pending=a.draft,
			unallocated=unallocated, status=status, absorbed_by=", ".join(sorted({x[1] for x in a.documents})),
			days=date_diff(today, r.posting_date) if r.posting_date else 0,
			excluded=cint(d.get(lc.EXCLUDE_FIELD)),
		))
	out.sort(key=lambda r: (r.status == ALLOCATED, r.posting_date or today, r.expense_entry))
	return out


def allocations() -> dict:
	"""{(expense doctype, expense entry, account): {submitted, draft, documents}} over every live charge."""
	out = {}
	if not frappe.db.has_column(lc.LCV_CHARGES, lc.ENTRY_FIELD):
		return out

	def add(key, docstatus, amount, document):
		a = out.setdefault(key, frappe._dict(submitted=0.0, draft=0.0, documents=[]))
		if cint(docstatus) == 1:
			a.submitted = flt(a.submitted + flt(amount), 3)
		else:
			a.draft = flt(a.draft + flt(amount), 3)
		if document not in a.documents:
			a.documents.append(document)

	for r in frappe.get_all(lc.LCV_CHARGES, filters={lc.ENTRY_FIELD: ["is", "set"], "docstatus": ["<", 2],
			"parenttype": "Landed Cost Voucher"},
			fields=["parent", "parenttype", "docstatus", "base_amount", "expense_account", lc.DOCTYPE_FIELD, lc.ENTRY_FIELD]):
		add((r.get(lc.DOCTYPE_FIELD), r.get(lc.ENTRY_FIELD), r.expense_account), r.docstatus, r.base_amount,
			(r.parenttype, r.parent))
	if frappe.db.has_column(lc.PURCHASE_CHARGES, lc.ENTRY_FIELD):
		for r in frappe.get_all(lc.PURCHASE_CHARGES, filters={lc.ENTRY_FIELD: ["is", "set"], "docstatus": ["<", 2],
				"parenttype": ["in", lc.CHARGE_PARENTS], "category": ["in", lc.VALUATION_CATEGORIES], "add_deduct_tax": "Add"},
				fields=["parent", "parenttype", "docstatus", "base_tax_amount", "account_head", lc.DOCTYPE_FIELD, lc.ENTRY_FIELD]):
			add((r.get(lc.DOCTYPE_FIELD), r.get(lc.ENTRY_FIELD), r.account_head), r.docstatus, r.base_tax_amount,
				(r.parenttype, r.parent))
	return out


# ─── charge side ─────────────────────────────────────────────────────────────────────────────────


def charge_lines(filters, accounts) -> list:
	link_fields = [lc.DOCTYPE_FIELD, lc.ENTRY_FIELD] if frappe.db.has_column(lc.LCV_CHARGES, lc.ENTRY_FIELD) else []
	parents = {}
	lcv = frappe.get_all("Landed Cost Voucher", filters={"company": filters.company, "docstatus": ["<", 2],
		"posting_date": ["between", [filters.from_date, filters.to_date]]}, fields=["name", "posting_date", "docstatus"])
	for p in lcv:
		parents[("Landed Cost Voucher", p.name)] = p
	rows = []
	if lcv:
		for r in frappe.get_all(lc.LCV_CHARGES, filters={"parenttype": "Landed Cost Voucher",
				"parent": ["in", [p.name for p in lcv]], "expense_account": ["in", accounts]},
				fields=["parent", "parenttype", "idx", "expense_account as account", "base_amount as amount", "description"]
				+ link_fields):
			rows.append(r)
	purchase_links = [lc.DOCTYPE_FIELD, lc.ENTRY_FIELD] if frappe.db.has_column(lc.PURCHASE_CHARGES, lc.ENTRY_FIELD) else []
	for doctype in lc.CHARGE_PARENTS:
		fields = ["name", "posting_date", "docstatus", "supplier"]
		if frappe.db.has_column(doctype, "branch"):
			fields.append("branch")
		docs = frappe.get_all(doctype, filters={"company": filters.company, "docstatus": ["<", 2],
			"posting_date": ["between", [filters.from_date, filters.to_date]]}, fields=fields)
		if not docs:
			continue
		for p in docs:
			parents[(doctype, p.name)] = p
		names = [p.name for p in docs]
		for i in range(0, len(names), 1000):
			rows += frappe.get_all(lc.PURCHASE_CHARGES, filters={"parenttype": doctype, "parent": ["in", names[i:i + 1000]],
				"category": ["in", lc.VALUATION_CATEGORIES], "add_deduct_tax": "Add", "account_head": ["in", accounts]},
				fields=["parent", "parenttype", "idx", "account_head as account", "base_tax_amount as amount", "description"]
				+ purchase_links)

	allocation = allocations()
	booked_cache, status_cache = {}, {}
	out = []
	for r in rows:
		parent = parents.get((r.parenttype, r.parent)) or frappe._dict()
		if filters.get("supplier") and parent.get("supplier") and parent.supplier != filters.supplier:
			continue
		if filters.get("branch") and parent.get("branch") and parent.branch != filters.branch:
			continue
		expense_dt, expense = r.get(lc.DOCTYPE_FIELD), r.get(lc.ENTRY_FIELD)
		if cint(parent.get("docstatus")) == 0:
			status = PENDING
		elif not expense:
			status = UNMATCHED
		else:
			key = (expense_dt, expense)
			if key not in status_cache:
				status_cache[key] = cint(frappe.db.get_value(expense_dt, expense, "docstatus")) if expense_dt else 2
			if status_cache[key] != 1:
				status = CANCELLED
			else:
				if (expense_dt, expense, r.account) not in booked_cache:
					booked_cache[(expense_dt, expense, r.account)] = lc.booked(expense_dt, expense, r.account).get(r.account, 0.0)
				on_account = booked_cache[(expense_dt, expense, r.account)]
				taken = allocation.get((expense_dt, expense, r.account))
				if on_account <= TOL:
					status = MISMATCH
				elif taken and flt(taken.submitted + taken.draft) - on_account > TOL:
					status = OVER
				else:
					status = MATCHED
		out.append(frappe._dict(
			charge_doctype=r.parenttype, charge_document=r.parent, row=r.idx, posting_date=parent.get("posting_date"),
			supplier=parent.get("supplier"), account=r.account, amount=flt(r.amount, 3),
			description=(r.description or "")[:100], expense_doctype=expense_dt, expense_entry=expense, status=status,
		))
	out.sort(key=lambda r: (r.status == MATCHED, r.posting_date or getdate(nowdate()), r.charge_document, r.row))
	return out


# ─── account summary ─────────────────────────────────────────────────────────────────────────────


def summary_rows(filters, accounts, expenses, charges, currency) -> list:
	gl = frappe.qb.DocType("GL Entry")
	debit, credit = Sum(gl.debit), Sum(gl.credit)
	movement = (
		frappe.qb.from_(gl)
		.select(gl.account, gl.voucher_type, debit.as_("debit"), credit.as_("credit"))
		.where((gl.company == filters.company) & (gl.is_cancelled == 0) & gl.account.isin(accounts)
			& (gl.posting_date >= filters.from_date) & (gl.posting_date <= filters.to_date))
		.groupby(gl.account, gl.voucher_type)
		.run(as_dict=True)
	)
	closing = dict(
		(r.account, flt(r.balance))
		for r in frappe.qb.from_(gl)
		.select(gl.account, Sum(gl.debit - gl.credit).as_("balance"))
		.where((gl.company == filters.company) & (gl.is_cancelled == 0) & gl.account.isin(accounts)
			& (gl.posting_date <= filters.to_date))
		.groupby(gl.account)
		.run(as_dict=True)
	)
	agg = defaultdict(lambda: frappe._dict(booked=0.0, absorbed=0.0, other=0.0))
	for m in movement:
		a = agg[m.account]
		net = flt(m.debit) - flt(m.credit)
		if m.voucher_type in lc.EXPENSE_DOCTYPES and net > 0:
			a.booked += net
		elif m.voucher_type in ("Purchase Receipt", "Purchase Invoice") and net < 0:
			a.absorbed += -net
		else:
			a.other += net
	out = []
	for account in accounts:
		a = agg.get(account) or frappe._dict(booked=0.0, absorbed=0.0, other=0.0)
		mine = [e for e in expenses if e.account == account]
		unmatched = [c for c in charges if c.account == account and c.status in (UNMATCHED, MISMATCH, CANCELLED)]
		out.append({
			"account": account,
			"expenses_booked": flt(a.booked, 3),
			"absorbed_into_stock": flt(a.absorbed, 3),
			"other_movement": flt(a.other, 3),
			"closing_balance": flt(closing.get(account), 3),
			"unallocated_expenses": flt(sum(max(e.unallocated, 0) for e in mine), 3),
			"unallocated_count": len([e for e in mine if e.status in (UNALLOCATED, PARTLY, PENDING)]),
			"unmatched_charges": flt(sum(c.amount for c in unmatched), 3),
			"unmatched_count": len(unmatched),
			"currency": currency,
		})
	return out


def cards(expenses, charges, currency):
	out = []
	if expenses:
		open_ = [e for e in expenses if e.status in (UNALLOCATED, PARTLY, PENDING)]
		out += [
			{"label": _("Expense Lines Not Fully Allocated"), "value": len(open_), "datatype": "Int",
				"indicator": "Orange" if open_ else "Green"},
			{"label": _("Unallocated Amount"), "value": flt(sum(max(e.unallocated, 0) for e in open_), 3),
				"datatype": "Currency", "currency": currency, "indicator": "Orange"},
			{"label": _("Over-allocated"), "value": len([e for e in expenses if e.status == OVER]), "datatype": "Int",
				"indicator": "Red"},
		]
	if charges:
		bad = [c for c in charges if c.status in (UNMATCHED, MISMATCH, CANCELLED, OVER)]
		out += [
			{"label": _("Charges Unmatched"), "value": len(bad), "datatype": "Int", "indicator": "Red" if bad else "Green"},
			{"label": _("Unmatched Amount"), "value": flt(sum(c.amount for c in bad), 3), "datatype": "Currency",
				"currency": currency, "indicator": "Red"},
			{"label": _("Charges Pending (draft)"), "value": len([c for c in charges if c.status == PENDING]),
				"datatype": "Int", "indicator": "Grey"},
		]
	return out


def money(fieldname, label, width=120):
	return {"label": label, "fieldname": fieldname, "fieldtype": "Currency", "options": "currency", "width": width}


def expense_columns():
	return [
		{"label": _("Expense Type"), "fieldname": "expense_doctype", "fieldtype": "Data", "width": 125},
		{"label": _("Expense Entry"), "fieldname": "expense_entry", "fieldtype": "Dynamic Link", "options": "expense_doctype", "width": 170},
		{"label": _("Posting Date"), "fieldname": "posting_date", "fieldtype": "Date", "width": 100},
		{"label": _("Status"), "fieldname": "status", "fieldtype": "Data", "width": 120},
		{"label": _("Account"), "fieldname": "account", "fieldtype": "Link", "options": "Account", "width": 220},
		money("booked", _("Booked")),
		money("allocated", _("Allocated")),
		money("pending", _("Pending (draft)")),
		money("unallocated", _("Unallocated")),
		{"label": _("Absorbed By"), "fieldname": "absorbed_by", "fieldtype": "Data", "width": 200},
		{"label": _("Party"), "fieldname": "party", "fieldtype": "Data", "width": 150},
		{"label": _("Reference"), "fieldname": "reference", "fieldtype": "Data", "width": 160},
		{"label": _("Branch"), "fieldname": "branch", "fieldtype": "Link", "options": "Branch", "width": 100},
		{"label": _("Days"), "fieldname": "days", "fieldtype": "Int", "width": 60},
		{"label": _("Currency"), "fieldname": "currency", "fieldtype": "Link", "options": "Currency", "hidden": 1},
	]


def charge_columns():
	return [
		{"label": _("Charged On"), "fieldname": "charge_doctype", "fieldtype": "Data", "width": 140},
		{"label": _("Document"), "fieldname": "charge_document", "fieldtype": "Dynamic Link", "options": "charge_doctype", "width": 170},
		{"label": _("Row"), "fieldname": "row", "fieldtype": "Int", "width": 50},
		{"label": _("Posting Date"), "fieldname": "posting_date", "fieldtype": "Date", "width": 100},
		{"label": _("Status"), "fieldname": "status", "fieldtype": "Data", "width": 125},
		{"label": _("Account"), "fieldname": "account", "fieldtype": "Link", "options": "Account", "width": 220},
		money("amount", _("Amount")),
		{"label": _("Expense Type"), "fieldname": "expense_doctype", "fieldtype": "Data", "width": 120},
		{"label": _("Expense Entry"), "fieldname": "expense_entry", "fieldtype": "Dynamic Link", "options": "expense_doctype", "width": 170},
		{"label": _("Supplier"), "fieldname": "supplier", "fieldtype": "Link", "options": "Supplier", "width": 150},
		{"label": _("Description"), "fieldname": "description", "fieldtype": "Data", "width": 200},
		{"label": _("Currency"), "fieldname": "currency", "fieldtype": "Link", "options": "Currency", "hidden": 1},
	]


def summary_columns():
	return [
		{"label": _("Account"), "fieldname": "account", "fieldtype": "Link", "options": "Account", "width": 240},
		money("expenses_booked", _("Expenses Booked"), 130),
		money("absorbed_into_stock", _("Absorbed Into Stock"), 140),
		money("other_movement", _("Other Movement"), 120),
		money("closing_balance", _("Ledger Balance"), 130),
		money("unallocated_expenses", _("Unallocated Expenses"), 140),
		{"label": _("Lines"), "fieldname": "unallocated_count", "fieldtype": "Int", "width": 60},
		money("unmatched_charges", _("Unmatched Charges"), 130),
		{"label": _("Charges"), "fieldname": "unmatched_count", "fieldtype": "Int", "width": 70},
		{"label": _("Currency"), "fieldname": "currency", "fieldtype": "Link", "options": "Currency", "hidden": 1},
	]
