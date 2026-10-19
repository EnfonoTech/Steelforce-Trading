# sf_trading/mis_profit_loss.py
"""MIS Profit and Loss -- the management view of a company's profit, with alternative cost centres.

What the books say and what management sees differ by exactly one thing here: a voucher can be charged to a cost
centre of ANOTHER company (the Alternative Cost Center dimension, sf_trading/alternative_cost_center.py). So,
for the company the report is run for:

* **Own books** -- the profit and loss lines of the company's own General Ledger.
* **Charged out** -- lines the company posted but charged to a cost centre of another company. They are the
  other company's, not this one's: left out of the figures and shown below them, by company.
* **Charged in** -- lines ANOTHER company posted and charged to a cost centre of this one. They are this
  company's: added to the section they belong to, each as its own line "from <company>: <account>", converted
  at the voucher date's exchange rate into this company's currency.

    MIS profit  =  profit per books  -  charged out  +  charged in

The report shows that sum under the profit, and checks it against the profit above it.

The layout is the management one, each step with its margin on revenue:

    Revenue
    - Cost of sales                                   = Gross profit
    + Other operating income, - Operating expenses    = EBITDA
    - Depreciation and amortisation                   = Operating profit (EBIT)
    - Interest, +/- gain or loss on disposal          = Profit before tax
    - Income tax                                      = Net profit

An account goes to a section by the same rules as the Cash Flow Statement (account type, then root type and
name words, then the override on SF Trading Settings > Cash Flow Statement).

Columns: a period each (total, monthly, quarterly, yearly), or the previous period / the same period last year
beside the current one with the change, or a cost centre each (an own line by the cost centre it was posted to,
a charged-in line by the cost centre of this company it was charged to). Every figure drills into the vouchers
behind it (MIS Profit and Loss Detail).
"""

from collections import defaultdict
from datetime import date

import frappe
from frappe import _
from frappe.utils import add_days, add_months, cint, flt, get_last_day, getdate

from sf_trading import cash_flow as money
from sf_trading import cash_flow_statement as cfs
from sf_trading.alternative_cost_center import DOCTYPE as ALT_DOCTYPE
from sf_trading.alternative_cost_center import FIELD as ALT_FIELD

# ─── the layout ────────────────────────────────────────────────────────────────────────────────

#: a section of lines, or a subtotal. `lines` are the cash flow statement's line keys; `sign` is what the section
#: shows of a line's (credit - debit): costs are shown as positive costs.
LAYOUT = (
	{"key": "REVENUE", "label": "Revenue", "lines": ("SALES",), "sign": 1},
	{"key": "COST_OF_SALES", "label": "Cost of sales", "lines": ("COGS", "VALUATION", "STOCK_ADJ"), "sign": -1},
	{"key": "GROSS", "label": "Gross profit", "subtotal": True, "margin": True},
	{"key": "OTHER_INCOME", "label": "Other operating income", "lines": ("OTHER_INCOME",), "sign": 1},
	{"key": "OPEX", "label": "Operating expenses", "lines": ("OPEX", "SHARE_COMP"), "sign": -1},
	{"key": "EBITDA", "label": "EBITDA", "subtotal": True, "margin": True},
	{"key": "DA", "label": "Depreciation and amortisation", "lines": ("DEPRECIATION", "AMORTIZATION"), "sign": -1},
	{"key": "EBIT", "label": "Operating profit (EBIT)", "subtotal": True, "margin": True},
	{"key": "INTEREST", "label": "Interest and finance cost", "lines": ("INTEREST_EXP",), "sign": -1},
	{"key": "DISPOSAL", "label": "Gain / (loss) on disposal of assets", "lines": ("DISPOSAL",), "sign": 1},
	{"key": "PBT", "label": "Profit before tax", "subtotal": True, "margin": True},
	{"key": "TAX", "label": "Income tax", "lines": ("TAX_EXP",), "sign": -1},
	{"key": "NET", "label": "Net profit", "subtotal": True, "margin": True},
)
SECTION_OF_LINE = {line: s["key"] for s in LAYOUT if not s.get("subtotal") for line in s["lines"]}
SECTIONS = {s["key"]: s for s in LAYOUT}
ORDER = [s["key"] for s in LAYOUT]
#: the sections a subtotal adds up (everything above it, each with its net (credit - debit))
FIRST_SECTION = {key: ORDER[: i + 1] for i, key in enumerate(ORDER)}

OWN, IN, OUT = "own", "in", "out"
ORIGIN_LABEL = {OWN: "Own books", IN: "Charged in", OUT: "Charged out"}
PERIODICITY = cfs.PERIODICITY
COMPARISONS = ("None", "Previous Period", "Same Period Last Year")
COLUMNS_BY = ("Period", "Cost Center")
DETAIL_LEVELS = ("Sections Only", "Sections and Accounts")
NO_CC = "(no cost centre)"


def section_of(line_key, root_type) -> str:
	"""The section a profit and loss account belongs to; an account the rules put on a balance sheet line still lands."""
	return SECTION_OF_LINE.get(line_key) or ("OTHER_INCOME" if root_type == "Income" else "OPEX")


def section_signs(title) -> dict:
	"""{section key: sign} for a section's title (its own sign) or a subtotal's (+1 on everything it adds up)."""
	wanted = cstr_lower(title)
	for s in LAYOUT:
		if wanted in (s["label"].lower(), cstr_lower(_(s["label"]))):
			if s.get("subtotal"):
				return {key: 1 for key in FIRST_SECTION[s["key"]] if not SECTIONS[key].get("subtotal")}
			return {s["key"]: s["sign"]}
	return {}


def cstr_lower(value):
	return (value or "").strip().lower()


# ─── exchange rates ─────────────────────────────────────────────────────────────────────────────


class Rates:
	"""The rate that turns a source company's currency into this company's, on a date: the latest Currency Exchange
	row on or before it, or the inverse of one. No rate means no conversion is possible; it is listed, not guessed."""

	def __init__(self, to_currency):
		self.to, self.cache, self.missing = to_currency, {}, set()

	def rate(self, from_currency, on) -> float:
		if not from_currency or from_currency == self.to:
			return 1.0
		key = (from_currency, getdate(on))
		if key not in self.cache:
			self.cache[key] = self.lookup(from_currency, self.to, getdate(on))
			if not self.cache[key]:
				self.missing.add(from_currency)
		return self.cache[key]

	@staticmethod
	def lookup(from_currency, to_currency, on) -> float:
		for source, target, invert in ((from_currency, to_currency, False), (to_currency, from_currency, True)):
			rows = frappe.get_all(
				"Currency Exchange",
				filters={"from_currency": source, "to_currency": target, "date": ["<=", on]},
				fields=["exchange_rate"],
				order_by="date desc",
				limit_page_length=1,
			)
			if rows and flt(rows[0].exchange_rate):
				return (1.0 / flt(rows[0].exchange_rate)) if invert else flt(rows[0].exchange_rate)
		return 0.0


# ─── the ledger ─────────────────────────────────────────────────────────────────────────────────

PL_ROOTS = "acc.root_type in ('Income', 'Expense')"
BASE = (
	"from `tabGL Entry` gle inner join `tabAccount` acc on acc.name = gle.account where gle.is_cancelled = 0 "
	"and gle.posting_date between %(from_date)s and %(to_date)s and acc.root_type in ('Income', 'Expense') "
	"and gle.voucher_type != 'Period Closing Voucher'"
)
OWN_ROWS = (
	"select gle.account, gle.posting_date, gle.cost_center, ifnull(gle." + ALT_FIELD + ", '') as alt, "
	"sum(gle.credit - gle.debit) as net " + BASE + " and gle.company = %(company)s"
)
OWN_GROUP = " group by gle.account, gle.posting_date, gle.cost_center, alt"
IN_ROWS = (
	"select gle.company, gle.account, gle.posting_date, gle." + ALT_FIELD + " as alt, sum(gle.credit - gle.debit) as net "
	+ BASE + " and gle.company != %(company)s and gle." + ALT_FIELD + " in %(alts)s"
)
IN_GROUP = " group by gle.company, gle.account, gle.posting_date, alt"


def alternatives() -> dict:
	"""{alternative cost centre: (company, cost centre)} for every mirror."""
	if not frappe.db.table_exists(ALT_DOCTYPE):
		return {}
	return {r.name: (r.company, r.cost_center) for r in frappe.get_all(ALT_DOCTYPE, fields=["name", "company", "cost_center"], limit_page_length=0)}


def has_dimension() -> bool:
	return bool(frappe.db.has_column("GL Entry", ALT_FIELD))


def fetch_own(filters, scope_sql, scope_params):
	params = dict(scope_params, company=filters.company, from_date=filters.range_from, to_date=filters.range_to)
	return frappe.db.sql(OWN_ROWS + scope_sql + OWN_GROUP, params)


def fetch_in(filters, alts, cc_alts):
	"""Rows other companies posted and charged to a cost centre of this one; `cc_alts` narrows them to given cost centres."""
	wanted = [a for a in alts if not cc_alts or a in cc_alts]
	if not wanted:
		return []
	params = dict(company=filters.company, from_date=filters.range_from, to_date=filters.range_to, alts=tuple(wanted))
	return frappe.db.sql(IN_ROWS + IN_GROUP, params)


# ─── accounts ───────────────────────────────────────────────────────────────────────────────────


def pl_accounts(company) -> dict:
	"""{account: {section, label, root}} for a company's profit and loss accounts."""
	line_of, _meta = cfs.load_accounts(company)
	out = {}
	for a in frappe.get_all("Account", filters={"company": company, "is_group": 0, "root_type": ["in", ["Income", "Expense"]]},
			fields=["name", "account_name", "account_number", "root_type"], limit_page_length=0):
		label = ("%s - %s" % (a.account_number, a.account_name)) if a.account_number else a.account_name
		out[a.name] = {"section": section_of(line_of.get(a.name), a.root_type), "label": label or a.name, "root": a.root_type,
			"base": a.account_name or a.name}
	return out


# ─── columns ────────────────────────────────────────────────────────────────────────────────────


class Columns:
	"""What a figure is counted in: a period (a date range), or a cost centre of this company."""

	def __init__(self, kind, items, comparison=False):
		self.kind, self.items, self.comparison = kind, items, comparison  # items: [{"label", "from", "to"}] or [{"label", "key"}]

	def index(self, on, cost_centre):
		if self.kind == "Cost Center":
			key = cost_centre or NO_CC
			for i, item in enumerate(self.items):
				if item["key"] == key:
					return i
			return None
		day = getdate(on)
		for i, item in enumerate(self.items):
			if item["from"] <= day <= item["to"]:
				return i
		return None

	def __len__(self):
		return len(self.items)

	@property
	def span(self):
		if self.kind == "Cost Center":
			return None
		return min(i["from"] for i in self.items), max(i["to"] for i in self.items)


def comparison_range(from_date, to_date, comparison):
	"""The earlier range to set beside the current one."""
	if comparison == "Same Period Last Year":
		return add_months(from_date, -12), add_months(to_date, -12)
	if from_date.day == 1 and to_date == get_last_day(to_date):
		# whole months (a month, a quarter, a year): the same number of months before, not the same number of days
		months = (to_date.year - from_date.year) * 12 + to_date.month - from_date.month + 1
		return add_months(from_date, -months), add_days(from_date, -1)
	days = (to_date - from_date).days + 1
	return add_days(from_date, -days), add_days(from_date, -1)


def period_columns(filters) -> Columns:
	if filters.comparison in ("Previous Period", "Same Period Last Year"):
		prev_from, prev_to = comparison_range(filters.from_date, filters.to_date, filters.comparison)
		return Columns("Period", [
			{"label": "%s to %s" % (filters.from_date, filters.to_date), "from": filters.from_date, "to": filters.to_date},
			{"label": "%s to %s" % (prev_from, prev_to), "from": prev_from, "to": prev_to},
		], comparison=True)
	return Columns("Period", cfs.period_list(filters.from_date, filters.to_date, filters.periodicity))


# ─── the model ──────────────────────────────────────────────────────────────────────────────────


class Model:
	"""Every figure of the report, per column: {section: {line key: line}}, what was charged out, and the profit per books."""

	def __init__(self, columns):
		self.columns, self.n = columns, len(columns)
		self.sections = defaultdict(dict)
		self.out = {}
		self.books = [0.0] * self.n
		self.unconverted = set()

	def line(self, section, key, label, origin, **extra):
		found = self.sections[section].get(key)
		if not found:
			found = self.sections[section][key] = dict(label=label, origin=origin, vals=[0.0] * self.n, **extra)
		return found


def build_model(own_rows, in_rows, accounts, source_accounts, alts, company, columns, rates) -> Model:
	"""own_rows: (account, date, cost centre, alternative, net); in_rows: (company, account, date, alternative, net)."""
	model = Model(columns)
	for account, on, cc, alt, net in own_rows:
		i = columns.index(on, cc)
		meta = accounts.get(account)
		if i is None or not meta:
			continue
		net = flt(net)
		model.books[i] += net
		target = alts.get(alt)
		if alt and target and target[0] != company:
			slot = model.out.setdefault((target[0], account), dict(label=meta["label"], company=target[0], account=account, vals=[0.0] * model.n))
			slot["vals"][i] += net
			continue
		model.line(meta["section"], (OWN, account), meta["label"], OWN, account=account)["vals"][i] += net
	for source, account, on, alt, net in in_rows:
		target = alts.get(alt)
		meta = (source_accounts.get(source) or {}).get(account)
		if not target or target[0] != company or not meta:
			continue
		i = columns.index(on, target[1])
		if i is None:
			continue
		rate = rates.rate(frappe.get_cached_value("Company", source, "default_currency"), on)
		if not rate:
			model.unconverted.add(source)
		abbr = frappe.get_cached_value("Company", source, "abbr") or source
		label = "%s: %s" % (abbr, meta["base"])
		model.line(meta["section"], (IN, source, account), label, IN, account=account, source_company=source)["vals"][i] += flt(net) * rate
	return model


# ─── the rows ───────────────────────────────────────────────────────────────────────────────────


def total_of(vals):
	return sum(vals)


def margin(value, revenue):
	return (value / revenue * 100.0) if revenue else 0.0


class Out:
	"""The report's rows in tree order."""

	def __init__(self, columns, currency, show_percent):
		self.columns, self.currency, self.show_percent = columns, currency, show_percent
		self.data = []

	def add(self, label, vals, indent, **extra):
		row = {"rid": len(self.data) + 1, "label": label, "indent": indent, "currency": self.currency}
		row.update(extra)
		if vals is not None:
			for i, v in enumerate(vals):
				row["p%d" % i] = flt(v, 6) if abs(v) >= 5e-7 else 0.0
			several_periods = self.columns.kind == "Period" and not self.columns.comparison and len(vals) > 1
			if several_periods or self.columns.kind == "Cost Center":
				total = sum(vals)
				row["total"] = flt(total, 6) if abs(total) >= 5e-7 else 0.0
			if self.columns.comparison and len(vals) == 2:
				change = vals[0] - vals[1]
				row["change"] = flt(change, 6)
				row["change_pct"] = flt((change / abs(vals[1]) * 100.0) if vals[1] else 0.0, 3)
		self.data.append(row)
		return row


def statement_rows(model: Model, out: Out, detail: str, show_zero: bool) -> dict:
	"""The profit and loss, section by section, then the bridge to the books. Returns the per-column section totals."""
	n = model.n
	nets = {}  # section key -> per-column net (credit - debit), what subtotals add
	revenue = None
	for spec in LAYOUT:
		key = spec["key"]
		if spec.get("subtotal"):
			vals = [sum(nets[k][i] for k in FIRST_SECTION[key] if k in nets) for i in range(n)]
			row = out.add(_(spec["label"]), vals, 0, bold=1, is_total=1, section=key, drill_title=spec["label"])
			if spec.get("margin"):
				base = revenue or [0.0] * n
				out.add(_("%s margin %%") % _(spec["label"]), [margin(vals[i], base[i]) for i in range(n)], 1, is_ratio=1)
			continue
		lines = model.sections.get(key, {})
		net = [sum(line["vals"][i] for line in lines.values()) for i in range(n)]
		nets[key] = net
		if key == "REVENUE":
			revenue = net
		if not lines and not show_zero:
			continue
		sign = spec["sign"]
		shown = [sign * v for v in net]
		head = out.add(_(spec["label"]), shown, 0, bold=1, is_section=1, section=key, drill_title=spec["label"])
		if detail == "Sections Only":
			continue
		for line_key, line in sorted(lines.items(), key=lambda kv: (kv[1]["origin"] == IN, -sum(abs(v) for v in kv[1]["vals"]), kv[1]["label"])):
			vals = [sign * v for v in line["vals"]]
			if not show_zero and all(abs(v) < 5e-7 for v in vals):
				continue
			prefix = "↳ " if line["origin"] == IN else ""
			out.add(prefix + line["label"], vals, 1, is_line=1, section=key, origin=line["origin"], account=line.get("account"),
				source_company=line.get("source_company"), drill_title=spec["label"])
	return {"nets": nets, "net": nets and [sum(nets[k][i] for k in nets) for i in range(n)]}


def bridge_rows(model: Model, out: Out, company, mis_net):
	"""Profit per books, less what was charged out, plus what was charged in: and a check against the profit above."""
	n = model.n
	books = list(model.books)
	out_total = [sum(s["vals"][i] for s in model.out.values()) for i in range(n)]
	in_total = [sum(line["vals"][i] for sec in model.sections.values() for k, line in sec.items() if line["origin"] == IN) for i in range(n)]
	out.add(_("Bridge to the books of %s") % company, None, 0, bold=1, is_header=1)
	out.add(_("Profit per books (as posted)"), books, 1, bold=1, is_bridge=1, origin="books")
	row = out.add(_("Less: charged to cost centres of other companies"), [-v for v in out_total], 1, is_bridge=1, origin=OUT, drill_title="charged out")
	for slot in sorted(model.out.values(), key=lambda s: (s["company"], s["label"])):
		out.add("↳ %s: %s" % (slot["company"], slot["label"]), [-v for v in slot["vals"]], 2, is_line=1, origin=OUT,
			source_company=slot["company"], account=slot["account"], drill_title="charged out")
	out.add(_("Add: charged in from other companies"), in_total, 1, is_bridge=1, origin=IN, drill_title="charged in")
	mis = [books[i] - out_total[i] + in_total[i] for i in range(n)]
	out.add(_("Net profit, management view"), mis, 1, bold=1, is_total=1, is_bridge=1, origin="mis")
	diff = [mis[i] - mis_net[i] for i in range(n)]
	out.add(_("Difference from the net profit above (must be 0)"), diff, 1, bold=1, is_difference=1)
	return {"books": books, "out": out_total, "in": in_total, "mis": mis, "difference": diff}


# ─── the report ─────────────────────────────────────────────────────────────────────────────────


def validate(filters):
	if not filters.get("company"):
		frappe.throw(_("Choose a company."))
	frappe.has_permission("Company", "read", filters.company, throw=True)
	for name, allowed, default in (("periodicity", PERIODICITY, "Total"), ("comparison", COMPARISONS, "None"), ("columns_by", COLUMNS_BY, "Period"),
			("detail", DETAIL_LEVELS, DETAIL_LEVELS[1])):
		if (filters.get(name) or default) not in allowed:
			frappe.throw(_("%s must be one of: %s.") % (_(name.replace("_", " ").title()), ", ".join(allowed)))
		filters[name] = filters.get(name) or default
	filters.from_date, filters.to_date = money.as_dates(filters, cfs.default_from)
	if filters.comparison != "None" and filters.periodicity != "Total":
		frappe.throw(_("A comparison needs Periodicity Total."))
	if filters.comparison != "None" and filters.columns_by == "Cost Center":
		frappe.throw(_("A comparison is of periods: choose Columns By Period."))


def cost_centre_columns(own_rows, in_rows, alts, company) -> Columns:
	names = set()
	for _a, _on, cc, alt, _n in own_rows:
		if not (alt and alts.get(alt) and alts[alt][0] != company):
			names.add(cc or NO_CC)
	for _s, _a, _on, alt, _n in in_rows:
		if alts.get(alt):
			names.add(alts[alt][1] or NO_CC)
	return Columns("Cost Center", [{"label": n, "key": n} for n in sorted(names)])


def build(filters) -> dict:
	validate(filters)
	company = filters.company
	currency = frappe.get_cached_value("Company", company, "default_currency")
	alts = alternatives()
	mine = [name for name, (c, _cc) in alts.items() if c == company]
	scope_sql, scope_params = money.scope_params(filters)
	cc_filter = set()
	if filters.get("cost_center"):
		cc_filter = set(money.cost_centers(filters.cost_center))
	columns = period_columns(filters) if filters.columns_by == "Period" else None
	filters.range_from, filters.range_to = columns.span if columns else (filters.from_date, filters.to_date)
	own_rows = _own_rows(filters, scope_sql, scope_params)
	in_rows = fetch_in(filters, mine, {a for a in mine if alts[a][1] in cc_filter} if cc_filter else None) if has_dimension() else []
	if columns is None:
		columns = cost_centre_columns(own_rows, in_rows, alts, company)
	accounts = pl_accounts(company)
	sources = {s: pl_accounts(s) for s in {r[0] for r in in_rows}}
	rates = Rates(currency)
	model = build_model(own_rows, in_rows, accounts, sources, alts, company, columns, rates)
	out = Out(columns, currency, cint(filters.get("show_percent")))
	totals = statement_rows(model, out, filters.detail, cint(filters.get("show_zero_rows")))
	bridge = bridge_rows(model, out, company, totals["net"] or [0.0] * model.n)
	return dict(model=model, out=out, columns=columns, currency=currency, totals=totals, bridge=bridge, rates=rates,
		scoped=bool(scope_sql), dimension=has_dimension(), company=company)


def _own_rows(filters, scope_sql, scope_params):
	"""Own rows; with no alternative cost centre column yet, every row is simply the company's own."""
	if has_dimension():
		return fetch_own(filters, scope_sql, scope_params)
	query = OWN_ROWS.replace("ifnull(gle." + ALT_FIELD + ", '')", "''")
	params = dict(scope_params, company=filters.company, from_date=filters.range_from, to_date=filters.range_to)
	return frappe.db.sql(query + scope_sql + OWN_GROUP, params)


def columns_for(result, filters) -> list:
	cols = [{"label": _("Particulars"), "fieldname": "label", "fieldtype": "Data", "width": 360}]
	columns, comparison = result["columns"], columns_is_comparison(result)
	for i, item in enumerate(columns.items):
		cols.append({"label": item["label"], "fieldname": "p%d" % i, "fieldtype": "Currency", "options": "currency", "width": 150})
	if comparison:
		cols.append({"label": _("Change"), "fieldname": "change", "fieldtype": "Currency", "options": "currency", "width": 130})
		cols.append({"label": _("Change %"), "fieldname": "change_pct", "fieldtype": "Percent", "width": 90})
	elif len(columns) > 1:
		cols.append({"label": _("Total"), "fieldname": "total", "fieldtype": "Currency", "options": "currency", "width": 160})
	cols.append({"label": "", "fieldname": "currency", "fieldtype": "Link", "options": "Currency", "hidden": 1})
	return cols


def columns_is_comparison(result) -> bool:
	return bool(result["columns"].comparison)


def percent_columns(data, columns, revenue_row):
	"""Beside each amount, its share of revenue: filled in here so that ratio rows stay plain."""
	for row in data:
		if row.get("is_ratio") or row.get("is_difference") or row.get("is_header"):
			continue
		for i in range(len(columns)):
			key = "p%d" % i
			if key in row:
				row["pct%d" % i] = flt(margin(flt(row[key]), flt(revenue_row.get(key))), 3)


def message(result, filters):
	notes = []
	if not result["dimension"]:
		notes.append(_("The Alternative Cost Center dimension is not set up on this site yet: the figures are the company's own books."))
	if result["rates"].missing:
		notes.append(_("No exchange rate was found for %s into %s: charged-in amounts in that currency are left out. Add a Currency Exchange row.") % (
			", ".join(sorted(result["rates"].missing)), result["currency"]))
	worst = max((abs(d) for d in result["bridge"]["difference"]), default=0)
	if worst >= 0.0005:
		notes.append(_("The bridge to the books is %s away from the net profit: tell the accounts team.") % flt(worst, 3))
	if result["scoped"] or filters.get("cost_center"):
		notes.append(_("Branch applies to the company's own lines; a cost centre applies to own lines (as posted) and to charged-in lines (as charged)."))
	return "<br>".join(notes) or None


def summary(result):
	cur, nets = result["currency"], result["totals"]["nets"]
	revenue = sum(nets.get("REVENUE", [0.0]))
	gross = revenue + sum(nets.get("COST_OF_SALES", [0.0]))
	net = sum(result["totals"]["net"] or [0.0])
	card = lambda label, value: {"label": label, "value": flt(value, 3), "datatype": "Currency", "currency": cur, "indicator": "Green" if value >= 0 else "Red"}
	return [
		{"label": _("Revenue"), "value": flt(revenue, 3), "datatype": "Currency", "currency": cur, "indicator": "Blue"},
		card(_("Gross Profit"), gross),
		{"label": _("Gross Margin"), "value": flt(margin(gross, revenue), 1), "datatype": "Percent", "indicator": "Green" if gross >= 0 else "Red"},
		card(_("Net Profit"), net),
		{"label": _("Net Margin"), "value": flt(margin(net, revenue), 1), "datatype": "Percent", "indicator": "Green" if net >= 0 else "Red"},
		card(_("Charged In"), sum(result["bridge"]["in"])),
		card(_("Charged Out"), -sum(result["bridge"]["out"])),
	]


def chart(result):
	columns = result["columns"]
	if len(columns) < 2 or columns.comparison:
		return None
	nets = result["totals"]["nets"]
	n = len(columns)
	revenue = nets.get("REVENUE", [0.0] * n)
	profit = result["totals"]["net"] or [0.0] * n
	return {"data": {"labels": [i["label"] for i in columns.items], "datasets": [
		{"name": _("Revenue"), "values": [flt(v, 3) for v in revenue]},
		{"name": _("Net profit"), "values": [flt(v, 3) for v in profit]},
	]}, "type": "bar"}


def columns_meta(columns, from_date, to_date) -> dict:
	"""What each money column stands for, for the drill-down: its dates, or its cost centre."""
	meta = {}
	if columns.kind == "Cost Center":
		for i, item in enumerate(columns.items):
			meta["p%d" % i] = {"cost_center": "" if item["key"] == NO_CC else item["key"]}
		return meta
	for i, item in enumerate(columns.items):
		meta["p%d" % i] = {"from_date": str(item["from"]), "to_date": str(item["to"])}
	if len(columns) > 1 and not columns.comparison:
		meta["total"] = {"from_date": str(from_date), "to_date": str(to_date)}
	return meta


def execute(filters=None):
	filters = frappe._dict(filters or {})
	result = build(filters)
	data = result["out"].data
	if data:
		data[0]["columns_meta"] = columns_meta(result["columns"], filters.from_date, filters.to_date)
	revenue_row = next((r for r in data if r.get("section") == "REVENUE" and r.get("is_section")), {})
	if cint(filters.get("show_percent")) and revenue_row:
		percent_columns(data, result["columns"], revenue_row)
	cols = columns_for(result, filters)
	if cint(filters.get("show_percent")) and revenue_row:
		spliced = []
		for col in cols:
			spliced.append(col)
			if col["fieldname"].startswith("p") and col["fieldname"][1:].isdigit():
				spliced.append({"label": _("% of Revenue"), "fieldname": "pct%s" % col["fieldname"][1:], "fieldtype": "Percent", "width": 90})
		cols = spliced
	return cols, data, message(result, filters), chart(result), summary(result)
