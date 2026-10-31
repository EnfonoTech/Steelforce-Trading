# sf_trading/comparison_statement.py
"""The Quotation Comparison Statement: one printable page per Request for Quotation, for sign-off.

The `RFQ Quotation Comparison Statement` Print Format (Request for Quotation) reads everything it prints from
`comparison_statement`, a Jinja method registered in hooks.py. It is the same comparison the report shows, run over
every date with the RFQ's expired quotations included (so a lapsed quotation reads as lapsed, not as missing), by
default Lowest Cost, and it says what was added to each price. Nothing is worked out in the template: every number
arrives formatted, so what is printed is what the report computed.

The statement is read only. A user who may not read the Request for Quotation, or may not read its quotations, gets a
statement that says so rather than a page that looks complete and is not.
"""

from __future__ import annotations

import frappe
from frappe import _
from frappe.utils import cint, cstr, flt, fmt_money, formatdate, nowdate

from sf_trading.sf_trading.report.supplier_quotation_comparison_extended import (
	supplier_quotation_comparison_extended as report,
)


def comparison_statement(rfq) -> frappe._dict:
	"""Everything the statement prints for one Request for Quotation, ready to place."""
	if not rfq or not frappe.has_permission("Request for Quotation", "read", doc=rfq):
		return frappe._dict(available=False, note=_("You may not read this Request for Quotation."))

	head = frappe.db.get_value(
		"Request for Quotation", rfq, ["name", "company", "transaction_date", "status"], as_dict=True
	)
	comparison = report.compare(
		frappe._dict(
			company=head.company,
			from_date="2000-01-01",
			to_date=nowdate(),
			request_for_quotation=rfq,
			status=report.STATUS_SUBMITTED,
			include_expired=1,
		)
	)
	currency = comparison.company_currency

	def money(value, precision=None):
		return fmt_money(flt(value), precision=precision, currency=currency) if value is not None else ""

	items = statement_items(comparison, money)
	suppliers = statement_suppliers(comparison, money)
	gaps = [
		{
			"gap": row["gap"],
			"supplier": cstr(row.get("supplier_name") or row.get("supplier")),
			"item": cstr(row.get("item_code")),
			"detail": cstr(row.get("detail")),
		}
		for row in report.gap_rows(comparison)
	]
	single = next((row for row in suppliers if row["basket_rank"] == 1), None)
	awarded = sum(quote.award_total for quote in comparison.quotes)

	return frappe._dict(
		available=bool(comparison.quotes) or bool(comparison.requested),
		note="" if comparison.quotes else _("No quotation has been received on this Request for Quotation."),
		rfq=head.name,
		company=head.company,
		date=formatdate(head.transaction_date),
		status=head.status,
		printed_on=formatdate(nowdate()),
		currency=currency,
		basis=_("Net price per stock unit in %s, before tax, plus the estimated charges: %s.")
		% (currency, report.describe_charges(comparison.use_quotation, comparison.percent)),
		items=items,
		suppliers=suppliers,
		gaps=gaps,
		award_total=money(awarded),
		cheapest_single=single["supplier"] if single else "",
		cheapest_single_total=single["basket_total"] if single else "",
	)


def statement_items(comparison, money) -> list:
	"""One block per item: its quotations ranked, and the lines that are awarded."""
	order, by_item = [], {}
	for row in report.item_rows(comparison):
		item = row["item_code"]
		if item not in by_item:
			by_item[item] = frappe._dict(
				item=item,
				name=cstr(row.get("item_name")),
				requested=flt(row.get("requested_qty"), 3),
				uom=cstr(row.get("stock_uom")),
				rule=cstr(row.get("rule_label")),
				note="",
				quotes=[],
				awards=[],
			)
			order.append(item)
		block = by_item[item]
		if not row.get("quotation"):
			block["note"] = cstr(row.get("remarks"))
			continue
		line = {
			"rank": cstr(row.get("rank") or ""),
			"supplier": cstr(row.get("supplier_name") or row.get("supplier")),
			"quotation": cstr(row.get("quotation")),
			"origin": cstr(row.get("origin")),
			"currency": cstr(row.get("currency")),
			"price": money(row.get("unit_cost_base"), 4),
			"charges": money(row.get("charge_unit"), 4) if flt(row.get("charge_unit")) else "",
			"charge_detail": cstr(row.get("charge_detail")),
			"landed": money(row.get("landed_unit_cost"), 4),
			"over": ("%s%%" % flt(row.get("pct_over_best"), 1)) if row.get("pct_over_best") else "",
			"offered": ("%s%%" % flt(row.get("qty_offered_pct"), 0)) if row.get("qty_offered_pct") is not None else "",
			"award_qty": cstr(flt(row.get("award_qty"), 3)) if flt(row.get("award_qty")) else "",
			"award_total": money(row.get("award_total")) if flt(row.get("award_qty")) else "",
			"lead": cstr(row.get("lead_time_days") or ""),
			"valid_till": formatdate(row.get("valid_till")) if row.get("valid_till") else "",
			"recommended": cint(row.get("recommended")),
			"remarks": cstr(row.get("remarks")),
		}
		block["quotes"].append(line)
		if flt(row.get("award_qty")):
			block["awards"].append(line)
	return [dict(by_item[item], item=item) for item in order]


def statement_suppliers(comparison, money) -> list:
	"""One line per supplier: how much of the request it covered and what it would cost."""
	return [
		{
			"supplier": cstr(row.get("supplier_name") or row.get("supplier")),
			"response": cstr(row.get("response")),
			"origin": cstr(row.get("origin")),
			"quoted": "%s / %s" % (row.get("items_quoted") or 0, row.get("items_requested") or 0),
			"awarded": cstr(row.get("items_awarded") or 0),
			"basket_total": money(row.get("basket_total")) if row.get("basket_total") is not None else "",
			"basket_rank": row.get("basket_rank"),
			"award_total": money(row.get("won_total")) if row.get("won_total") is not None else "",
			"lead": cstr(row.get("max_lead_time") or ""),
			"recommendation": cstr(row.get("recommendation")),
		}
		for row in report.supplier_rows(comparison)
	]
