# sf_trading/mis_profit_loss_detail.py
"""MIS Profit and Loss Detail -- the vouchers behind a figure of the MIS Profit and Loss.

Opened by clicking a figure of the profit and loss, which fills in the section (or subtotal), the dates of its
column, the origin (own books / charged in / charged out), the account, the source company and the cost
centre; or run on its own. It reads the same General Ledger rows the profit and loss reads, classifies each one
the same way, and shows them as vouchers, grouped as asked:

    Voucher Type    Sales Invoice, Journal Entry ...                each opened into its biggest vouchers
    Account         each account of the figure                      (a charged-in row under its own company's account)
    Company         the company that posted it                      (own books, or each company that charged this one)
    Origin          own books, charged in, charged out
    Party, Month    as they say
    Vouchers        one row per voucher (the biggest; the rest pooled in one row)
    Transactions    one row per voucher and account

A figure of this report is the figure clicked: an entry is worth the sign its section shows (costs as positive
costs), converted into this company's currency at the voucher date's rate for a charged-in entry; a subtotal adds
the net of everything above it. The report adds the same ledger rows up a second way (the profit and loss's own
totals) and says on top whether the two agree. With no origin chosen it is the management view, own books and
charged in, as the profit and loss shows it; "Charged out" is asked for by name and is shown as the amount
added back.
"""

import frappe
from frappe import _
from frappe.utils import cint, cstr, flt, getdate

from sf_trading import cash_flow as money
from sf_trading import cash_flow_statement_detail as det
from sf_trading import mis_profit_loss as mis

ORIGINS = ("", "Own books", "Charged in", "Charged out")
ORIGIN_KEY = {"Own books": mis.OWN, "Charged in": mis.IN, "Charged out": mis.OUT}
GROUPS = ("Voucher Type", "Account", "Company", "Origin", "Party", "Month", "Vouchers", "Transactions")

DETAIL = (
	"select gle.voucher_type, gle.voucher_no, gle.account, gle.party_type, gle.party, gle.branch, gle.cost_center, "
	"ifnull(gle." + mis.ALT_FIELD + ", '') as alt, min(gle.posting_date) as posting_date, min(gle.remarks) as remarks, "
	"sum(gle.debit) as debit, sum(gle.credit) as credit "
)
DETAIL_OWN = DETAIL + ", gle.company " + mis.BASE + " and gle.company = %(company)s"
DETAIL_IN = DETAIL + ", gle.company " + mis.BASE + " and gle.company != %(company)s and gle." + mis.ALT_FIELD + " in %(alts)s"
DETAIL_GROUP = " group by gle.company, gle.voucher_type, gle.voucher_no, gle.account, gle.party_type, gle.party, gle.branch, gle.cost_center, alt"


def listed(value):
	return det.listed(value)


def fetch_entries(filters, alts_of_company, scope_sql, scope_params, want_in, want_own):
	rows = []
	params = dict(scope_params, company=filters.company, from_date=filters.from_date, to_date=filters.to_date)
	if want_own:
		rows += frappe.db.sql(DETAIL_OWN + scope_sql + DETAIL_GROUP, params, as_dict=True)
	if want_in and alts_of_company:
		params_in = dict(company=filters.company, from_date=filters.from_date, to_date=filters.to_date, alts=tuple(alts_of_company))
		rows += frappe.db.sql(DETAIL_IN + DETAIL_GROUP, params_in, as_dict=True)
	return rows


def classify(entries, company, accounts, source_accounts, alts, rates):
	"""Give each ledger row its origin, section and the amounts in this company's currency; rows of no account
	of ours, or of another company's that were not charged to this one, drop out."""
	out = []
	for e in entries:
		alt = e.get("alt") or ""
		target = alts.get(alt)
		if e["company"] == company:
			meta = accounts.get(e["account"])
			origin = mis.OUT if (alt and target and target[0] != company) else mis.OWN
			rate, shown_cc = 1.0, e.get("cost_center")
		else:
			meta = (source_accounts.get(e["company"]) or {}).get(e["account"])
			if not target or target[0] != company:
				continue
			origin, shown_cc = mis.IN, target[1]
			rate = rates.rate(frappe.get_cached_value("Company", e["company"], "default_currency"), e["posting_date"])
			if not rate:
				rates.missing.add(frappe.get_cached_value("Company", e["company"], "default_currency"))
		if not meta:
			continue
		debit, credit = flt(e["debit"]) * rate, flt(e["credit"]) * rate
		out.append(dict(e, origin=origin, section=meta["section"], account_label=meta["label"], rate=rate, debit=debit, credit=credit,
			net=credit - debit, cost_center=shown_cc, account_name=e["account"]))
	return out


def select(entries, filters, signs):
	"""The entries a figure is made of, each valued as that figure shows it."""
	wanted_origin = ORIGIN_KEY.get(filters.get("origin") or "")
	accounts = set(listed(filters.get("account")))
	source = filters.get("source_company")
	flip = -1 if (wanted_origin == mis.OUT and not signs_given(filters)) else 1
	out = []
	for e in entries:
		if wanted_origin and e["origin"] != wanted_origin:
			continue
		if not wanted_origin and e["origin"] == mis.OUT:
			# no origin asked: the management view, which is own books and charged in; charged out is not this company's
			continue
		if accounts and e["account_name"] not in accounts:
			continue
		if source and e["company"] != source and not (e["origin"] == mis.OUT and cstr(alt_company(e)) == source):
			continue
		sign = signs.get(e["section"])
		if sign is None:
			continue
		out.append(dict(e, amount=flip * sign * e["net"], account=e["account_label"]))
	return out


def alt_company(e):
	return mis.alternatives().get(e.get("alt") or "", (None, None))[0]


def signs_given(filters) -> bool:
	return bool(cstr(filters.get("line")).strip())


def figure_signs(filters) -> dict:
	"""{section: sign} for the line named, or every section (+1) for the whole profit."""
	title = cstr(filters.get("line")).strip()
	if not title:
		return {s["key"]: 1 for s in mis.LAYOUT if not s.get("subtotal")}
	signs = mis.section_signs(title)
	if not signs:
		titles = "; ".join(s["label"] for s in mis.LAYOUT)
		frappe.throw(_("There is no section or subtotal called '%s'. They are: %s.") % (title, titles))
	return signs


def build(filters) -> dict:
	mis.validate(filters)
	if (filters.get("group_by") or GROUPS[0]) not in GROUPS:
		frappe.throw(_("Group By must be one of: %s.") % ", ".join(GROUPS))
	if (filters.get("origin") or "") not in ORIGINS:
		frappe.throw(_("Origin must be one of: %s.") % ", ".join(o for o in ORIGINS if o))
	company = filters.company
	signs = figure_signs(filters)
	currency = frappe.get_cached_value("Company", company, "default_currency")
	alts = mis.alternatives()
	mine = [n for n, (c, _cc) in alts.items() if c == company]
	scope_sql, scope_params = money.scope_params(filters)
	allowed_in = mis.charged_in_scope(filters, mine, alts)
	want_origin = ORIGIN_KEY.get(filters.get("origin") or "")
	have_dimension = mis.has_dimension()
	raw = fetch_entries(filters, [a for a in mine if allowed_in is None or a in allowed_in] if have_dimension else [], scope_sql,
		scope_params, want_in=have_dimension and want_origin in (None, mis.IN), want_own=want_origin in (None, mis.OWN, mis.OUT))
	accounts = mis.pl_accounts(company)
	sources = {c: mis.pl_accounts(c) for c in {e["company"] for e in raw if e["company"] != company}}
	rates = mis.Rates(currency)
	classified = classify(raw, company, accounts, sources, alts, rates)
	entries = select(classified, filters, signs)
	total = sum(e["amount"] for e in entries)
	return dict(entries=entries, total=total, currency=currency, rates=rates, signs=signs, scoped=bool(scope_sql), company=company)


def build_rows(entries, mode, currency):
	if mode not in ("Company", "Origin"):
		return det.build_rows(entries, mode, currency)
	report = det.Report(currency)
	groups, labels = {}, {}
	for e in entries:
		key = e["company"] if mode == "Company" else mis.ORIGIN_LABEL[e["origin"]]
		groups.setdefault(key, []).append(e)
		labels[key] = key
	for key in sorted(groups, key=lambda k: (-abs(sum(e["amount"] for e in groups[k])), k)):
		members = groups[key]
		head = report.add(labels[key], 0, {"debit": sum(e["debit"] for e in members), "credit": sum(e["credit"] for e in members),
			"amount": sum(e["amount"] for e in members)}, is_group=1, bold=1, entries=len(members))
		recs = det.aggregate_vouchers(members)
		head["vouchers"] = len(recs)
		det.show_vouchers(report, recs, 1, det.PER_GROUP)
	return report


def columns(currency):
	cols = det.columns(currency)
	for col in cols:
		if col["fieldname"] == "amount":
			col["label"] = _("Amount (as in the profit and loss)")
	return cols


def figure_total(filters, result) -> float:
	"""The same figure by the profit and loss's own road: its date-grouped ledger rows, valued the same way."""
	company = filters.company
	scope_sql, scope_params = money.scope_params(filters)
	alts = mis.alternatives()
	mine = [n for n, (c, _cc) in alts.items() if c == company]
	filters.range_from, filters.range_to = filters.from_date, filters.to_date
	own = mis._own_rows(filters, scope_sql, scope_params)
	inn = mis.fetch_in(filters, mine, mis.charged_in_scope(filters, mine, alts)) if mis.has_dimension() else []
	accounts = mis.pl_accounts(company)
	sources = {s: mis.pl_accounts(s) for s in {r[0] for r in inn}}
	rates = mis.Rates(result["currency"])
	wanted = ORIGIN_KEY.get(filters.get("origin") or "")
	chosen = set(listed(filters.get("account")))
	source = filters.get("source_company")
	flip = -1 if (wanted == mis.OUT and not signs_given(filters)) else 1
	signs, total = result["signs"], 0.0
	for account, _on, _cc, alt, net in own:
		meta, target = accounts.get(account), alts.get(alt or "")
		origin = mis.OUT if (alt and target and target[0] != company) else mis.OWN
		if not meta or (wanted and origin != wanted) or (not wanted and origin == mis.OUT) or (chosen and account not in chosen):
			continue
		if source and not (origin == mis.OUT and target and target[0] == source):
			continue
		total += flip * signs.get(meta["section"], 0) * flt(net)
	if wanted in (None, mis.IN):
		for src, account, on, alt, net in inn:
			meta, target = (sources.get(src) or {}).get(account), alts.get(alt)
			if not meta or not target or (chosen and account not in chosen) or (source and src != source):
				continue
			rate = rates.rate(frappe.get_cached_value("Company", src, "default_currency"), on)
			total += signs.get(meta["section"], 0) * flt(net) * rate
	return total


def message(result, filters, check):
	title = cstr(filters.get("line")).strip() or _("the whole profit")
	notes = [_("%s: %s to %s.") % (frappe.bold(title), filters.from_date, filters.to_date)]
	if filters.get("origin"):
		notes.append(_("%s only.") % _(filters.origin))
	if abs(result["total"] - check) < 0.0005:
		notes.append(_("The vouchers add up to %s, the figure on the MIS Profit and Loss.") % flt(result["total"], 3))
	else:
		notes.append(_("The vouchers add up to %s but the profit and loss's figure is %s: tell the accounts team.") % (flt(result["total"], 3), flt(check, 3)))
	if result["rates"].missing:
		notes.append(_("No exchange rate for %s into %s: those charged-in amounts are left out.") % (", ".join(sorted(result["rates"].missing)), result["currency"]))
	if filters.get("origin") == "Charged out" and not signs_given(filters):
		notes.append(_("Charged out is shown as the amount added back to this company's figures."))
	return "<br>".join(notes)


def execute(filters=None):
	filters = frappe._dict(filters or {})
	result = build(filters)
	report = build_rows(result["entries"], filters.get("group_by") or GROUPS[0], result["currency"])
	data = report.data
	data.append({"label": _("Total"), "indent": 0, "is_total": 1, "bold": 1, "currency": result["currency"],
		"debit": flt(sum(e["debit"] for e in result["entries"]), 6), "credit": flt(sum(e["credit"] for e in result["entries"]), 6),
		"amount": flt(result["total"], 6), "entries": len(result["entries"])})
	summary = [
		{"label": _("Amount"), "value": flt(result["total"], 3), "datatype": "Currency", "currency": result["currency"],
			"indicator": "Green" if result["total"] >= 0 else "Red"},
		{"label": _("Vouchers"), "value": len({(e["voucher_type"], e["voucher_no"]) for e in result["entries"]}), "datatype": "Int"},
		{"label": _("Ledger Entries"), "value": len(result["entries"]), "datatype": "Int"},
		{"label": _("Companies"), "value": len({e["company"] for e in result["entries"]}), "datatype": "Int"},
	]
	return columns(result["currency"]), data, message(result, filters, figure_total(filters, result)), None, summary
