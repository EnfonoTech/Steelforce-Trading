# sf_trading/report_speed.py
"""Balance Sheet, Profit and Loss and Cash Flow: the same figures, read less.

ERPNext builds a financial statement from the year's GL Entry rows and reads them wastefully in two ways.

1. One walk of the whole year per account type. `financial_statements.get_accounting_entries` runs once for each
   root (a Balance Sheet reads Asset, Liability and Equity, and the profit it shows reads Income and Expense). It
   forces `posting_date_company_index` and keeps the root's accounts with an EXISTS(select ... from tabAccount) test,
   so every one of those reads walks EVERY row of the year -- 309,000 on steelforce, about 2 s apiece -- to keep a
   fraction of them. The accounts of a root are few and known, so they are named in the query instead and MariaDB
   chooses its own way in: only the rows of those accounts are read. Same rows, in the same order (posting_date, name,
   which the forced index used to give for free), so the same totals to the last digit.

2. One query per period and account type. Cash Flow asks `cash_flow.get_account_type_based_gl_data` for
   `sum(credit) - sum(debit)` once for every period and every account type: 72 statements for a monthly year. One
   query per account type, grouped by day, answers all of them, and a period is the sum of its days. The days are
   added as exact decimals, so a period comes out as the very number the database would have returned.

Both versions are written against one exact release of ERPNext's code and are used only while the source of the
function they replace is that release's (`fingerprint`). After an ERPNext upgrade that changes either function the
stock one simply runs again and a line in logs/sf_trading.log says so -- a speed-up never has to be trusted blind.
`SF Trading Settings > Report Speed` turns both off at once, with no restart.

Installed on `before_request` and `before_job` (the Prepared Report runs in a background job), like
api/query_report_columns.py. The patch lives in a process that serves every site of the bench, so each call asks
whether THIS site has sf_trading and the switch on; another site keeps ERPNext's own queries.

Measured on steelforce and altabiah (read-only, live data): every report's output byte-identical to the stock one;
Balance Sheet 12 s -> 3 s, Profit and Loss 8.6 s -> 1.8 s, Cash Flow (yearly) 13 s -> 2.9 s on steelforce.
"""

import hashlib
import inspect
import textwrap
from decimal import Decimal

import frappe
from frappe.utils import cstr, getdate

from sf_trading import switches

APP = "sf_trading"
SWITCH = "fast_financial_statements"

#: sha1 of the stock functions this module's copies were written against: erpnext 15.114 - 15.116
STOCK_FINGERPRINTS = {
	"get_accounting_entries": {"0f26b1e6d96e315745aeb81f8f82e25cb5d089ff"},
	"get_account_type_based_gl_data": {"d070fe2c76c3bdcf01f4e0afd2a408173328c3c0"},
}

#: function name -> the stock function, kept so that the switch can hand a call back to it
STOCK: dict = {}
_STATE = {"tried": False}


def fingerprint(function) -> str:
	"""SHA-1 of a function's source with indentation and trailing spaces ignored; "" when the source is unreadable."""
	try:
		source = textwrap.dedent(inspect.getsource(function))
	except (OSError, TypeError):
		return ""
	return hashlib.sha1("\n".join(line.rstrip() for line in source.strip().splitlines()).encode()).hexdigest()


def is_enabled() -> bool:
	"""Whether this call may use the faster reads: sf_trading installed on THIS site and the switch on.

	Decided once per request or job (frappe.local is cleared after each), because a statement calls the two
	functions dozens of times and the answer cannot change under it.
	"""
	state = getattr(frappe.local, "sf_fast_statements", None)
	if state is None:
		state = frappe.local.sf_fast_statements = bool(APP in frappe.get_installed_apps() and switches.is_on(SWITCH))
	return state


# ---------------------------------------------------------------------------------------------------------------
# 1. the year's GL rows, by account instead of by walking the year
# ---------------------------------------------------------------------------------------------------------------


def accounts_under(root_lft, root_rgt, root_type) -> list:
	"""The leaf accounts ERPNext's EXISTS(select ... from tabAccount) test lets through, named once.

	Same conditions as `financial_statements.get_account_filter_query`: not a group, inside the root's nested-set
	range when one is given, of the root type when one is given.
	"""
	sql = "select name from `tabAccount` where is_group = 0"
	args = []
	if root_lft and root_rgt:
		sql += " and lft >= %s and rgt <= %s"
		args += [root_lft, root_rgt]
	if root_type:
		sql += " and root_type = %s"
		args.append(root_type)
	return frappe.db.sql_list(sql, args)


def fast_get_accounting_entries(
	doctype,
	from_date,
	to_date,
	filters,
	root_lft=None,
	root_rgt=None,
	root_type=None,
	ignore_closing_entries=None,
	period_closing_voucher=None,
	ignore_opening_entries=False,
	group_by_account=False,
):
	"""`financial_statements.get_accounting_entries` (erpnext 15.116), with two lines changed, marked below."""
	from erpnext.accounts.report.financial_statements import apply_additional_conditions
	from frappe.desk.reportview import build_match_conditions
	from frappe.query_builder.functions import Sum

	gl_entry = frappe.qb.DocType(doctype)
	query = (
		frappe.qb.from_(gl_entry)
		.select(
			gl_entry.account,
			gl_entry.debit if not group_by_account else Sum(gl_entry.debit).as_("debit"),
			gl_entry.credit if not group_by_account else Sum(gl_entry.credit).as_("credit"),
			gl_entry.debit_in_account_currency
			if not group_by_account
			else Sum(gl_entry.debit_in_account_currency).as_("debit_in_account_currency"),
			gl_entry.credit_in_account_currency
			if not group_by_account
			else Sum(gl_entry.credit_in_account_currency).as_("credit_in_account_currency"),
			gl_entry.account_currency,
		)
		.where(gl_entry.company == filters.company)
	)

	ignore_is_opening = frappe.db.get_single_value(
		"Accounts Settings", "ignore_is_opening_check_for_reporting"
	)

	if doctype == "GL Entry":
		query = query.select(gl_entry.posting_date, gl_entry.is_opening, gl_entry.fiscal_year)
		query = query.where(gl_entry.is_cancelled == 0)
		query = query.where(gl_entry.posting_date <= to_date)
		# CHANGED: no `.force_index("posting_date_company_index")` -- that hint made every root walk the whole year

		if ignore_opening_entries and not ignore_is_opening:
			query = query.where(gl_entry.is_opening == "No")
	else:
		query = query.select(gl_entry.closing_date.as_("posting_date"))
		query = query.where(gl_entry.period_closing_voucher == period_closing_voucher)

	query = apply_additional_conditions(doctype, query, from_date, ignore_closing_entries, filters)

	if (root_lft and root_rgt) or root_type:
		# CHANGED: the root's accounts by name, not an EXISTS(select ... from tabAccount) test per GL row
		query = query.where(gl_entry.account.isin(accounts_under(root_lft, root_rgt, root_type) or [""]))

	query, params = query.walk()
	match_conditions = build_match_conditions(doctype)

	if match_conditions:
		query += "and" + match_conditions

	if group_by_account:
		query += " GROUP BY `account`"
	elif doctype == "GL Entry":
		# CHANGED: the forced index also handed the rows over in posting_date order, then by name (the primary key ends every
		# secondary-index entry), and the statements add the rows up in the order they read them: another order moves a
		# floating-point sum in its last digits (a Balance Sheet by Date Range showed an opening balance 3e-9 apart). Say it.
		query += " ORDER BY `posting_date`, `name`"

	return frappe.db.sql(query, params, as_dict=True)


def get_accounting_entries(*args, **kwargs):
	"""`financial_statements.get_accounting_entries`: the faster read where it is on, ERPNext's own otherwise."""
	if is_enabled():
		return fast_get_accounting_entries(*args, **kwargs)
	return STOCK["get_accounting_entries"](*args, **kwargs)


# ---------------------------------------------------------------------------------------------------------------
# 2. Cash Flow: every period of an account type from one daily read
# ---------------------------------------------------------------------------------------------------------------


def account_type_days(company, account_type, book_condition, params) -> list:
	"""[(day, credit - debit)] of one account type, as exact decimals, for the whole ledger of the company.

	Read once per report run and kept on frappe.local, which is cleared after the request or job: a later report
	reads the ledger afresh. `book_condition` and `params` carry the finance-book and cost-centre conditions of the
	stock query; they are part of the key, so a different filter never reuses another one's days.
	"""
	cache = getattr(frappe.local, "sf_cashflow_days", None)
	if cache is None:
		cache = frappe.local.sf_cashflow_days = {}
	key = (
		company,
		account_type,
		book_condition,
		cstr(params.get("book")),
		cstr(params.get("company_book")),
		tuple(sorted(params.get("cost_center") or ())),
	)
	if key not in cache:
		query = (
			"select posting_date, cast(sum(credit) - sum(debit) as char) from `tabGL Entry` "
			"where company = %(company)s and voucher_type != 'Period Closing Voucher' "
			"and account in (select name from `tabAccount` where account_type = %(account_type)s)"
			+ book_condition
			+ " group by posting_date"
		)
		cache[key] = [(day, Decimal(amount)) for day, amount in frappe.db.sql(query, params) if amount is not None]
	return cache[key]


def fast_get_account_type_based_gl_data(company, filters=None):
	"""`cash_flow.get_account_type_based_gl_data` (erpnext 15.116): the same sum, taken from the daily read."""
	from erpnext.accounts.report.financial_statements import get_cost_centers_with_children

	filters = frappe._dict(filters or {})
	if not (filters.start_date and filters.end_date):
		# the stock query compares the dates as given; anything unusual goes to it
		return STOCK["get_account_type_based_gl_data"](company, filters)

	# the stock finance-book condition; a missing book is '' in both (the stock function escapes None to '')
	params = {"company": company, "account_type": filters.account_type, "book": cstr(filters.finance_book)}
	if filters.include_default_book_entries:
		params["company_book"] = cstr(frappe.get_cached_value("Company", company, "default_finance_book"))
		condition = " and (finance_book in (%(book)s, %(company_book)s, '') or finance_book is null)"
	else:
		condition = " and (finance_book in (%(book)s, '') or finance_book is null)"

	if filters.get("cost_center"):
		params["cost_center"] = get_cost_centers_with_children(filters.cost_center)
		condition += " and cost_center in %(cost_center)s"

	start, end = getdate(filters.start_date), getdate(filters.end_date)
	total = sum(
		(
			amount
			for day, amount in account_type_days(company, filters.account_type, condition, params)
			if start <= day <= end
		),
		Decimal(0),
	)
	# the stock function answers 0 (an int) for no rows and for a sum of exactly zero
	return float(total) if total else 0


def get_account_type_based_gl_data(company, filters=None):
	"""`cash_flow.get_account_type_based_gl_data`: the daily read where it is on, ERPNext's own otherwise."""
	if is_enabled():
		return fast_get_account_type_based_gl_data(company, filters)
	return STOCK["get_account_type_based_gl_data"](company, filters)


# ---------------------------------------------------------------------------------------------------------------
# installation
# ---------------------------------------------------------------------------------------------------------------


def install(*args, **kwargs) -> None:
	"""Point the two ERPNext functions at the dispatchers above. Once per process; cheap enough for `before_request`.

	A function whose source is not the release the copies were written against is left alone and named in
	logs/sf_trading.log, so an upgrade can never leave a stale copy answering for it.
	"""
	if _STATE["tried"]:
		return
	_STATE["tried"] = True

	try:
		from erpnext.accounts.report import financial_statements
		from erpnext.accounts.report.cash_flow import cash_flow
	except ImportError:
		return

	for module, name, replacement in (
		(financial_statements, "get_accounting_entries", get_accounting_entries),
		(cash_flow, "get_account_type_based_gl_data", get_account_type_based_gl_data),
	):
		stock = getattr(module, name, None)
		if stock is None or stock is replacement:
			continue
		if fingerprint(stock) not in STOCK_FINGERPRINTS[name]:
			frappe.logger("sf_trading").warning(
				"report_speed: %s.%s is not the erpnext source this was written against (sha1 %s); left as it is",
				module.__name__,
				name,
				fingerprint(stock) or "unreadable",
			)
			continue
		STOCK[name] = stock
		setattr(module, name, replacement)
