# sf_trading/cash_flow_statement.py
"""The Cash Flow Statement, by the direct and by the indirect method, read from the General Ledger.

Why a new report: ERPNext's own Cash Flow (and periodic_accounting's Standard Cash Flow Report) are
indirect only and sum whatever accounts a mapping names, so an account nobody mapped is simply missing
and the statement stops tying to the bank (on sft-uat they showed -273,482 where the cash and bank
accounts really moved +38,858). This one cannot lose an account.

The identity it is built on. Every voucher balances, so over any set of GL entries

    movement of cash and bank  =  sum over every OTHER account of (credit - debit)

Each non-cash account's (credit - debit) is therefore a piece of the cash flow, and the whole statement
is only a matter of naming the piece. Every non-cash account is given ONE line (`classify`), the lines are
laid out as the direct statement (cash from customers, to suppliers, for expenses, interest, taxes, then
investing and financing) or the indirect one (net income, non-cash adjustments, working capital, investing,
financing), and both layouts use the same lines, so both add up to the same total and the total adds up
to the real movement of the cash and bank accounts. The report shows that check; it is not assumed.

Rules that keep it honest:

* **Opening entries are balances, not flows.** GL entries marked `is_opening` and Period Closing Vouchers
  are left out of the flows; opening entries dated in the period count towards the opening cash instead
  (the Trial Balance does the same).
* **A line comes from the account**: its account type first, then its root type and name, and a row on
  SF Trading Settings > Cash Flow Statement overrides any of it, for a single account or a whole group.
  An account no rule can place goes to "Accrued and other liabilities" or "Prepaid and other current
  assets" by its root type, and the statement still adds up.
* **Filters.** Branch and cost centre narrow every GL row the same way; a voucher spread over several cost
  centres then no longer balances inside one, and the statement says so in its difference row. The other
  filters (dates, method, periodicity) cannot break the check.

Drill-down: every figure opens the General Ledger for the accounts behind it and the dates of its column
(`public/js/cash_flow_statement.js`); lines open into their accounts, biggest first.
"""

import re
from collections import defaultdict
from datetime import date

import frappe
from frappe import _
from frappe.utils import add_days, add_months, cint, flt, get_first_day, get_last_day, getdate

from sf_trading import cash_flow as money

SETTINGS = "SF Trading Settings"
MAP_FIELD = "cash_flow_account_map"
MAP_TABLE = "SF Cash Flow Account Map"

CASH = "CASH"

#: every line a non-cash account can be given: key -> (label, kind). kind "pl" is a profit and loss account,
#: "bs" a balance sheet one. The amount of a line is always (credit - debit): income and a rise in a
#: liability add to cash, expense and a rise in an asset take from it.
LINES = {
	"SALES": ("Sales revenue", "pl"),
	"OTHER_INCOME": ("Other operating income", "pl"),
	"COGS": ("Cost of goods sold", "pl"),
	"VALUATION": ("Landed costs capitalised into stock", "pl"),
	"STOCK_ADJ": ("Stock adjustments and write-downs", "pl"),
	"OPEX": ("Operating expenses", "pl"),
	"DEPRECIATION": ("Depreciation expense", "pl"),
	"AMORTIZATION": ("Amortisation expense", "pl"),
	"SHARE_COMP": ("Share-based compensation expense", "pl"),
	"INTEREST_EXP": ("Interest expense", "pl"),
	"TAX_EXP": ("Income tax expense", "pl"),
	"DISPOSAL": ("Gain / loss on disposal of assets", "pl"),
	"AR": ("Accounts receivable", "bs"),
	"DEFERRED_REV": ("Deferred revenue and customer advances", "bs"),
	"INVENTORY": ("Inventory", "bs"),
	"AP": ("Accounts payable", "bs"),
	"PREPAID": ("Prepaid expenses and other current assets", "bs"),
	"ACCRUED": ("Accrued and other liabilities", "bs"),
	"INTEREST_BS": ("Accrued interest", "bs"),
	"TAX_BS": ("Taxes payable / receivable (VAT and others)", "bs"),
	"PROVISIONS": ("Provisions", "bs"),
	"DEFERRED_TAX": ("Deferred taxes", "bs"),
	"ACC_DEP": ("Accumulated depreciation", "bs"),
	"ACC_AMORT": ("Accumulated amortisation", "bs"),
	"SHARE_RESERVE": ("Share-based compensation reserve", "bs"),
	"FIXED_ASSETS": ("Property, plant and equipment", "bs"),
	"INVESTMENTS": ("Investments", "bs"),
	"LOANS": ("Loans and borrowings", "bs"),
	"EQUITY": ("Owners' equity (capital, drawings, dividends)", "bs"),
}
CASH_LABEL = "Cash and cash equivalents"
#: the labels offered on the settings override, and back
LABEL_TO_KEY = {label: key for key, (label, _kind) in LINES.items()}
LABEL_TO_KEY[CASH_LABEL] = CASH
PL_KEYS = tuple(key for key, (_label, kind) in LINES.items() if kind == "pl")

ACCOUNT_TYPE_LINE = {
	"Receivable": "AR",
	"Stock Delivered But Not Billed": "AR",
	"Payable": "AP",
	"Stock Received But Not Billed": "AP",
	"Asset Received But Not Billed": "AP",
	"Stock": "INVENTORY",
	"Stock Billed But Not Delivered": "DEFERRED_REV",
	"Fixed Asset": "FIXED_ASSETS",
	"Capital Work in Progress": "FIXED_ASSETS",
	"Accumulated Depreciation": "ACC_DEP",
	"Depreciation": "DEPRECIATION",
	"Cost of Goods Sold": "COGS",
	"Expenses Included In Valuation": "VALUATION",
	"Stock Adjustment": "STOCK_ADJ",
	"Temporary": "PREPAID",
	"Current Asset": "PREPAID",
	"Current Liability": "ACCRUED",
	"Income Account": "SALES",
}

#: name rules, tried in this order within a root type; the first hit wins. Lower-cased account names.
KEYWORDS = {
	"Asset": (
		("ACC_AMORT", r"accumulated amorti[sz]ation"),
		("ACC_DEP", r"accumulated depreciation"),
		("INVESTMENTS", r"\binvestment"),
		("FIXED_ASSETS", r"fixed asset|property|plant|equipment|vehicle|furniture|building|\bland\b|software|intangible|goodwill"),
		("INTEREST_BS", r"interest receivable|accrued interest"),
		("TAX_BS", r"\bvat\b|\btax\b|input tax"),
		("INVENTORY", r"inventory|\bstock\b"),
		("AR", r"receivable|debtor"),
	),
	"Liability": (
		("LOANS", r"loan|borrowing|overdraft|debenture|finance lease|bank facility|term finance|murabaha|tawarruq|ijara"),
		("INTEREST_BS", r"interest"),
		("DEFERRED_TAX", r"deferred tax"),
		("PROVISIONS", r"provision"),
		("DEFERRED_REV", r"deferred revenue|deferred income|unearned|advance received|advances? from customer|customer advance"),
		("TAX_BS", r"\bvat\b|\btax\b|zakat|\bduty\b"),
	),
	"Equity": (("SHARE_RESERVE", r"share[- ]based|stock option|\besop\b"),),
	"Income": (("OTHER_INCOME", r".*"),),
	"Expense": (
		("OTHER_INCOME", r"other income|interest income|discount received"),
		("DISPOSAL", r"disposal|gain on sale|loss on sale|sale of asset|profit on sale"),
		("INTEREST_EXP", r"interest|finance cost|finance charge"),
		("TAX_EXP", r"income tax|corporate tax|zakat|tax expense|taxation"),
		("AMORTIZATION", r"amorti[sz]ation"),
		("DEPRECIATION", r"depreciation"),
		("SHARE_COMP", r"share[- ]based|stock option|\besop\b"),
	),
}
ROOT_DEFAULT = {"Asset": "PREPAID", "Liability": "ACCRUED", "Equity": "EQUITY", "Income": "OTHER_INCOME", "Expense": "OPEX"}

# ─── the two statements, from the same lines ──────────────────────────────────────────────────
#
# A display line is (key, label, members): `members` are (line key, multiplier) pairs. An indirect line may
# reverse a P&L line (-1) because it was added back, and the line it was added back against is corrected
# by the same amount, so nothing is counted twice and nothing is lost (see `test_every_line_counts_once`).

D = "direct"
I = "indirect"


def L(key, label, *members):
	return {"key": key, "label": label, "members": [(m, 1) if isinstance(m, str) else m for m in members]}


DIRECT_SECTIONS = [
	{
		"key": "OPERATING",
		"label": "A. Cash flows from operating activities",
		"total": "Net cash from operating activities",
		"groups": [
			{"key": "CUSTOMERS", "label": "Cash from customers", "lines": [
				L("SALES", "Sales revenue", "SALES"),
				L("AR", "Change in accounts receivable", "AR"),
				L("DEFERRED_REV", "Change in deferred revenue and customer advances", "DEFERRED_REV"),
			]},
			{"key": "SUPPLIERS", "label": "Cash to suppliers", "lines": [
				L("COGS", "Cost of goods sold", "COGS"),
				L("VALUATION", "Landed costs capitalised into stock", "VALUATION"),
				L("STOCK_ADJ", "Stock adjustments and write-downs", "STOCK_ADJ"),
				L("INVENTORY", "Change in inventory", "INVENTORY"),
				L("AP", "Change in accounts payable", "AP"),
			]},
			{"key": "EXPENSES", "label": "Cash for operating expenses", "lines": [
				L("OPEX", "Operating expenses", "OPEX"),
				L("OTHER_INCOME", "Other operating income", "OTHER_INCOME"),
				L("PREPAID", "Change in prepaid expenses and other current assets", "PREPAID"),
				L("ACCRUED", "Change in accrued and other liabilities", "ACCRUED"),
				L("PROVISIONS", "Change in provisions and deferred taxes", "PROVISIONS", "DEFERRED_TAX"),
				L("NONCASH", "Depreciation, amortisation and other non-cash items (net)",
					"DEPRECIATION", "AMORTIZATION", "ACC_DEP", "ACC_AMORT", "SHARE_COMP", "SHARE_RESERVE"),
			]},
			{"key": "INTEREST", "label": "Cash for interest", "lines": [
				L("INTEREST_EXP", "Interest expense", "INTEREST_EXP"),
				L("INTEREST_BS", "Change in accrued interest", "INTEREST_BS"),
			]},
			{"key": "TAXES", "label": "Cash for taxes", "lines": [
				L("TAX_EXP", "Income tax expense", "TAX_EXP"),
				L("TAX_BS", "Change in VAT and other tax accounts", "TAX_BS"),
			]},
		],
	},
	{
		"key": "INVESTING",
		"label": "B. Cash flows from investing activities",
		"total": "Net cash from investing activities",
		"lines": [
			L("FIXED_ASSETS", "Purchase and sale of property, plant and equipment", "FIXED_ASSETS"),
			L("INVESTMENTS", "Purchase and sale of investments", "INVESTMENTS"),
			L("DISPOSAL", "Gain / loss on disposal of assets", "DISPOSAL"),
		],
	},
	{
		"key": "FINANCING",
		"label": "C. Cash flows from financing activities",
		"total": "Net cash from financing activities",
		"lines": [
			L("LOANS", "Loans and borrowings", "LOANS"),
			L("EQUITY", "Capital introduced and drawings", "EQUITY"),
		],
	},
]

INDIRECT_SECTIONS = [
	{
		"key": "OPERATING",
		"label": "A. Cash flows from operating activities",
		"total": "Net cash from operating activities",
		"groups": [
			{"key": "NET_INCOME", "label": "Net income", "lines": [
				L(key, LINES[key][0], key) for key in (
					"SALES", "OTHER_INCOME", "COGS", "VALUATION", "STOCK_ADJ", "OPEX", "DEPRECIATION", "AMORTIZATION",
					"SHARE_COMP", "INTEREST_EXP", "TAX_EXP", "DISPOSAL")
			]},
			{"key": "ADJUSTMENTS", "label": "Adjustments for non-cash items", "lines": [
				L("ACC_DEP", "Depreciation", "ACC_DEP"),
				L("ACC_AMORT", "Amortisation", "ACC_AMORT"),
				L("ADJ_DISPOSAL", "Loss / (gain) on disposal of assets", ("DISPOSAL", -1)),
				L("SHARE_RESERVE", "Share-based compensation", "SHARE_RESERVE"),
				L("DEFERRED_TAX", "Deferred taxes", "DEFERRED_TAX"),
				L("ADJ_STOCK", "Inventory write-downs", ("STOCK_ADJ", -1)),
				L("PROVISIONS", "Provisions for future liabilities", "PROVISIONS"),
			]},
			{"key": "WORKING_CAPITAL", "label": "Changes in working capital", "lines": [
				L("AR", "Accounts receivable", "AR"),
				L("INVENTORY", "Inventory (before write-downs)", "INVENTORY", "STOCK_ADJ"),
				L("PREPAID", "Prepaid expenses and other current assets", "PREPAID"),
				L("AP", "Accounts payable", "AP"),
				L("ACCRUED", "Accrued and other liabilities", "ACCRUED"),
				L("DEFERRED_REV", "Deferred revenue and customer advances", "DEFERRED_REV"),
				L("INTEREST_BS", "Accrued interest", "INTEREST_BS"),
				L("TAX_BS", "Taxes payable / receivable (VAT and others)", "TAX_BS"),
			]},
		],
	},
	{
		"key": "INVESTING",
		"label": "B. Cash flows from investing activities",
		"total": "Net cash from investing activities",
		"lines": [
			L("FIXED_ASSETS", "Purchase and sale of property, plant and equipment", "FIXED_ASSETS"),
			L("INVESTMENTS", "Purchase and sale of investments", "INVESTMENTS"),
			L("DISPOSAL", "Proceeds from disposal of assets (including the gain or loss)", "DISPOSAL"),
		],
	},
	{
		"key": "FINANCING",
		"label": "C. Cash flows from financing activities",
		"total": "Net cash from financing activities",
		"lines": [
			L("LOANS", "Loans and borrowings", "LOANS"),
			L("EQUITY", "Capital introduced and drawings", "EQUITY"),
		],
	},
]

SECTIONS = {D: DIRECT_SECTIONS, I: INDIRECT_SECTIONS}
METHODS = {"Direct": D, "Indirect": I}
SHOW_ACCOUNTS = ("None", "Top 15 and others", "All")
TOP_ACCOUNTS = 15
ACCOUNT_BATCH = 500


def display_lines(method):
	"""Every display line of a statement, in order."""
	out = []
	for section in SECTIONS[method]:
		for group in section.get("groups") or [section]:
			out += group["lines"]
	return out


# ─── which line is an account? ─────────────────────────────────────────────────────────────────


def classify(account, overrides=None, named=None) -> str:
	"""The line key of a non-group account, or CASH. `account` has name, root_type and account_type; `overrides`
	({account: key}, from the settings) and `named` (the company's own disposal / depreciation accounts) win
	over every rule; then the account type, then the root type and the words in the name."""
	name = account.get("name")
	for table in (overrides, named):
		if table and name in table:
			return table[name]
	account_type, root = account.get("account_type") or "", account.get("root_type") or ""
	if account_type in ("Cash", "Bank"):
		return CASH
	text = re.sub(r"^[\d\s.\-]+", "", (name or "").lower())
	if account_type == "Tax":
		return "TAX_EXP" if root == "Expense" else ("OTHER_INCOME" if root == "Income" else "TAX_BS")
	if account_type == "Current Liability":
		# a typed Current Liability may still be a loan, a provision or a customer advance
		return keyword_line("Liability", text) or "ACCRUED"
	if account_type in ACCOUNT_TYPE_LINE:
		return ACCOUNT_TYPE_LINE[account_type]
	return keyword_line(root, text) or ROOT_DEFAULT.get(root, "ACCRUED")


def keyword_line(root, text):
	for key, pattern in KEYWORDS.get(root, ()):
		if re.search(pattern, text):
			return key
	return None


def line_overrides(company) -> dict:
	"""{account: line key} from the settings rows, a group's row applying to everything under it unless a
	deeper row names the account (or a nearer group) itself."""
	if not frappe.db.table_exists(MAP_TABLE):
		return {}
	rows = frappe.get_all(
		MAP_TABLE,
		filters={"parent": SETTINGS, "parenttype": SETTINGS, "parentfield": MAP_FIELD},
		fields=["account", "line"],
		order_by="idx asc",
	)
	picked = {}
	for row in rows:
		key = LABEL_TO_KEY.get(row.line)
		if not row.account or not key:
			continue
		bounds = frappe.db.get_value("Account", row.account, ["lft", "rgt", "company"], as_dict=True)
		if bounds and bounds.company == company:
			picked[row.account] = (key, bounds.lft, bounds.rgt)
	if not picked:
		return {}
	out = {}
	for acc in frappe.get_all("Account", filters={"company": company, "is_group": 0}, fields=["name", "lft", "rgt"]):
		best = None
		for key, lft, rgt in picked.values():
			if lft <= acc.lft and acc.rgt <= rgt and (best is None or rgt - lft < best[2] - best[1]):
				best = (key, lft, rgt)
		if best:
			out[acc.name] = best[0]
	return out


def company_account_lines(company) -> dict:
	"""Accounts the company itself names for a purpose (disposal gain / loss, depreciation expense)."""
	fields = {"disposal_account": "DISPOSAL", "depreciation_expense_account": "DEPRECIATION"}
	have = frappe.db.get_value("Company", company, list(fields), as_dict=True) or {}
	return {have[f]: key for f, key in fields.items() if have.get(f)}


def load_accounts(company) -> tuple:
	rows = frappe.get_all(
		"Account", filters={"company": company, "is_group": 0}, fields=["name", "root_type", "account_type"], limit_page_length=0
	)
	over, named = line_overrides(company), company_account_lines(company)
	return {a.name: classify(a, over, named) for a in rows}, {a.name: a for a in rows}


# ─── periods ───────────────────────────────────────────────────────────────────────────────────

PERIODICITY = ("Total", "Monthly", "Quarterly", "Yearly")


def period_list(from_date, to_date, periodicity="Total") -> list:
	"""[{label, from, to}] covering [from_date, to_date] exactly, cut at month / quarter / year ends."""
	from_date, to_date = getdate(from_date), getdate(to_date)
	if periodicity not in ("Monthly", "Quarterly", "Yearly"):
		return [{"label": _("Amount"), "from": from_date, "to": to_date}]
	step = {"Monthly": 1, "Quarterly": 3, "Yearly": 12}[periodicity]
	out, start = [], from_date
	while start <= to_date:
		first_month = 1 if periodicity == "Yearly" else start.month - ((start.month - 1) % step)
		first = date(start.year, first_month, 1)
		end = min(get_last_day(add_months(first, step - 1)), to_date)
		out.append({"label": period_label(periodicity, first), "from": start, "to": end})
		start = add_days(end, 1)
	return out


def period_label(periodicity, first):
	if periodicity == "Monthly":
		return first.strftime("%b %Y")
	if periodicity == "Quarterly":
		return "Q%d %d" % ((first.month - 1) // 3 + 1, first.year)
	return str(first.year)


def period_index(periods, month_key) -> int:
	"""Which period holds a 'YYYY-MM' GL bucket (periods are cut on month ends, so exactly one does)."""
	first = getdate(month_key + "-01")
	for i, p in enumerate(periods):
		if get_first_day(p["from"]) <= first <= p["to"]:
			return i
	return len(periods) - 1


# ─── the ledger ────────────────────────────────────────────────────────────────────────────────

FLOWS = (
	"select gle.account, date_format(gle.posting_date, '%%Y-%%m') as month, sum(gle.credit - gle.debit) as net "
	"from `tabGL Entry` gle where gle.company = %(company)s and gle.is_cancelled = 0 "
	"and gle.posting_date between %(from_date)s and %(to_date)s "
	"and ifnull(gle.is_opening, 'No') != 'Yes' and gle.voucher_type != 'Period Closing Voucher'"
)
FLOWS_GROUP = " group by gle.account, month"
OPENING = (
	"select sum(gle.debit - gle.credit) from `tabGL Entry` gle where gle.company = %(company)s and gle.is_cancelled = 0 "
	"and gle.account in %(cash)s and (gle.posting_date < %(from_date)s or (ifnull(gle.is_opening, 'No') = 'Yes' "
	"and gle.posting_date between %(from_date)s and %(to_date)s))"
)
BALANCE = (
	"select sum(gle.debit - gle.credit) from `tabGL Entry` gle where gle.company = %(company)s and gle.is_cancelled = 0 "
	"and gle.account in %(cash)s and gle.posting_date <= %(to_date)s"
)


def fetch_flows(filters, scope_sql, scope_params):
	params = dict(scope_params, company=filters.company, from_date=filters.from_date, to_date=filters.to_date)
	return frappe.db.sql(FLOWS + scope_sql + FLOWS_GROUP, params)


def fetch_opening_cash(filters, cash, scope_sql, scope_params) -> float:
	if not cash:
		return 0.0
	params = dict(scope_params, company=filters.company, from_date=filters.from_date, to_date=filters.to_date, cash=tuple(cash))
	return flt(frappe.db.sql(OPENING + scope_sql, params)[0][0])


def fetch_closing_cash(filters, cash, scope_sql, scope_params) -> float:
	"""The cash and bank balance at the end date, read on its own -- the check the statement is held to."""
	if not cash:
		return 0.0
	params = dict(scope_params, company=filters.company, to_date=filters.to_date, cash=tuple(cash))
	return flt(frappe.db.sql(BALANCE + scope_sql, params)[0][0])


# ─── the model: amounts per line, per account, per period ───────────────────────────────────────


def aggregate(flows, line_of, periods) -> dict:
	"""flows: (account, 'YYYY-MM', credit - debit). Returns
	{"lines": {key: {account: [amount per period]}}, "cash": [cash movement per period]}.
	An account the chart no longer holds is placed by its root type's default, so it is never dropped."""
	n = len(periods)
	lines = defaultdict(lambda: defaultdict(lambda: [0.0] * n))
	cash = [0.0] * n
	for account, month, net in flows:
		i = period_index(periods, month)
		key = line_of.get(account, "ACCRUED")
		if key == CASH:
			cash[i] -= flt(net)
		else:
			lines[key][account][i] += flt(net)
	return {"lines": lines, "cash": cash}


def line_total(model, key, i):
	return sum(vals[i] for vals in model["lines"].get(key, {}).values())


def display_amount(model, line, i) -> float:
	return sum(mult * line_total(model, key, i) for key, mult in line["members"])


def display_accounts(model, line) -> dict:
	"""{account: [amount per period]} behind a display line, signed as the line shows them."""
	n = len(model["cash"])
	out = defaultdict(lambda: [0.0] * n)
	for key, mult in line["members"]:
		for account, vals in model["lines"].get(key, {}).items():
			for i in range(n):
				out[account][i] += mult * vals[i]
	return out


def statement_total(model, method, i) -> float:
	return sum(display_amount(model, line, i) for line in display_lines(method))


# ─── the rows ───────────────────────────────────────────────────────────────────────────────────


class Rows:
	"""Collects report rows in tree order, each with a number, its indent and what its figure drills into."""

	def __init__(self, periods, currency, show_accounts, show_zero):
		self.periods, self.currency = periods, currency
		self.show_accounts, self.show_zero = show_accounts, show_zero
		self.data = []

	def add(self, label, values, indent, **extra):
		row = {"rid": len(self.data) + 1, "label": label, "indent": indent, "currency": self.currency}
		row.update(extra)
		self.fill(row, values)
		self.data.append(row)
		return row

	def fill(self, row, values):
		if values is None:
			return
		for i, v in enumerate(values):
			row["p%d" % i] = flt(v, 6) if abs(v) >= 5e-7 else 0.0
		if len(self.periods) > 1:
			total = sum(values)
			row["total"] = flt(total, 6) if abs(total) >= 5e-7 else 0.0


def period_ranges(periods) -> dict:
	"""The dates behind each money column, for the drill-down: p0.. and, with several periods, the total."""
	out = {"p%d" % i: {"from_date": str(p["from"]), "to_date": str(p["to"])} for i, p in enumerate(periods)}
	if len(periods) > 1:
		out["total"] = {"from_date": str(periods[0]["from"]), "to_date": str(periods[-1]["to"])}
	return out


def zero(values):
	return all(abs(v) < 5e-7 for v in values)


def statement_rows(model, method, rows: Rows, opening):
	"""The statement for one method. Returns {section key: [per-period totals]} for the summary."""
	n = len(rows.periods)
	totals = {}
	grand = [0.0] * n
	for number, section in enumerate(SECTIONS[method]):
		head = rows.add(_(section["label"]), None, 0, is_header=1, bold=1)
		if number == 0:
			head["periods"] = period_ranges(rows.periods)
		section_total = [0.0] * n
		section_rids = []
		for group in section.get("groups") or [None]:
			holder = group or section
			lines = [ln for ln in holder["lines"] if rows.show_zero or not zero([display_amount(model, ln, i) for i in range(n)])]
			if not lines and group is None:
				continue
			group_vals = [sum(display_amount(model, ln, i) for ln in holder["lines"]) for i in range(n)]
			indent = 1
			group_row = None
			if group:
				if not lines and not rows.show_zero and zero(group_vals):
					continue
				group_row = rows.add(_(group["label"]), group_vals, 1, bold=1, is_group=1, lines=[])
				indent = 2
			for line in lines:
				vals = [display_amount(model, line, i) for i in range(n)]
				line_row = rows.add(_(line["label"]), vals, indent, is_line=1, accounts=[])
				accts = display_accounts(model, line)
				line_row["accounts"] = sorted(a for a, v in accts.items() if not zero(v))
				if group_row is not None:
					group_row["lines"].append(line_row["rid"])
				section_rids.append(line_row["rid"])
				if rows.show_accounts != "None":
					account_rows(rows, accts, indent + 1)
			for i in range(n):
				section_total[i] += group_vals[i]
		total_row = rows.add(_(section["total"]), section_total, 0, bold=1, is_total=1, lines=section_rids)
		head["lines"] = section_rids
		totals[section["key"]] = section_total
		for i in range(n):
			grand[i] += section_total[i]
	rows.add(_("Net increase / (decrease) in cash and bank"), grand, 0, bold=1, is_total=1, lines=[r["rid"] for r in rows.data if r.get("is_line")])
	cash_begin, cash_end, cash_ledger = [], [], []
	running = opening
	for i in range(n):
		cash_begin.append(running)
		running += grand[i]
		cash_end.append(running)
	rows.add(_("Cash and bank at the beginning of the period"), cash_begin, 0)
	rows.add(_("Cash and bank at the end of the period"), cash_end, 0, bold=1, is_total=1)
	actual_run = opening
	for i in range(n):
		actual_run += model["cash"][i]
		cash_ledger.append(actual_run)
	rows.add(_("Cash and bank at the end of the period, per the ledger"), cash_ledger, 0)
	diff = [cash_end[i] - cash_ledger[i] for i in range(n)]
	rows.add(_("Difference (the statement must tie to the ledger)"), diff, 0, bold=1, is_difference=1)
	return {"totals": totals, "grand": grand, "difference": diff, "end": cash_end,
		"end_ledger": cash_ledger[-1] if cash_ledger else opening}


def account_rows(rows: Rows, accts, indent):
	"""The accounts under a line, biggest first; with 'Top 15' the rest become one row that drills into them."""
	live = [(a, v) for a, v in accts.items() if not zero(v)]
	live.sort(key=lambda kv: (-sum(abs(x) for x in kv[1]), kv[0]))
	shown, rest = (live, []) if rows.show_accounts == "All" else (live[:TOP_ACCOUNTS], live[TOP_ACCOUNTS:])
	for account, vals in shown:
		rows.add(account, vals, indent, is_account=1, accounts=[account])
	if rest:
		n = len(rows.periods)
		pooled = [sum(v[i] for _a, v in rest) for i in range(n)]
		rows.add(_("Other accounts (%s)") % len(rest), pooled, indent, is_account=1, accounts=sorted(a for a, _v in rest))


# ─── the report ──────────────────────────────────────────────────────────────────────────────────


def default_from(to_date):
	return date(to_date.year, 1, 1)


def validate(filters):
	if not filters.get("company"):
		frappe.throw(_("Choose a company."))
	frappe.has_permission("Company", "read", filters.company, throw=True)
	method = filters.get("method") or "Direct"
	if method not in METHODS:
		frappe.throw(_("Method must be Direct or Indirect."))
	if (filters.get("periodicity") or "Total") not in PERIODICITY:
		frappe.throw(_("Periodicity must be one of: %s.") % ", ".join(PERIODICITY))
	if (filters.get("show_accounts") or SHOW_ACCOUNTS[1]) not in SHOW_ACCOUNTS:
		frappe.throw(_("Accounts under each line must be one of: %s.") % ", ".join(SHOW_ACCOUNTS))
	filters.from_date, filters.to_date = money.as_dates(filters, default_from)


def build(filters) -> dict:
	"""Everything the report shows, from the filters: model, rows and the numbers the checks use."""
	validate(filters)
	method = METHODS[filters.get("method") or "Direct"]
	periods = period_list(filters.from_date, filters.to_date, filters.get("periodicity") or "Total")
	line_of, meta = load_accounts(filters.company)
	scope_sql, scope_params = money.scope_params(filters)
	cash = [a for a, key in line_of.items() if key == CASH]
	flows = fetch_flows(filters, scope_sql, scope_params)
	model = aggregate(flows, line_of, periods)
	opening = fetch_opening_cash(filters, cash, scope_sql, scope_params)
	currency = frappe.get_cached_value("Company", filters.company, "default_currency")
	rows = Rows(periods, currency, filters.get("show_accounts") or SHOW_ACCOUNTS[1], cint(filters.get("show_zero_lines")))
	result = statement_rows(model, method, rows, opening)
	result.update(model=model, rows=rows, periods=periods, opening=opening, currency=currency, method=method,
		closing_ledger=fetch_closing_cash(filters, cash, scope_sql, scope_params), scoped=bool(scope_sql))
	return result


def columns(periods, currency):
	cols = [{"label": _("Particulars"), "fieldname": "label", "fieldtype": "Data", "width": 430}]
	for i, p in enumerate(periods):
		cols.append({"label": p["label"], "fieldname": "p%d" % i, "fieldtype": "Currency", "options": "currency", "width": 150})
	if len(periods) > 1:
		cols.append({"label": _("Total"), "fieldname": "total", "fieldtype": "Currency", "options": "currency", "width": 160})
	cols.append({"label": "", "fieldname": "currency", "fieldtype": "Link", "options": "Currency", "hidden": 1})
	return cols


def report_summary(result):
	cur, t = result["currency"], result["totals"]
	def total(values):
		return sum(values)
	cards = []
	for key, label in (("OPERATING", _("Operating")), ("INVESTING", _("Investing")), ("FINANCING", _("Financing"))):
		value = total(t[key])
		cards.append({"label": label, "value": flt(value, 3), "datatype": "Currency", "currency": cur,
			"indicator": "Green" if value >= 0 else "Red"})
	net = total(result["grand"])
	cards.append({"label": _("Net change in cash"), "value": flt(net, 3), "datatype": "Currency", "currency": cur,
		"indicator": "Green" if net >= 0 else "Red"})
	return cards


def chart(result):
	t, periods = result["totals"], result["periods"]
	if len(periods) < 2:
		return None
	return {
		"data": {
			"labels": [p["label"] for p in periods],
			"datasets": [
				{"name": _("Operating"), "values": [flt(v, 3) for v in t["OPERATING"]]},
				{"name": _("Investing"), "values": [flt(v, 3) for v in t["INVESTING"]]},
				{"name": _("Financing"), "values": [flt(v, 3) for v in t["FINANCING"]]},
			],
		},
		"type": "bar",
	}


def message(result, filters):
	notes = []
	worst = max((abs(d) for d in result["difference"]), default=0)
	if worst >= 0.0005:
		notes.append(
			_("The statement is %s away from the ledger's cash and bank movement. With a branch or cost centre chosen this is "
				"expected when a voucher is spread over several; otherwise some entry is out of balance.") % flt(worst, 3)
		)
	if abs(result["closing_ledger"] - result["end_ledger"]) >= 0.0005:
		notes.append(
			_("The cash and bank balance read on its own at the end date (%s) is not the opening balance plus the period's "
				"movement (%s): the opening balance or an entry is out of step.") % (flt(result["closing_ledger"], 3), flt(result["end_ledger"], 3))
		)
	if result["scoped"]:
		notes.append(_("Filtered by branch or cost centre: the lines are what was posted to it; cash and bank, the same."))
	return "<br>".join(notes) or None


def execute(filters=None):
	filters = frappe._dict(filters or {})
	result = build(filters)
	data = result["rows"].data
	return columns(result["periods"], result["currency"]), data, message(result, filters), chart(result), report_summary(result)
