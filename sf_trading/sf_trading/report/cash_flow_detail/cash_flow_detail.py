# apps/sf_trading/sf_trading/report/cash_flow_detail/cash_flow_detail.py
"""Cash Flow Detail - what is behind a figure on the Cash Flow summary, and where it went.

Reached by clicking a period on Cash Flow Money In vs Money Out (its own dates and filters are
carried through), or run on its own. The money rows, their categories and every filter come from
sf_trading.cash_flow, the same code the summary uses, so the two always agree.

Views (Group By):

    Transactions             one row per ledger movement, with a running balance when it means one
    Category                 customers, suppliers, expenses, transfers ... (sf_trading.cash_flow)
    Category and Party       the same, opened up party by party
    Party / Voucher Type / Account / Mode of Payment / Branch / Day / Month
    Receivables and Payables what customers owed us and we owed suppliers: opening, invoiced,
                             returned, collected or paid, adjusted, closing

Every grouped row links to the Transactions behind it, with that group as a filter, so any figure
can be opened down to the vouchers.
"""

import json
from collections import defaultdict
from urllib.parse import urlencode

import frappe
from frappe import _
from frappe.utils import add_months, cint, flt, getdate

from sf_trading import cash_flow as cf

ROW_CAP = 5000
TRANSACTIONS = "Transactions"
CATEGORY_PARTY = "Category and Party"
RECEIVABLES_PAYABLES = "Receivables and Payables"
NOT_SET = "(not set)"

#: group-by -> (row key, drill filter, label)
GROUPS = {
	"Category": ("category", "category", "Category"),
	"Party": ("counter_party", "party", "Party"),
	"Voucher Type": ("voucher_type", "voucher_type", "Voucher Type"),
	"Account": ("account", "account", "Account"),
	"Mode of Payment": ("mode_of_payment", "mode_of_payment", "Mode of Payment"),
	"Branch": ("branch", "branch", "Branch"),
	"Day": ("posting_date", None, "Day"),
	"Month": ("month", None, "Month"),
}
#: the old option names, still in saved links and the summary's drill-down
LEGACY_GROUPS = {"By Party": "Party", "By Voucher Type": "Voucher Type", "By Account": "Account",
	"By Mode of Payment": "Mode of Payment"}
#: filters a drill link carries forward
CARRIED = ("account", "branch", "cost_center", "direction", "category", "party_type", "party", "voucher_type",
	"mode_of_payment", "exclude_internal_transfers", "exclude_pdc_accounts")


def execute(filters=None):
	filters = normalise(filters)
	from_date, to_date = cf.as_dates(filters, lambda to: add_months(to, -1))
	currency = frappe.get_cached_value("Company", filters.company, "default_currency")
	group_by = LEGACY_GROUPS.get(filters.get("group_by"), filters.get("group_by")) or TRANSACTIONS

	if group_by == RECEIVABLES_PAYABLES:
		return receivables_payables_view(filters, from_date, to_date, currency)

	accounts = cf.selected_accounts(filters)
	if not accounts:
		frappe.msgprint(_("No Cash or Bank accounts found for this company."))
		return transaction_columns(True), []

	rows = cf.money_rows(filters, accounts, from_date, to_date)
	reconciles = cf.balance_reconciles(filters)
	opening = cf.opening_balance(filters, accounts, from_date) if reconciles else None
	summary = cards(rows, opening, currency)
	message = note(filters, accounts, reconciles)

	if group_by == TRANSACTIONS:
		return transaction_columns(reconciles), transaction_rows(rows, opening, currency), message, None, summary
	if group_by == CATEGORY_PARTY:
		data = category_party_rows(rows, filters, from_date, to_date, currency)
		return grouped_columns(_("Category / Party")), data, message, None, summary
	key, drill, label = GROUPS.get(group_by, GROUPS["Category"])
	data = grouped_rows(rows, key, drill, filters, from_date, to_date, currency)
	return grouped_columns(_(label)), data, message, chart(data), summary


def normalise(filters):
	filters = frappe._dict(filters or {})
	filters.company = filters.company or frappe.defaults.get_user_default("Company")
	if not filters.company:
		frappe.throw(_("Please choose a company."))
	branch = filters.get("branch")
	if isinstance(branch, str):
		# a drill link carries a list as JSON or as "A,B"
		try:
			branch = json.loads(branch)
		except ValueError:
			branch = [b.strip() for b in branch.split(",")]
	if isinstance(branch, (list, tuple)):
		filters.branch = [b for b in branch if b]
	else:
		filters.branch = [branch] if branch else []
	return filters


# ── transactions ────────────────────────────────────────────────────────────────────────────────


def transaction_rows(rows, opening, currency):
	"""Every movement, capped for the browser. Totals and cards always cover every row."""
	data = []
	running = opening
	if opening is not None:
		data.append({"posting_date": None, "voucher_type": _("Opening Balance"), "running": opening,
			"is_opening": 1, "currency": currency})
	for r in rows[:ROW_CAP]:
		net = flt(r.money_in - r.money_out, 3)
		if running is not None:
			running = flt(running + net, 3)
		data.append({
			"posting_date": r.posting_date, "voucher_type": r.voucher_type, "voucher_no": r.voucher_no,
			"account": r.account, "branch": r.branch, "category": r.category,
			"party_type": r.counter_party_type, "party": r.counter_party, "mode_of_payment": r.mode_of_payment,
			"remarks": (r.remarks or "")[:140], "money_in": r.money_in, "money_out": r.money_out,
			"net": net, "running": running, "currency": currency,
		})
	total_in, total_out = summarise(rows)
	closing = flt(opening + total_in - total_out, 3) if opening is not None else None
	data.append({"voucher_type": _("Total"), "money_in": flt(total_in, 3), "money_out": flt(total_out, 3),
		"net": flt(total_in - total_out, 3), "running": closing, "is_total": 1, "currency": currency})
	if len(rows) > ROW_CAP:
		capped = _("Showing the first {0} of {1} movements; totals and cards cover all of them.")
		frappe.msgprint(capped.format(ROW_CAP, len(rows)) + " " + _("Narrow the dates or group the report to see the rest."))
	return data


def transaction_columns(reconciles):
	columns = [
		{"label": _("Date"), "fieldname": "posting_date", "fieldtype": "Date", "width": 95},
		{"label": _("Voucher Type"), "fieldname": "voucher_type", "fieldtype": "Data", "width": 120},
		{"label": _("Voucher"), "fieldname": "voucher_no", "fieldtype": "Dynamic Link", "options": "voucher_type", "width": 150},
		{"label": _("Account"), "fieldname": "account", "fieldtype": "Link", "options": "Account", "width": 190},
		{"label": _("Branch"), "fieldname": "branch", "fieldtype": "Link", "options": "Branch", "width": 75},
		{"label": _("Category"), "fieldname": "category", "fieldtype": "Data", "width": 120},
		{"label": _("Party Type"), "fieldname": "party_type", "fieldtype": "Data", "width": 85},
		{"label": _("Party"), "fieldname": "party", "fieldtype": "Dynamic Link", "options": "party_type", "width": 160},
		{"label": _("Mode"), "fieldname": "mode_of_payment", "fieldtype": "Data", "width": 120},
		money("money_in", _("Money In")),
		money("money_out", _("Money Out")),
		money("net", _("Net")),
	]
	if reconciles:
		columns.append(money("running", _("Running Balance"), 140))
	columns += [{"label": _("Remarks"), "fieldname": "remarks", "fieldtype": "Data", "width": 260}, CURRENCY]
	return columns


# ── grouped ─────────────────────────────────────────────────────────────────────────────────────


def group_value(row, key):
	if key == "month":
		return getdate(row.posting_date).strftime("%Y-%m")
	value = row.get(key)
	if key == "posting_date":
		return str(value)
	return value or NOT_SET


def drill_args(filters, from_date, to_date, extra):
	args = {"company": filters.company, "from_date": str(from_date), "to_date": str(to_date), "group_by": TRANSACTIONS}
	for key in CARRIED:
		value = filters.get(key)
		if value:
			args[key] = ",".join(value) if isinstance(value, (list, tuple)) else value
	args.update({k: v for k, v in extra.items() if v is not None})
	return args


def drill_link(label, args):
	url = "/app/query-report/Cash Flow Detail?" + urlencode(args)
	return "<a href='%s'>%s</a>" % (frappe.utils.escape_html(url), frappe.utils.escape_html(str(label)))


def group_drill(key, drill, value, row_sample, filters, from_date, to_date):
	"""The filters that open exactly this group's transactions, or None when nothing can."""
	if key == "posting_date":
		extra = {"from_date": value, "to_date": value}
	elif key == "month":
		first = getdate(value + "-01")
		last = getdate(frappe.utils.get_last_day(first))
		extra = {"from_date": str(max(first, getdate(from_date))), "to_date": str(min(last, getdate(to_date)))}
	elif key == "counter_party":
		if not row_sample.counter_party:
			return None
		extra = {"party_type": row_sample.counter_party_type, "party": row_sample.counter_party}
	elif drill and value != NOT_SET:
		extra = {drill: value}
	else:
		return None
	return drill_args(filters, from_date, to_date, extra)


def summarise(rows):
	return sum(r.money_in for r in rows) or 0.0, sum(r.money_out for r in rows) or 0.0


def grouped_rows(rows, key, drill, filters, from_date, to_date, currency):
	groups = defaultdict(lambda: frappe._dict(money_in=0.0, money_out=0.0, count=0, sample=None))
	for r in rows:
		g = groups[group_value(r, key)]
		g.money_in += r.money_in
		g.money_out += r.money_out
		g.count += 1
		g.sample = g.sample or r
	total_in, total_out = summarise(rows)
	if key in ("posting_date", "month"):
		ordered = sorted(groups.items())
	else:
		ordered = sorted(groups.items(), key=lambda kv: -(kv[1].money_in + kv[1].money_out))
	data = []
	for value, g in ordered:
		args = group_drill(key, drill, value, g.sample, filters, from_date, to_date)
		data.append(group_row(drill_link(value, args) if args else value, value, g, total_in, total_out, currency))
	data.append(total_row(rows, currency))
	return data


def category_party_rows(rows, filters, from_date, to_date, currency):
	"""Category rows, each opened up by party (indent 1) -- a two-level drill on one screen."""
	by_category = defaultdict(list)
	for r in rows:
		by_category[r.category].append(r)
	total_in, total_out = summarise(rows)
	data = []
	for category in sorted(by_category, key=lambda c: -sum(r.money_in + r.money_out for r in by_category[c])):
		members = by_category[category]
		g = frappe._dict(money_in=sum(r.money_in for r in members), money_out=sum(r.money_out for r in members),
			count=len(members))
		cell = drill_link(category, drill_args(filters, from_date, to_date, {"category": category}))
		data.append(dict(group_row(cell, category, g, total_in, total_out, currency), indent=0))
		parties = defaultdict(lambda: frappe._dict(money_in=0.0, money_out=0.0, count=0))
		for r in members:
			p = parties[(r.counter_party_type, r.counter_party)]
			p.money_in += r.money_in
			p.money_out += r.money_out
			p.count += 1
		for (party_type, party), p in sorted(parties.items(), key=lambda kv: -(kv[1].money_in + kv[1].money_out)):
			if party:
				extra = {"category": category, "party_type": party_type, "party": party}
				cell, label = drill_link(party, drill_args(filters, from_date, to_date, extra)), party
			else:
				cell = label = _("(no party)")
			data.append(dict(group_row(cell, label, p, total_in, total_out, currency), indent=1))
	data.append(total_row(rows, currency))
	return data


def group_row(cell, label, g, total_in, total_out, currency):
	return {
		"group": cell, "label": label, "count": g.count,
		"money_in": flt(g.money_in, 3), "money_out": flt(g.money_out, 3), "net": flt(g.money_in - g.money_out, 3),
		"share_in": flt(g.money_in * 100 / total_in, 1) if total_in else None,
		"share_out": flt(g.money_out * 100 / total_out, 1) if total_out else None,
		"currency": currency,
	}


def total_row(rows, currency):
	total_in, total_out = summarise(rows)
	return {"group": _("Total"), "label": _("Total"), "count": len(rows), "money_in": flt(total_in, 3),
		"money_out": flt(total_out, 3), "net": flt(total_in - total_out, 3), "is_total": 1, "currency": currency}


def grouped_columns(label):
	return [
		{"label": label, "fieldname": "group", "fieldtype": "Data", "width": 260},
		{"label": _("Movements"), "fieldname": "count", "fieldtype": "Int", "width": 90},
		money("money_in", _("Money In")),
		{"label": _("% of In"), "fieldname": "share_in", "fieldtype": "Percent", "width": 80},
		money("money_out", _("Money Out")),
		{"label": _("% of Out"), "fieldname": "share_out", "fieldtype": "Percent", "width": 80},
		money("net", _("Net")),
		CURRENCY,
	]


# ── receivables and payables ────────────────────────────────────────────────────────────────────


def receivables_payables_view(filters, from_date, to_date, currency):
	model = cf.receivables_and_payables(filters, from_date, to_date)
	receivable = model["lines"]["Receivable"]
	payable = model["lines"]["Payable"]
	ar_open = flt(model["opening"].get("Receivable"), 3)
	ar_close = flt(model["closing"].get("Receivable"), 3)
	# payables are credit balances; shown as what is owed, a positive figure
	ap_open = -flt(model["opening"].get("Payable"), 3)
	ap_close = -flt(model["closing"].get("Payable"), 3)

	def link(label, extra):
		return drill_link(label, drill_args(filters, from_date, to_date, extra))

	data = [
		{"line": _("Receivables: what customers owe"), "is_header": 1},
		line(_("Opening balance"), ar_open, currency, bold=1),
		line(_("+ Sales invoiced"), receivable["invoiced"], currency),
		line(_("- Sales returns"), -receivable["returned"], currency),
		line(link(_("- Collected"), {"category": cf.CUSTOMERS, "direction": "Money In"}), -receivable["settled"], currency),
		line(link(_("+ Refunds paid out"), {"category": cf.CUSTOMERS, "direction": "Money Out"}), receivable["refunded"], currency),
		line(_("+/- Adjustments (set-offs, write-offs, journals)"), receivable["adjusted"], currency),
		line(_("Closing balance"), ar_close, currency, bold=1),
		{"line": ""},
		{"line": _("Payables: what we owe suppliers"), "is_header": 1},
		line(_("Opening balance"), ap_open, currency, bold=1),
		line(_("+ Purchases billed"), payable["invoiced"], currency),
		line(_("- Purchase returns"), -payable["returned"], currency),
		line(link(_("- Paid"), {"category": cf.SUPPLIERS, "direction": "Money Out"}), -payable["settled"], currency),
		line(link(_("+ Refunds received"), {"category": cf.SUPPLIERS, "direction": "Money In"}), payable["refunded"], currency),
		line(_("+/- Adjustments (set-offs, write-offs, journals)"), payable["adjusted"], currency),
		line(_("Closing balance"), ap_close, currency, bold=1),
		{"line": ""},
		{"line": _("Net position"), "is_header": 1},
		line(_("Receivables less payables, opening"), ar_open - ap_open, currency),
		line(_("Receivables less payables, closing"), ar_close - ap_close, currency, bold=1),
	]
	collectible = ar_open + receivable["invoiced"] - receivable["returned"]
	payable_due = ap_open + payable["invoiced"] - payable["returned"]
	data += [
		{"line": _("Collection ratio (collected / opening + net sales)"),
			"ratio": flt(receivable["settled"] * 100 / collectible, 1) if collectible else None},
		{"line": _("Payment ratio (paid / opening + net purchases)"),
			"ratio": flt(payable["settled"] * 100 / payable_due, 1) if payable_due else None},
	]
	columns = [
		{"label": _("Line"), "fieldname": "line", "fieldtype": "Data", "width": 360},
		money("amount", _("Amount"), 160),
		{"label": _("Ratio"), "fieldname": "ratio", "fieldtype": "Percent", "width": 90},
		CURRENCY,
	]
	summary = [
		card(_("Receivables, closing"), ar_close, currency, "Blue"),
		card(_("Collected"), receivable["settled"], currency, "Green"),
		card(_("Payables, closing"), ap_close, currency, "Orange"),
		card(_("Paid"), payable["settled"], currency, "Red"),
	]
	return columns, data, None, None, summary


def line(label, amount, currency, bold=0):
	return {"line": label, "amount": flt(amount, 3), "currency": currency, "bold": bold}


# ── shared bits ─────────────────────────────────────────────────────────────────────────────────


CURRENCY = {"label": "Currency", "fieldname": "currency", "fieldtype": "Link", "options": "Currency", "hidden": 1}


def money(fieldname, label, width=130):
	return {"label": label, "fieldname": fieldname, "fieldtype": "Currency", "options": "currency", "width": width}


def card(label, value, currency, indicator):
	return {"label": label, "value": flt(value, 3), "datatype": "Currency", "currency": currency, "indicator": indicator}


def cards(rows, opening, currency):
	total_in, total_out = summarise(rows)
	out = [
		card(_("Money In"), total_in, currency, "Green"),
		card(_("Money Out"), total_out, currency, "Red"),
		card(_("Net"), total_in - total_out, currency, "Green" if total_in >= total_out else "Red"),
	]
	if opening is not None:
		closing = opening + total_in - total_out
		out = [card(_("Opening Balance"), opening, currency, "Blue")] + out
		out.append(card(_("Closing Balance"), closing, currency, "Blue" if closing >= 0 else "Red"))
	return out


def note(filters, accounts, reconciles):
	reading = _("Reading {0} cash / bank account(s).").format(len(accounts))
	bits = [reading + " " + _("Each movement is classed by the other side of its voucher.")]
	if not reconciles:
		bits.append(_("A movement filter is set (direction, category, party, voucher type, mode or transfers), "
			"so no running balance is shown: a balance cannot be filtered that way."))
	elif not cint(filters.get("exclude_internal_transfers")):
		bits.append(_("Transfers between your own accounts appear on both sides and net to nil."))
	return "<div style='padding:8px 10px;border-left:3px solid #1f6f54'>" + "<br>".join(bits) + "</div>"


def chart(data):
	points = [d for d in data if not d.get("is_total")][:20]
	if not points:
		return None
	return {
		"data": {
			"labels": [str(d.get("label")) for d in points],
			"datasets": [
				{"name": _("Money In"), "values": [flt(d.get("money_in")) for d in points]},
				{"name": _("Money Out"), "values": [flt(d.get("money_out")) for d in points]},
			],
		},
		"type": "bar",
		"colors": ["#2e7d32", "#c62828"],
		"fieldtype": "Currency",
	}
