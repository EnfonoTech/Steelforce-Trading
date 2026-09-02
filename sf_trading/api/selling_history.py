# sf_trading/api/selling_history.py
"""What an item has been selling for, read from the buyer's side of the counter.

A buyer agreeing a purchase price wants the other half of the picture: what the branch has actually
been getting for the same item lately, and at what quantities. ERPNext offers Last Purchase Rate on
the order; this is its mirror on the selling side.

Rates are `Sales Invoice Item.net_rate` -- after discount, before tax -- because that is what the
customer really paid per unit and the only figure comparable with a purchase rate. `base_net_rate`
is used when the invoice is in another currency, so every number in one column is company currency.

Returns are excluded. A credit note carries a negative quantity at the original rate, and letting
it in would show the buyer a sale that was undone as though it were demand.
"""

import frappe
from frappe import _
from frappe.utils import add_months, cint, flt, getdate, nowdate

# How far back to look when the caller says nothing. A year of a paint item's history is enough to
# see the trend without dragging the whole ledger into a dialog.
DEFAULT_MONTHS = 12
DEFAULT_LIMIT = 200


@frappe.whitelist()
def get_selling_history(
	items: str | list,
	company: str = None,
	cost_center: str = None,
	all_branches: int | str = 0,
	from_date: str = None,
	to_date: str = None,
	customer: str = None,
	limit: int | str = DEFAULT_LIMIT,
) -> dict:
	"""Recent sales of these items, newest first.

	Args:
		items: item codes (JSON list or list) -- the order's own rows
		cost_center: the branch to report; ignored when all_branches is set
		all_branches: 1 to look past the branch, for a buyer comparing what other branches get
		from_date / to_date: the window, defaulting to the last twelve months
		customer: narrow to one customer
		limit: rows returned, newest first
	"""
	frappe.has_permission("Sales Invoice", "read", throw=True)

	if isinstance(items, str):
		items = frappe.parse_json(items)
	items = [code for code in (items or []) if code]
	if not items:
		return {"rows": [], "summary": [], "currency": _company_currency(company), "filters": {}}

	to_date = getdate(to_date or nowdate())
	from_date = getdate(from_date or add_months(to_date, -DEFAULT_MONTHS))
	limit = min(cint(limit) or DEFAULT_LIMIT, 1000)
	company_currency = _company_currency(company)

	invoice = frappe.qb.DocType("Sales Invoice")
	row = frappe.qb.DocType("Sales Invoice Item")

	query = (
		frappe.qb.from_(row)
		.join(invoice)
		.on(invoice.name == row.parent)
		.select(
			row.item_code,
			row.item_name,
			row.qty,
			row.uom,
			row.rate,
			row.net_rate,
			row.base_net_rate,
			row.amount,
			row.cost_center,
			invoice.name.as_("invoice"),
			invoice.posting_date,
			invoice.customer,
			invoice.customer_name,
			invoice.currency,
		)
		.where(
			(invoice.docstatus == 1)
			& (invoice.is_return == 0)
			& (row.item_code.isin(items))
			& (invoice.posting_date >= from_date)
			& (invoice.posting_date <= to_date)
			& (row.qty > 0)
		)
		.orderby(invoice.posting_date, order=frappe.qb.desc)
		.orderby(invoice.name, order=frappe.qb.desc)
		.limit(limit)
	)

	if company:
		query = query.where(invoice.company == company)
	if customer:
		query = query.where(invoice.customer == customer)
	if not cint(all_branches) and cost_center:
		query = query.where(row.cost_center == cost_center)

	records = query.run(as_dict=True)

	rows = []
	for record in records:
		# one column, one currency: a foreign invoice is reported at its company-currency rate
		rate = flt(record.base_net_rate) or flt(record.net_rate) or flt(record.rate)
		rows.append(
			{
				"item_code": record.item_code,
				"item_name": record.item_name,
				"posting_date": record.posting_date,
				"invoice": record.invoice,
				"customer": record.customer,
				"customer_name": record.customer_name,
				"cost_center": record.cost_center,
				"qty": flt(record.qty, 3),
				"uom": record.uom,
				"rate": flt(rate, 3),
				# only claimed when the company currency is actually known; without it every row
				# would be labelled foreign, which is worse than saying nothing
				"foreign": record.currency
				if company_currency and record.currency != company_currency
				else None,
			}
		)

	return {
		"rows": rows,
		"summary": _summarise(rows),
		# the dialog formats every figure with this, NOT the order's own currency: a purchase order
		# may be raised in the supplier's currency while these rates are all company currency
		"currency": company_currency,
		"filters": {
			"from_date": str(from_date),
			"to_date": str(to_date),
			"cost_center": None if cint(all_branches) else cost_center,
			"all_branches": cint(all_branches),
			"customer": customer,
			"limit": limit,
		},
	}


def _company_currency(company):
	if company:
		return frappe.get_cached_value("Company", company, "default_currency")
	return frappe.defaults.get_global_default("currency")


def _summarise(rows) -> list:
	"""One line per item: how often, how much, and the range of rates it went out at.

	The last rate is what a negotiation opens with; the lowest is what the buyer has to beat for
	the branch to keep its margin, so both are given rather than an average alone.
	"""
	per_item = {}
	for row in rows:
		entry = per_item.setdefault(
			row["item_code"],
			{
				"item_code": row["item_code"],
				"item_name": row["item_name"],
				"invoices": 0,
				"qty": 0.0,
				"value": 0.0,
				"last_rate": None,
				"last_date": None,
				"low_rate": None,
				"high_rate": None,
			},
		)
		entry["invoices"] += 1
		entry["qty"] += flt(row["qty"])
		entry["value"] += flt(row["qty"]) * flt(row["rate"])
		if entry["last_date"] is None or getdate(row["posting_date"]) > getdate(entry["last_date"]):
			entry["last_date"] = row["posting_date"]
			entry["last_rate"] = row["rate"]
		rate = flt(row["rate"])
		entry["low_rate"] = rate if entry["low_rate"] is None else min(entry["low_rate"], rate)
		entry["high_rate"] = rate if entry["high_rate"] is None else max(entry["high_rate"], rate)

	summary = []
	for entry in per_item.values():
		entry["qty"] = flt(entry["qty"], 3)
		entry["avg_rate"] = flt(entry["value"] / entry["qty"], 3) if entry["qty"] else 0.0
		entry.pop("value")
		summary.append(entry)

	summary.sort(key=lambda e: (e["last_date"] or ""), reverse=True)
	return summary
