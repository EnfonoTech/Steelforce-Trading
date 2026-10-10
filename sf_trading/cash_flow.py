# sf_trading/cash_flow.py
"""The money ledger behind the cash-flow reports: which rows count, and what each one was for.

Money is read from the General Ledger -- every posting to a Cash or Bank account -- because money
reaches those accounts by routes no single document type covers (payment entries, journals, POS
invoices that never create a payment entry). Both Cash Flow Money In vs Money Out and Cash Flow
Detail read their rows here, so the summary and the detail cannot disagree, and every filter means
the same thing in both.

Each money row is classified by the voucher's OTHER side -- its counterpart rows:

* a customer on the other side   -> Customers        (receipts, or refunds when money went out)
* a supplier                     -> Suppliers
* an employee                    -> Employees
* only other money accounts      -> Internal Transfers (bank to till, PDC to bank)
* otherwise the largest counterpart's account: Expenses, Income, Taxes, Assets, Liabilities, Equity

Transfer detection looks at EVERY money account of the company, whatever the Account filter says,
so filtering on one till still recognises the bank deposit that emptied it.

Two kinds of filter, kept apart on purpose:

* **Which accounts count** -- account, branch, cost centre, PDC holding accounts. These narrow the
  balance too, so the opening balance is read with the same filters and the running balance still
  ends on that set of accounts' real closing balance.
* **Which movements to show** -- direction, category, party, voucher type, mode of payment, internal
  transfers. A balance cannot be narrowed that way, so when any of these is set the running balance
  is not shown; the reports say so instead of printing a balance that is not one.
"""

from collections import defaultdict

import frappe
from frappe import _
from frappe.utils import add_days, cint, flt, getdate

MONEY_ACCOUNT_TYPES = ("Cash", "Bank")

CUSTOMERS = "Customers"
SUPPLIERS = "Suppliers"
EMPLOYEES = "Employees"
TRANSFERS = "Internal Transfers"
CHEQUES_BANKED = "Cheques Banked"
EXPENSES = "Expenses"
INCOME = "Income"
TAXES = "Taxes"
ASSETS = "Assets"
LIABILITIES = "Liabilities"
EQUITY = "Equity"
OTHER = "Other"
CATEGORIES = (CUSTOMERS, SUPPLIERS, EMPLOYEES, EXPENSES, INCOME, TAXES, ASSETS, LIABILITIES, EQUITY,
	CHEQUES_BANKED, TRANSFERS, OTHER)

ROOT_CATEGORY = {"Expense": EXPENSES, "Income": INCOME, "Asset": ASSETS, "Liability": LIABILITIES, "Equity": EQUITY}
PARTY_CATEGORY = {"Customer": CUSTOMERS, "Supplier": SUPPLIERS, "Employee": EMPLOYEES, "Shareholder": EQUITY}

MODE_NOT_SET = "(mode not set)"
MOVEMENT_FILTERS = ("direction", "category", "party_type", "party", "voucher_type", "mode_of_payment",
	"exclude_internal_transfers")
BATCH = 500


# ── accounts ────────────────────────────────────────────────────────────────────────────────────


def money_accounts(company) -> list:
	"""Every non-group Cash / Bank account of the company."""
	return frappe.get_all(
		"Account",
		filters={"company": company, "is_group": 0, "account_type": ["in", list(MONEY_ACCOUNT_TYPES)]},
		pluck="name",
	)


def pdc_accounts(company) -> set:
	"""The holding accounts cheques are received into before they are banked: the default account
	of every cheque Mode of Payment for this company."""
	from sf_trading.pdc_transfer import cheque_modes

	modes = cheque_modes()
	if not modes:
		return set()
	return set(
		frappe.get_all(
			"Mode of Payment Account",
			filters={"parent": ["in", modes], "company": company},
			pluck="default_account",
		)
	) - {None, ""}


def selected_accounts(filters) -> list:
	"""The money accounts this run reads, after the Account and PDC filters."""
	accounts = money_accounts(filters.company)
	if filters.get("account"):
		wanted_accounts = set(filters.account) if isinstance(filters.account, (list, tuple)) else {filters.account}
		unknown = wanted_accounts - set(accounts)
		if unknown:
			names = ", ".join(sorted(unknown))
			frappe.throw(_("{0} is not a Cash or Bank account of {1}.").format(names, filters.company))
		accounts = [a for a in accounts if a in wanted_accounts]
	if cint(filters.get("exclude_pdc_accounts")):
		held = pdc_accounts(filters.company)
		accounts = [a for a in accounts if a not in held]
	return accounts


def cost_centers(cost_center) -> list:
	"""A cost centre and everything under it, so a group cost centre means its whole branch."""
	bounds = frappe.db.get_value("Cost Center", cost_center, ["lft", "rgt"])
	if not bounds or bounds[0] is None:
		return [cost_center]
	return frappe.get_all("Cost Center", filters={"lft": [">=", bounds[0]], "rgt": ["<=", bounds[1]]}, pluck="name")


def permitted(doctype, user=None):
	"""The values a User Permission limits this user to, or None for no limit."""
	from frappe.permissions import get_user_permissions

	user = user or frappe.session.user
	if user == "Administrator":
		return None
	rows = (get_user_permissions(user) or {}).get(doctype) or []
	values = {row.get("doc") for row in rows if row.get("doc")}
	return values or None


SCOPE_BRANCH = " and gle.branch in %(branches)s"
SCOPE_COST_CENTER = " and gle.cost_center in %(cost_centers)s"


def scope_params(filters) -> tuple:
	"""SQL conditions and params for the account-scope filters (branch, cost centre, and the user's
	own Branch / Cost Center permissions). Fixed fragments only; every value is bound."""
	condition, params = "", {}
	branches = set(filters.get("branch") or [])
	allowed = permitted("Branch")
	if allowed is not None:
		branches = (branches & allowed) if branches else allowed
		if not branches:
			branches = {"__no_branch__"}
	if branches:
		condition += SCOPE_BRANCH
		params["branches"] = tuple(branches)
	centres = set(cost_centers(filters.cost_center)) if filters.get("cost_center") else set()
	allowed_cc = permitted("Cost Center")
	if allowed_cc is not None:
		centres = (centres & allowed_cc) if centres else allowed_cc
		if not centres:
			centres = {"__no_cost_center__"}
	if centres:
		condition += SCOPE_COST_CENTER
		params["cost_centers"] = tuple(centres)
	return condition, params


def balance_reconciles(filters) -> bool:
	"""Whether a running balance means anything under these filters (see the module docstring)."""
	return not any(filters.get(key) for key in MOVEMENT_FILTERS)


# ── the money rows ──────────────────────────────────────────────────────────────────────────────


OPENING_SQL = """
	select coalesce(sum(gle.debit - gle.credit), 0)
	from `tabGL Entry` gle
	where gle.company = %(company)s and gle.is_cancelled = 0
		and gle.account in %(accounts)s and gle.posting_date < %(from_date)s
"""

MONEY_ROWS_SQL = """
	select gle.name, gle.posting_date, gle.voucher_type, gle.voucher_no, gle.account, gle.branch,
		gle.cost_center, gle.party_type, gle.party, gle.remarks, gle.debit as money_in,
		gle.credit as money_out, gle.creation
	from `tabGL Entry` gle
	where gle.company = %(company)s and gle.is_cancelled = 0
		and gle.account in %(accounts)s
		and gle.posting_date between %(from_date)s and %(to_date)s
"""

ORDER_BY_DATE = " order by gle.posting_date, gle.creation"


def opening_balance(filters, accounts, from_date) -> float:
	if not accounts:
		return 0.0
	condition, params = scope_params(filters)
	params.update(company=filters.company, accounts=tuple(accounts), from_date=from_date)
	return flt(frappe.db.sql(OPENING_SQL + condition, params)[0][0], 3)


def money_rows(filters, accounts, from_date, to_date) -> list:
	"""Every money-account posting in the range, classified, with the movement filters applied."""
	if not accounts:
		return []
	condition, params = scope_params(filters)
	params.update(company=filters.company, accounts=tuple(accounts), from_date=from_date, to_date=to_date)
	rows = frappe.db.sql(MONEY_ROWS_SQL + condition + ORDER_BY_DATE, params, as_dict=True)
	if not rows:
		return []

	classify(rows, filters.company)
	return [row for row in rows if wanted(row, filters)]


def wanted(row, filters) -> bool:
	direction = filters.get("direction")
	if direction == "Money In" and not flt(row.money_in):
		return False
	if direction == "Money Out" and not flt(row.money_out):
		return False
	if filters.get("category") and row.category != filters.category:
		return False
	if filters.get("party_type") and row.counter_party_type != filters.party_type:
		return False
	if filters.get("party") and row.counter_party != filters.party:
		return False
	if filters.get("voucher_type") and row.voucher_type != filters.voucher_type:
		return False
	if filters.get("mode_of_payment") and row.mode_of_payment != filters.mode_of_payment:
		return False
	if cint(filters.get("exclude_internal_transfers")) and row.category in (TRANSFERS, CHEQUES_BANKED):
		return False
	return True


# ── classification ──────────────────────────────────────────────────────────────────────────────


COUNTERPART_SQL = """
	select gle.voucher_no, gle.account, gle.party_type, gle.party,
		sum(gle.debit - gle.credit) as amount
	from `tabGL Entry` gle
	where gle.is_cancelled = 0 and gle.voucher_no in %(vouchers)s and gle.account not in %(money)s
	group by gle.voucher_no, gle.account, gle.party_type, gle.party
"""


def classify(rows, company):
	"""Set category, counter party and mode of payment on every money row (one pass per batch)."""
	money = set(money_accounts(company))
	held = pdc_accounts(company)
	vouchers = list({row.voucher_no for row in rows})
	accounts = {
		a.name: a
		for a in frappe.get_all("Account", filters={"company": company}, fields=["name", "root_type", "account_type"])
	}

	counterparts = defaultdict(list)
	for start in range(0, len(vouchers), BATCH):
		chunk = vouchers[start : start + BATCH]
		for c in frappe.db.sql(COUNTERPART_SQL, {"vouchers": tuple(chunk), "money": tuple(money)}, as_dict=True):
			if abs(flt(c.amount)) > 0.0005:
				counterparts[c.voucher_no].append(c)

	modes = voucher_modes(rows, company)
	for row in rows:
		category, party_type, party = classify_voucher(counterparts.get(row.voucher_no) or [], accounts, held)
		row.category = category
		row.counter_party_type = party_type
		row.counter_party = party
		row.mode_of_payment = modes.get((row.voucher_no, row.account)) or modes.get(row.voucher_no) or MODE_NOT_SET
		row.money_in = flt(row.money_in, 3)
		row.money_out = flt(row.money_out, 3)


def classify_voucher(counterparts, accounts, held) -> tuple:
	"""(category, party_type, party) for one voucher, from its non-money rows."""
	if not counterparts:
		return TRANSFERS, None, None
	parties = [c for c in counterparts if c.party_type and c.party]
	for party_type in ("Customer", "Supplier", "Employee", "Shareholder"):
		mine = [c for c in parties if c.party_type == party_type]
		if mine:
			biggest = max(mine, key=lambda c: abs(flt(c.amount)))
			return PARTY_CATEGORY[party_type], party_type, biggest.party
	if held and all(c.account in held for c in counterparts):
		# a PDC holding account left out of money: this is a cheque being banked, not income
		return CHEQUES_BANKED, None, None
	biggest = max(counterparts, key=lambda c: abs(flt(c.amount)))
	account = accounts.get(biggest.account) or frappe._dict()
	if account.account_type == "Tax":
		return TAXES, None, None
	return ROOT_CATEGORY.get(account.root_type, OTHER), None, None


def voucher_modes(rows, company) -> dict:
	"""Mode of payment per voucher (or per voucher + account for a POS invoice).

	Payment Entry and Journal Entry carry it on the header; a POS invoice per payment row, keyed by
	the account it was paid into. Anything else, or a header left blank, is resolved from the money
	account when exactly one Mode of Payment uses it.
	"""
	by_type = defaultdict(set)
	for row in rows:
		by_type[row.voucher_type].add(row.voucher_no)

	modes = {}
	for doctype in ("Payment Entry", "Journal Entry"):
		names = list(by_type.get(doctype) or [])
		for start in range(0, len(names), BATCH):
			chunk = names[start : start + BATCH]
			for r in frappe.get_all(doctype, filters={"name": ["in", chunk]}, fields=["name", "mode_of_payment"]):
				if r.mode_of_payment:
					modes[r.name] = r.mode_of_payment
	invoices = list(by_type.get("Sales Invoice") or [])
	for start in range(0, len(invoices), BATCH):
		chunk = invoices[start : start + BATCH]
		for r in frappe.get_all("Sales Invoice Payment", filters={"parent": ["in", chunk]},
			fields=["parent", "account", "mode_of_payment"]):
			if r.mode_of_payment:
				modes[(r.parent, r.account)] = r.mode_of_payment

	by_account = defaultdict(set)
	for r in frappe.get_all("Mode of Payment Account", filters={"company": company}, fields=["parent", "default_account"]):
		if r.default_account:
			by_account[r.default_account].add(r.parent)
	for row in rows:
		if row.voucher_no in modes or (row.voucher_no, row.account) in modes:
			continue
		candidates = by_account.get(row.account) or set()
		if len(candidates) == 1:
			modes[(row.voucher_no, row.account)] = next(iter(candidates))
	return modes


# ── receivables and payables ────────────────────────────────────────────────────────────────────


PARTY_LEDGER_SQL = """
	select gle.voucher_type, gle.voucher_no, gle.account, gle.party_type, gle.party, gle.posting_date,
		gle.debit, gle.credit, acc.account_type
	from `tabGL Entry` gle
	join `tabAccount` acc on acc.name = gle.account
	where gle.company = %(company)s and gle.is_cancelled = 0
		and acc.account_type in ('Receivable', 'Payable')
		and gle.posting_date between %(from_date)s and %(to_date)s
"""

PARTY_BALANCE_SQL = """
	select acc.account_type, coalesce(sum(gle.debit - gle.credit), 0) as balance
	from `tabGL Entry` gle
	join `tabAccount` acc on acc.name = gle.account
	where gle.company = %(company)s and gle.is_cancelled = 0
		and acc.account_type in ('Receivable', 'Payable')
		and gle.posting_date < %(as_on)s
"""

PARTY_TYPE_CONDITION = " and gle.party_type = %(party_type)s"
PARTY_CONDITION = " and gle.party = %(party)s"
GROUP_BY_ACCOUNT_TYPE = " group by acc.account_type"


def receivables_and_payables(filters, from_date, to_date) -> dict:
	"""What customers owed and the company owed suppliers: opening, what moved it, closing.

	Read from the General Ledger on Receivable / Payable accounts, so it ties to the Trial Balance.
	A movement is "collected" / "paid" when its voucher also touched a money account, so a set-off
	journal or a write-off is an adjustment, not cash.
	"""
	condition, params = scope_params(filters)
	if filters.get("party_type"):
		condition += PARTY_TYPE_CONDITION
		params["party_type"] = filters.party_type
	if filters.get("party"):
		condition += PARTY_CONDITION
		params["party"] = filters.party
	params.update(company=filters.company, from_date=from_date, to_date=to_date)

	def balances(as_on):
		query = PARTY_BALANCE_SQL + condition + GROUP_BY_ACCOUNT_TYPE
		return {r.account_type: flt(r.balance, 3) for r in frappe.db.sql(query, dict(params, as_on=as_on), as_dict=True)}

	opening, closing = balances(from_date), balances(add_days(to_date, 1))
	rows = frappe.db.sql(PARTY_LEDGER_SQL + condition, params, as_dict=True)

	money = list(money_accounts(filters.company))
	cash_vouchers = set()
	vouchers = list({r.voucher_no for r in rows if r.voucher_type in ("Payment Entry", "Journal Entry")})
	for start in range(0, len(vouchers), BATCH):
		chunk = vouchers[start : start + BATCH]
		cash_vouchers |= set(frappe.get_all("GL Entry", filters={"voucher_no": ["in", chunk],
			"account": ["in", money], "is_cancelled": 0}, pluck="voucher_no", distinct=True))

	# An invoice can carry its own payment (a POS sale, a paid purchase bill): then the second
	# receivable/payable row of the SAME voucher is the settlement, not a return.
	returns = set()
	for doctype in ("Sales Invoice", "Purchase Invoice"):
		names = list({r.voucher_no for r in rows if r.voucher_type == doctype})
		for start in range(0, len(names), BATCH):
			chunk = names[start : start + BATCH]
			returns |= set(frappe.get_all(doctype, filters={"name": ["in", chunk], "is_return": 1}, pluck="name"))

	lines = {"Receivable": defaultdict(float), "Payable": defaultdict(float)}
	for r in rows:
		book = lines[r.account_type]
		debit, credit = flt(r.debit), flt(r.credit)
		receivable = r.account_type == "Receivable"
		if r.voucher_type in ("Sales Invoice", "Purchase Invoice"):
			raised, cleared = (debit, credit) if r.voucher_type == "Sales Invoice" else (credit, debit)
			if r.voucher_no in returns:
				book["returned"] += cleared
				book["refunded"] += raised
			else:
				book["invoiced"] += raised
				book["settled"] += cleared
		elif r.voucher_no in cash_vouchers:
			book["settled"] += credit if receivable else debit
			book["refunded"] += debit if receivable else credit
		else:
			book["adjusted"] += (debit - credit) if receivable else (credit - debit)
	return {"opening": opening, "closing": closing, "lines": lines}


def as_dates(filters, default_from):
	to_date = getdate(filters.get("to_date") or frappe.utils.nowdate())
	from_date = getdate(filters.get("from_date") or default_from(to_date))
	if from_date > to_date:
		frappe.throw(_("From Date is after To Date."))
	return from_date, to_date
