# apps/sf_trading/sf_trading/report/cash_flow_money_in_vs_money_out/cash_flow_money_in_vs_money_out.py
"""Cash Flow Money In vs Money Out.

    Money In        = every debit posted to a Cash or Bank account
    Money Out       = every credit posted to one
    Net Movement    = Money In - Money Out
    Running Balance = opening balance + every net movement up to that row

Read from the ledger through sf_trading.cash_flow -- the same rows, categories and filters Cash
Flow Detail uses -- so a period here always opens to vouchers that add up to it.

A transfer between two of the company's own accounts shows as money out of one and into the other,
and nets to nothing. Tick "Exclude internal transfers" to see only money that crossed the company
boundary. Branch, cost centre, account and the PDC option narrow which accounts count, and the
opening balance with them, so the closing balance is still those accounts' balance on the To Date.
Any other filter narrows movements only, and then no running balance is shown.
"""

from collections import defaultdict
from urllib.parse import urlencode

import frappe
from frappe import _
from frappe.utils import add_months, flt, getdate

from sf_trading import cash_flow as cf
from sf_trading.sf_trading.report.cash_flow_detail.cash_flow_detail import CARRIED, normalise

PERIODS = {"Daily": "%Y-%m-%d", "Weekly": "%G-W%V", "Monthly": "%Y-%m", "Yearly": "%Y"}


def execute(filters=None):
	filters = normalise(filters)
	from_date, to_date = cf.as_dates(filters, lambda to: add_months(to, -6))
	currency = frappe.get_cached_value("Company", filters.company, "default_currency")

	accounts = cf.selected_accounts(filters)
	if not accounts:
		frappe.msgprint(_("No Cash or Bank accounts found for this company."))
		return columns(True), []

	rows = cf.money_rows(filters, accounts, from_date, to_date)
	reconciles = cf.balance_reconciles(filters)
	opening = cf.opening_balance(filters, accounts, from_date) if reconciles else None

	periods = defaultdict(lambda: frappe._dict(money_in=0.0, money_out=0.0, starts=None, ends=None))
	for r in rows:
		p = periods[period_label(r.posting_date, filters.get("periodicity") or "Monthly")]
		p.money_in += r.money_in
		p.money_out += r.money_out
		date = getdate(r.posting_date)
		p.starts = min(p.starts, date) if p.starts else date
		p.ends = max(p.ends, date) if p.ends else date

	data, running = [], opening
	if opening is not None:
		data.append({"period": _("Opening Balance"), "label": _("Opening Balance"), "running": opening,
			"is_opening": 1, "currency": currency})
	for label in sorted(periods):
		p = periods[label]
		net = flt(p.money_in - p.money_out, 3)
		if running is not None:
			running = flt(running + net, 3)
		data.append({
			# `period` carries the drill-down anchor; `label` is plain text for the chart axis
			"period": detail_link(label, p.starts, p.ends, filters),
			"label": label,
			"money_in": flt(p.money_in, 3), "money_out": flt(p.money_out, 3), "net": net,
			"running": running, "currency": currency,
		})
	total_in = flt(sum(r.money_in for r in rows), 3)
	total_out = flt(sum(r.money_out for r in rows), 3)
	data.append({"period": _("Total"), "label": _("Total"), "money_in": total_in, "money_out": total_out,
		"net": flt(total_in - total_out, 3), "running": running, "is_total": 1, "currency": currency})

	summary = [
		card(_("Money In"), total_in, currency, "Green"),
		card(_("Money Out"), total_out, currency, "Red"),
		card(_("Net Movement"), total_in - total_out, currency, "Green" if total_in >= total_out else "Red"),
	]
	if opening is not None:
		summary.insert(0, card(_("Opening Balance"), opening, currency, "Blue"))
		summary.append(card(_("Closing Balance"), running, currency, "Blue" if flt(running) >= 0 else "Red"))
	return columns(reconciles), data, message(accounts, filters, reconciles), chart(data, reconciles), summary


def period_label(posting_date, periodicity):
	date = getdate(posting_date)
	if periodicity == "Quarterly":
		return "%s-Q%s" % (date.year, (date.month - 1) // 3 + 1)
	return date.strftime(PERIODS.get(periodicity, PERIODS["Monthly"]))


def detail_link(label, starts, ends, filters):
	"""A period as a link to Cash Flow Detail, carrying this row's dates and every filter."""
	args = {"company": filters.company, "from_date": str(starts), "to_date": str(ends), "group_by": "Transactions"}
	for key in CARRIED:
		value = filters.get(key)
		if value:
			args[key] = ",".join(value) if isinstance(value, (list, tuple)) else value
	url = "/app/query-report/Cash Flow Detail?" + urlencode(args)
	return "<a href='%s'>%s</a>" % (frappe.utils.escape_html(url), frappe.utils.escape_html(str(label)))


def card(label, value, currency, indicator):
	return {"label": label, "value": flt(value, 3), "datatype": "Currency", "currency": currency, "indicator": indicator}


def columns(reconciles):
	cols = [
		{"label": _("Period"), "fieldname": "period", "fieldtype": "Data", "width": 190},
		{"label": _("Money In"), "fieldname": "money_in", "fieldtype": "Currency", "options": "currency", "width": 150},
		{"label": _("Money Out"), "fieldname": "money_out", "fieldtype": "Currency", "options": "currency", "width": 150},
		{"label": _("Net Movement"), "fieldname": "net", "fieldtype": "Currency", "options": "currency", "width": 150},
	]
	if reconciles:
		cols.append({"label": _("Running Balance"), "fieldname": "running", "fieldtype": "Currency",
			"options": "currency", "width": 170})
	cols.append({"label": "Currency", "fieldname": "currency", "fieldtype": "Link", "options": "Currency", "hidden": 1})
	return cols


def message(accounts, filters, reconciles):
	reading = _("Reading {0} cash / bank account(s).").format(len(accounts))
	bits = [_("<b>Money In</b> is every debit to a Cash or Bank account, <b>Money Out</b> every credit."), reading]
	if not reconciles:
		bits.append(_("A movement filter is set, so no running balance is shown: a balance cannot be filtered that way."))
	elif filters.get("exclude_internal_transfers"):
		bits.append(_("Transfers between your own cash and bank accounts are excluded."))
	else:
		bits.append(_("Transfers between your own accounts appear on both sides and net to nil."))
	return "<div style='padding:8px 10px;border-left:3px solid #1f6f54'>" + "<br>".join(bits) + "</div>"


def chart(data, reconciles):
	points = [d for d in data if not d.get("is_opening") and not d.get("is_total")]
	if not points:
		return None
	datasets = [
		{"name": _("Money In"), "values": [flt(d["money_in"]) for d in points]},
		{"name": _("Money Out"), "values": [flt(d["money_out"]) for d in points]},
	]
	if reconciles:
		datasets.append({"name": _("Running Balance"), "values": [flt(d["running"]) for d in points]})
	return {
		"data": {"labels": [d["label"] for d in points], "datasets": datasets},
		"type": "axis-mixed",
		"colors": ["#2e7d32", "#c62828", "#1565c0"],
		"fieldtype": "Currency",
	}
