# sf_trading/api/selling_history.py
"""What an item has been selling for, read from the buyer's side of the counter.

A buyer agreeing a purchase price wants the other half of the picture: what the branch has actually
been getting for the same item lately, and at what quantities. ERPNext offers Last Purchase Rate on
the order; this is its mirror on the selling side.

Rates are `Sales Invoice Item.net_rate` -- after discount, before tax -- because that is what the
customer really paid per unit and the only figure comparable with a purchase rate. `base_net_rate`
is used when the invoice is in another currency, so every number in one column is company currency,
and the payload names that currency so the dialog cannot label it with the order's.

What is left out, and why:
  * returns -- a credit note carries a negative quantity at the original rate, and letting it in
    would show a sale that was undone as though it were demand
  * free and sample lines (rate 0) -- not a price anybody agreed, and one of them drags the Lowest
    column down to nothing
  * internal customers, consolidated POS invoices and debit notes -- a transfer price, a duplicate
    of the POS invoices underneath it, and a zero-quantity adjustment respectively

Each item is read separately with its own share of the limit. One flat limit across a whole order
lets the busiest item eat it and reports every other row as "never sold", which is exactly the
wrong answer for a buyer about to agree a price.
"""

import frappe
from frappe import _
from frappe.query_builder.functions import IfNull
from frappe.utils import add_months, cint, flt, getdate, nowdate

# How far back to look when the caller says nothing. A year of a paint item's history is enough to
# see the trend without dragging the whole ledger into a dialog.
DEFAULT_MONTHS = 12
DEFAULT_LIMIT = 200
MIN_ROWS_PER_ITEM = 10
MAX_ROWS_PER_ITEM = 60

# invoices that are not a customer paying a price; skipped when the field exists on this version
NOT_A_SALE = ("is_internal_customer", "is_consolidated", "is_debit_note")


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
		limit: rows in total, shared out per item so no single item crowds out the rest
	"""
	frappe.has_permission("Sales Invoice", "read", throw=True)

	if isinstance(items, str):
		items = frappe.parse_json(items)
	items = list(dict.fromkeys([code for code in (items or []) if code]))

	companies, permitted_branches = _permitted()
	if company and companies and company not in companies:
		frappe.throw(_("You are not permitted to see {0}.").format(company), frappe.PermissionError)

	company_currency = _company_currency(company)
	if not items:
		return {"rows": [], "summary": [], "currency": company_currency, "filters": {}}

	to_date = getdate(to_date or nowdate())
	from_date = getdate(from_date or add_months(to_date, -DEFAULT_MONTHS))
	limit = min(cint(limit) or DEFAULT_LIMIT, 1000)
	branches = _branches_to_read(cost_center, all_branches, permitted_branches)
	per_item = max(MIN_ROWS_PER_ITEM, min(MAX_ROWS_PER_ITEM, limit // len(items) or limit))

	rows = []
	for item_code in items:
		rows.extend(
			_recent_sales(
				item_code,
				company=company,
				companies=companies,
				branches=branches,
				customer=customer,
				from_date=from_date,
				to_date=to_date,
				per_item=per_item,
				company_currency=company_currency,
			)
		)

	rows.sort(key=lambda r: (str(r["posting_date"]), r["posting_time"] or "", r["invoice"]), reverse=True)

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
			"branches": branches,
			"customer": customer,
			"limit": limit,
			"rows_per_item": per_item,
		},
	}


def _permitted():
	"""The companies and branches this user may see, or None where unrestricted.

	`frappe.qb` applies no User Permissions of its own, and every other selling-side endpoint in
	this app (api/last_selling_rate.py) restricts on exactly these two. Without it a buyer limited
	to one branch could name any other branch's cost centre and read its rates and its customers.
	"""
	from frappe.core.doctype.user_permission.user_permission import get_permitted_documents

	return get_permitted_documents("Company") or None, get_permitted_documents("Cost Center") or None


def _branches_to_read(cost_center, all_branches, permitted) -> list | None:
	"""Which cost centres to report; None means every one the user may see.

	`all_branches` is the client's own ask -- a buyer comparing what other branches get -- so it
	widens as far as that user's permissions reach and no further.
	"""
	if cost_center and permitted and cost_center not in permitted:
		frappe.throw(
			_("You are not permitted to see the branch {0}.").format(cost_center),
			frappe.PermissionError,
		)

	if cint(all_branches):
		return permitted

	if cost_center:
		return [cost_center]

	return permitted


def _recent_sales(
	item_code,
	*,
	company,
	companies,
	branches,
	customer,
	from_date,
	to_date,
	per_item,
	company_currency,
) -> list:
	invoice = frappe.qb.DocType("Sales Invoice")
	row = frappe.qb.DocType("Sales Invoice Item")
	meta = frappe.get_meta("Sales Invoice")

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
			row.cost_center,
			invoice.name.as_("invoice"),
			invoice.posting_date,
			invoice.posting_time,
			invoice.customer,
			invoice.customer_name,
			invoice.currency,
			invoice.cost_center.as_("invoice_cost_center"),
		)
		.where(
			(invoice.docstatus == 1)
			& (invoice.is_return == 0)
			& (row.item_code == item_code)
			& (invoice.posting_date >= from_date)
			& (invoice.posting_date <= to_date)
			& (row.qty > 0)
			& (row.rate > 0)
		)
		# newest first, with posting_time breaking the tie the way erpnext's own last-purchase
		# lookup does; without it two same-day invoices order arbitrarily and Last Rate flickers
		.orderby(invoice.posting_date, order=frappe.qb.desc)
		.orderby(invoice.posting_time, order=frappe.qb.desc)
		.orderby(invoice.name, order=frappe.qb.desc)
		.limit(per_item)
	)

	for flag in NOT_A_SALE:
		if meta.get_field(flag):
			query = query.where(IfNull(getattr(invoice, flag), 0) == 0)

	if company:
		query = query.where(invoice.company == company)
	elif companies:
		query = query.where(invoice.company.isin(companies))

	if customer:
		query = query.where(invoice.customer == customer)

	if branches:
		# the row's own cost centre decides, falling back to the invoice header when it is blank --
		# the convention the DCR reports already follow
		query = query.where(
			row.cost_center.isin(branches)
			| ((IfNull(row.cost_center, "") == "") & invoice.cost_center.isin(branches))
		)

	rows = []
	for record in query.run(as_dict=True):
		# one column, one currency: a foreign invoice is reported at its company-currency rate
		rate = flt(record.base_net_rate) or flt(record.net_rate) or flt(record.rate)
		rows.append(
			{
				"item_code": record.item_code,
				"item_name": record.item_name,
				"posting_date": record.posting_date,
				"posting_time": str(record.posting_time or ""),
				"invoice": record.invoice,
				"customer": record.customer,
				"customer_name": record.customer_name,
				"cost_center": record.cost_center or record.invoice_cost_center,
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
	return rows


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

	summary.sort(key=lambda e: str(e["last_date"] or ""), reverse=True)
	return summary
