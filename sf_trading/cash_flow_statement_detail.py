# sf_trading/cash_flow_statement_detail.py
"""Cash Flow Statement Detail -- the vouchers behind a figure of the Cash Flow Statement.

Every figure of the statement opens here, with its line, its dates, its branch / cost centre and (for an
account row) its accounts. The report reads the General Ledger entries of the accounts that make the figure up,
with the very rules the statement used (opening entries and Period Closing Vouchers are not flows; cancelled
entries are out), and shows them as vouchers.

The amount of an entry is its weight times (credit - debit): the weight is what its account's line counts for in
the figure (`cash_flow_statement.title_weights`; 1 for a plain line, -1 where the indirect statement adds a P&L
line back, and a total adds a line's weights up). So the total of this report IS the figure, and the report says
so: it adds the same entries up by another road (the statement's own month totals) and shows both.

Group By:

    Voucher Type    Sales Invoice, Payment Entry, Journal Entry ... each opened into its biggest vouchers
    Account         each account of the figure, opened into its biggest vouchers
    Party           customers / suppliers, opened into their vouchers
    Month           by posting month
    Vouchers        one row per voucher (the biggest ones; the rest are pooled in one row)
    Transactions    one row per voucher and account

A group shows its biggest vouchers (`PER_GROUP`) and one row for the rest, so that every group still adds up;
narrow by account, party, voucher type or date to see them all. A voucher's number opens the voucher.
"""

from collections import defaultdict

import frappe
from frappe import _
from frappe.utils import cstr, flt, getdate

from sf_trading import cash_flow as money
from sf_trading import cash_flow_statement as cfs

GROUPS = ("Voucher Type", "Account", "Party", "Month", "Vouchers", "Transactions")
PER_GROUP = 50
MAX_ROWS = 3000
VOUCHER_TYPES = ("", "Sales Invoice", "Purchase Invoice", "Payment Entry", "Journal Entry", "Stock Entry", "Purchase Receipt",
	"Delivery Note", "Stock Reconciliation", "Expense Claim", "Landed Cost Voucher")

ENTRIES = (
	"select gle.voucher_type, gle.voucher_no, gle.account, gle.party_type, gle.party, gle.branch, gle.cost_center, "
	"min(gle.posting_date) as posting_date, min(gle.remarks) as remarks, sum(gle.debit) as debit, sum(gle.credit) as credit "
	"from `tabGL Entry` gle where gle.company = %(company)s and gle.is_cancelled = 0 "
	"and gle.posting_date between %(from_date)s and %(to_date)s "
	"and ifnull(gle.is_opening, 'No') != 'Yes' and gle.voucher_type != 'Period Closing Voucher' "
	"and gle.account in %(accounts)s"
)
BY_VOUCHER_TYPE = " and gle.voucher_type = %(voucher_type)s"
BY_PARTY_TYPE = " and gle.party_type = %(party_type)s"
BY_PARTY = " and gle.party = %(party)s"
ENTRIES_GROUP = " group by gle.voucher_type, gle.voucher_no, gle.account, gle.party_type, gle.party, gle.branch, gle.cost_center"


# ─── what the figure is made of ──────────────────────────────────────────────────────────────


def listed(value) -> list:
	"""A filter that arrives as a list, a JSON list or a comma-separated string."""
	if not value:
		return []
	if isinstance(value, (list, tuple, set)):
		return [cstr(v).strip() for v in value if cstr(v).strip()]
	value = cstr(value).strip()
	if value.startswith("["):
		return listed(frappe.parse_json(value))
	return [v.strip() for v in value.split(",") if v.strip()]


def figure(filters):
	"""(title, {line key: weight}) the report is about: a line, group, section total or the whole statement."""
	method = cfs.METHODS[filters.get("method") or "Direct"]
	table = cfs.title_weights(method)
	asked = cstr(filters.get("line")).strip()
	entry = table.get((asked or cfs.GRAND_TITLE).lower())
	if not entry:
		titles = sorted({e["title"] for e in table.values()})
		frappe.throw(_("There is no line called '%s' on the %s statement. The lines are: %s.") % (asked, filters.get("method") or "Direct", "; ".join(titles)))
	return entry["title"], entry["weights"]


def account_weights(filters, line_of, weights) -> dict:
	"""{account: weight} for the accounts the figure is made of, narrowed to the accounts asked for."""
	chosen = set(listed(filters.get("account")))
	return {a: weights[key] for a, key in line_of.items() if key in weights and (not chosen or a in chosen)}


def fetch_entries(filters, accounts, scope_sql, scope_params):
	params = dict(scope_params, company=filters.company, from_date=filters.from_date, to_date=filters.to_date, accounts=tuple(accounts))
	query = ENTRIES
	for column, fragment in (("voucher_type", BY_VOUCHER_TYPE), ("party_type", BY_PARTY_TYPE), ("party", BY_PARTY)):
		if filters.get(column):
			query += fragment
			params[column] = filters[column]
	return frappe.db.sql(query + scope_sql + ENTRIES_GROUP, params, as_dict=True)


def weighted(entries, weight_of) -> list:
	"""Each entry with its amount: weight x (credit - debit)."""
	out = []
	for e in entries:
		amount = weight_of[e["account"]] * (flt(e["credit"]) - flt(e["debit"]))
		out.append(dict(e, amount=amount, debit=flt(e["debit"]), credit=flt(e["credit"])))
	return out


# ─── the rows ─────────────────────────────────────────────────────────────────────────────────


def month_label(d):
	return getdate(d).strftime("%b %Y")


def group_key(mode, e):
	if mode == "Voucher Type":
		return e["voucher_type"], e["voucher_type"]
	if mode == "Account":
		return e["account"], e["account"]
	if mode == "Party":
		party = e.get("party")
		return (e.get("party_type") or "", party or ""), ("%s: %s" % (e.get("party_type") or "", party)) if party else _("(no party)")
	month = getdate(e["posting_date"]).strftime("%Y-%m")
	return month, month_label(e["posting_date"])


def aggregate_vouchers(entries) -> list:
	"""One record per voucher: its entries added up, the first account, party, branch, cost centre and remark."""
	by = {}
	for e in entries:
		rec = by.setdefault((e["voucher_type"], e["voucher_no"]), dict(voucher_type=e["voucher_type"], voucher_no=e["voucher_no"],
			posting_date=e["posting_date"], accounts=[], parties=[], branch=e.get("branch"), cost_center=e.get("cost_center"),
			remarks=e.get("remarks"), debit=0.0, credit=0.0, amount=0.0, entries=0))
		rec["debit"] += e["debit"]
		rec["credit"] += e["credit"]
		rec["amount"] += e["amount"]
		rec["entries"] += 1
		if e["account"] not in rec["accounts"]:
			rec["accounts"].append(e["account"])
		if e.get("party") and e["party"] not in rec["parties"]:
			rec["parties"].append(e["party"])
		if not rec["remarks"] and e.get("remarks"):
			rec["remarks"] = e["remarks"]
	return list(by.values())


class Report:
	def __init__(self, currency):
		self.currency, self.data = currency, []

	def add(self, label, indent, record, **extra):
		row = {"label": label, "indent": indent, "currency": self.currency}
		row.update(extra)
		for key in ("posting_date", "voucher_type", "voucher_no", "account", "party", "branch", "cost_center", "remarks"):
			if key in record:
				row[key] = record[key]
		for key in ("debit", "credit", "amount"):
			row[key] = flt(record.get(key), 6)
		self.data.append(row)
		return row


def voucher_row(report, rec, indent):
	accounts, parties = rec["accounts"], rec["parties"]
	account = accounts[0] if len(accounts) == 1 else ("%s (+%s)" % (accounts[0], len(accounts) - 1) if accounts else "")
	party = parties[0] if len(parties) == 1 else ("%s (+%s)" % (parties[0], len(parties) - 1) if parties else "")
	return report.add(rec["voucher_no"], indent, dict(rec, account=account, party=party), is_voucher=1, entries=rec["entries"])


def pooled_row(report, label, recs, indent):
	return report.add(label, indent, {"debit": sum(r["debit"] for r in recs), "credit": sum(r["credit"] for r in recs),
		"amount": sum(r["amount"] for r in recs)}, is_pooled=1, entries=sum(r["entries"] for r in recs))


def show_vouchers(report, recs, indent, limit):
	"""The biggest `limit` vouchers, in date order, then one row for the rest."""
	ranked = sorted(recs, key=lambda r: (-abs(r["amount"]), r["voucher_no"]))
	shown, rest = ranked[:limit], ranked[limit:]
	for rec in sorted(shown, key=lambda r: (cstr(r["posting_date"]), r["voucher_no"])):
		voucher_row(report, rec, indent)
	if rest:
		pooled_row(report, _("Other vouchers (%s)") % len(rest), rest, indent)


def build_rows(entries, mode, currency) -> Report:
	report = Report(currency)
	if mode == "Transactions":
		recs = [dict(e, accounts=[e["account"]], parties=[e["party"]] if e.get("party") else [], entries=1, voucher_no=e["voucher_no"]) for e in entries]
		ranked = sorted(recs, key=lambda r: (-abs(r["amount"]), r["voucher_no"], r["account"]))
		shown, rest = ranked[:MAX_ROWS], ranked[MAX_ROWS:]
		for rec in sorted(shown, key=lambda r: (cstr(r["posting_date"]), r["voucher_no"], r["account"])):
			voucher_row(report, rec, 0)
		if rest:
			pooled_row(report, _("Other transactions (%s)") % len(rest), rest, 0)
		return report
	if mode == "Vouchers":
		show_vouchers(report, aggregate_vouchers(entries), 0, MAX_ROWS)
		return report
	groups = defaultdict(list)
	labels = {}
	for e in entries:
		key, label = group_key(mode, e)
		groups[key].append(e)
		labels[key] = label
	order = sorted(groups, key=lambda k: k if mode == "Month" else (-abs(sum(e["amount"] for e in groups[k])), cstr(k)))
	for key in order:
		members = groups[key]
		total = {"debit": sum(e["debit"] for e in members), "credit": sum(e["credit"] for e in members), "amount": sum(e["amount"] for e in members)}
		head = report.add(labels[key], 0, total, is_group=1, bold=1, entries=len(members))
		recs = aggregate_vouchers(members)
		head["vouchers"] = len(recs)
		show_vouchers(report, recs, 1, PER_GROUP)
	return report


# ─── the report ──────────────────────────────────────────────────────────────────────────────────


def columns(currency):
	def money_col(label, fieldname, width=120):
		return {"label": label, "fieldname": fieldname, "fieldtype": "Currency", "options": "currency", "width": width}

	return [
		{"label": _("Voucher / Group"), "fieldname": "label", "fieldtype": "Data", "width": 300},
		{"label": _("Date"), "fieldname": "posting_date", "fieldtype": "Date", "width": 95},
		{"label": _("Voucher Type"), "fieldname": "voucher_type", "fieldtype": "Data", "width": 120},
		{"label": _("Account"), "fieldname": "account", "fieldtype": "Data", "width": 230},
		{"label": _("Party"), "fieldname": "party", "fieldtype": "Data", "width": 170},
		{"label": _("Branch"), "fieldname": "branch", "fieldtype": "Link", "options": "Branch", "width": 75},
		{"label": _("Cost Center"), "fieldname": "cost_center", "fieldtype": "Link", "options": "Cost Center", "width": 130},
		money_col(_("Debit"), "debit"),
		money_col(_("Credit"), "credit"),
		money_col(_("Cash Flow Effect"), "amount", 140),
		{"label": _("Entries"), "fieldname": "entries", "fieldtype": "Int", "width": 70},
		{"label": _("Remarks"), "fieldname": "remarks", "fieldtype": "Data", "width": 280},
		{"label": "", "fieldname": "currency", "fieldtype": "Link", "options": "Currency", "hidden": 1},
	]


def validate(filters):
	if not filters.get("company"):
		frappe.throw(_("Choose a company."))
	frappe.has_permission("Company", "read", filters.company, throw=True)
	if (filters.get("method") or "Direct") not in cfs.METHODS:
		frappe.throw(_("Method must be Direct or Indirect."))
	if (filters.get("group_by") or GROUPS[0]) not in GROUPS:
		frappe.throw(_("Group By must be one of: %s.") % ", ".join(GROUPS))
	if (filters.get("voucher_type") or "") not in VOUCHER_TYPES:
		frappe.throw(_("Voucher Type must be one of: %s.") % ", ".join(v for v in VOUCHER_TYPES if v))
	filters.from_date, filters.to_date = money.as_dates(filters, cfs.default_from)


def statement_figure(filters, line_of, weights, selected, scope_sql, scope_params) -> float:
	"""The figure as the statement itself adds it up: its month totals per account, weighed."""
	flows = cfs.fetch_flows(filters, scope_sql, scope_params)
	return sum(selected[a] * flt(net) for a, _month, net in flows if a in selected)


def build(filters) -> dict:
	validate(filters)
	title, weights = figure(filters)
	line_of, _meta = cfs.load_accounts(filters.company)
	selected = account_weights(filters, line_of, weights)
	scope_sql, scope_params = money.scope_params(filters)
	currency = frappe.get_cached_value("Company", filters.company, "default_currency")
	entries = weighted(fetch_entries(filters, list(selected), scope_sql, scope_params), selected) if selected else []
	report = build_rows(entries, filters.get("group_by") or GROUPS[0], currency)
	total = sum(e["amount"] for e in entries)
	narrowed = bool(filters.get("voucher_type") or filters.get("party") or filters.get("party_type"))
	return dict(title=title, weights=weights, selected=selected, entries=entries, report=report, total=total, currency=currency,
		statement=None if narrowed else statement_figure(filters, line_of, weights, selected, scope_sql, scope_params),
		vouchers=len({(e["voucher_type"], e["voucher_no"]) for e in entries}), narrowed=narrowed, scoped=bool(scope_sql))


def message(result, filters):
	notes = [_("%s: %s to %s.") % (frappe.bold(result["title"]), filters.from_date, filters.to_date)]
	if result["statement"] is not None:
		diff = flt(result["total"] - result["statement"], 6)
		if abs(diff) < 0.0005:
			notes.append(_("The vouchers add up to %s, the figure on the Cash Flow Statement.") % flt(result["statement"], 3))
		else:
			notes.append(_("The vouchers add up to %s but the statement's figure is %s (%s apart): tell the accounts team.") % (
				flt(result["total"], 3), flt(result["statement"], 3), diff))
	else:
		notes.append(_("Narrowed by voucher type or party: this is part of the figure on the statement."))
	if not result["selected"]:
		notes.append(_("No account of the company belongs to this figure."))
	return "<br>".join(notes)


def summary(result):
	cur = result["currency"]
	return [
		{"label": _("Cash Flow Effect"), "value": flt(result["total"], 3), "datatype": "Currency", "currency": cur,
			"indicator": "Green" if result["total"] >= 0 else "Red"},
		{"label": _("Vouchers"), "value": result["vouchers"], "datatype": "Int"},
		{"label": _("Ledger Entries"), "value": len(result["entries"]), "datatype": "Int"},
		{"label": _("Accounts"), "value": len({e["account"] for e in result["entries"]}), "datatype": "Int"},
	]


def execute(filters=None):
	filters = frappe._dict(filters or {})
	result = build(filters)
	data = result["report"].data
	data.append({"label": _("Total"), "indent": 0, "is_total": 1, "bold": 1, "currency": result["currency"],
		"accounts": sorted(result["selected"]),
		"debit": flt(sum(e["debit"] for e in result["entries"]), 6), "credit": flt(sum(e["credit"] for e in result["entries"]), 6),
		"amount": flt(result["total"], 6), "entries": len(result["entries"])})
	return columns(result["currency"]), data, message(result, filters), None, summary(result)
