# Copyright (c) 2026, Enfono Technologies and contributors
# For license information, please see license.txt

"""Supplier Quotation Comparison, extended: who to buy each item from, and who to give the whole Request for Quotation to.

Core's Supplier Quotation Comparison lines the quotations up and marks the cheapest price per unit in green. It does not
say which supplier to pick, it compares a local quotation with an import one only by the currency it was typed in, and it
shows nothing for a supplier who never answered. This report keeps what core does and adds the decision.

THREE VIEWS (the `View` filter)
-------------------------------
Item-wise Comparison   one line per quotation line, grouped by Request for Quotation and item, ranked, with the
                       recommended line marked.
Supplier-wise Summary  one line per supplier per Request for Quotation: how much of the request they covered, how many
                       items they win, what their basket costs, and which supplier is the cheapest single award.
RFQ Gaps               what is missing: suppliers who never answered, items a supplier left out, items nobody quoted,
                       items short of the quotations the Purchase Order rules ask for.

WHAT "CHEAPEST" MEANS
---------------------
Quotations are put on one footing before they are compared, because a raw price is not comparable:

    * the price is the NET rate (after the line and document discount, before tax), per STOCK unit, so a quotation in
      cartons and one in pieces compare;
    * it is in the COMPANY currency, converted at the rate on the quotation;
    * an import quotation carries `Import Charges %` on top (freight, duty, clearance), so a cheap price abroad is not
      read as a cheap landed cost. A local quotation carries none: its VAT is reclaimable and is left out of both.

That figure is the landed unit cost. The recommended quotation for an item is the one with the lowest landed unit cost
among the quotations that can still be bought from. Ties go to the shorter stated lead time, then to the earlier
quotation. A lead time of 0 means "not stated" and loses a tie to one that is stated.

NOT ELIGIBLE: an Expired quotation (status, or Valid Till already past), a Stopped one, one with no price. They stay on
the report, marked, and are never recommended. A Draft quotation can be recommended (buyers compare before they submit)
but a draft cannot be turned into a Purchase Order, and the order button says so.

One supplier counts once per item. A supplier with two quotations for the same item competes with its lower one, and the
other is shown as superseded, so "three quotations" always means three suppliers, as the Purchase Order rule counts them.

QUANTITY
--------
Every landed total is for the quantity the REQUEST asked for, not what the supplier chose to offer, so two suppliers
compare on the same basket. A supplier who offered less is flagged by `Qty Offered %` and is not penalised in the rank:
the price is what is ranked, the shortfall is what the buyer reads next to it.

LOCAL AND IMPORT
----------------
A quotation is IMPORT when it is in a currency other than the company's (the very test the Purchase Order rules use to
decide an order is an import) or, failing that, when the supplier's own country is set and is not the company's. A
supplier's country is the weak test and goes last: few supplier records here carry one.

`Purchase Origin` keeps one half or both. The Purchase Order rules ask a different number of quotations of each half
(SF Trading Settings > Purchasing > Purchase Order Rules), and the `Procurement Rule` column reports each item against it:
Met, Short, Not required (a local order at or under the rule amount), or blank when the company has no rule switched on.
A local order is held to the rule only above an amount, and the order does not exist yet, so the amount tested is the
recommended quotation's own grand total. Under an origin filter the other half is simply not there, so a supplier or an
item with nothing of the chosen origin reads as not quoted; the message line above the report says so.

RFQ SCOPE
---------
The items and suppliers an RFQ asked are read from the Request for Quotation itself, so an item nobody quoted and a
supplier who never answered are visible. A quotation that names no RFQ is compared with the others for the same item in
the period under an empty RFQ, and its Procurement Rule says it was not raised from one. When no item, supplier or
quotation filter is set, RFQs raised in the period with no quotation at all are listed too.

LAST PURCHASE RATE
------------------
Read from the Item master (`last_purchase_rate`, per stock unit, company currency, the figure the selling-price guard
already trusts), so it is one read for the whole page. The percentage is measured against the quotation's price BEFORE the
import charge estimate, like for like with what a Purchase Receipt records.

CREATE PURCHASE ORDERS
----------------------
`get_recommended_orders` lists the recommended lines grouped by quotation; `make_purchase_orders` raises a DRAFT Purchase
Order for each chosen quotation using core's own mapper, restricted to the recommended lines. Both recompute the
recommendation on the server from the filters, and never trust a list of lines sent from the browser. A line already on a
live Purchase Order is left out. The Purchase Order procurement rules run on the draft like on any other, so an order
that does not meet them is refused with the rule's own message and nothing is created for it.
"""

from __future__ import annotations

import frappe
from frappe import _
from frappe.utils import cint, cstr, escape_html, flt, getdate, nowdate

from sf_trading.purchase_order_procurement import rules as procurement_rules
from sf_trading.query import fetch_in
from sf_trading.sf_trading.report.purchase_register_extended.purchase_register_extended import (
	company_profile,
	suppliers_abroad,
)

VIEW_ITEM = "Item-wise Comparison"
VIEW_SUPPLIER = "Supplier-wise Summary"
VIEW_GAPS = "RFQ Gaps"
VIEWS = (VIEW_ITEM, VIEW_SUPPLIER, VIEW_GAPS)

ORIGIN_BOTH = "Local and Import"
ORIGIN_LOCAL = "Local Only"
ORIGIN_IMPORT = "Import Only"
LOCAL = "Local"
IMPORT = "Import"

STATUS_SUBMITTED = "Submitted"
STATUS_DRAFT = "Draft"
STATUS_BOTH = "Draft and Submitted"

#: the group key of quotations that name no Request for Quotation
NO_RFQ = ""

#: two landed unit costs closer than this are a tie; a quotation price rarely has more than four decimals
COST_PRECISION = 4
#: sorts after everything real, so "not stated" loses a tie
FAR = 10**9

LINE_FIELDS = [
	"name",
	"parent",
	"idx",
	"item_code",
	"item_name",
	"qty",
	"uom",
	"stock_uom",
	"stock_qty",
	"conversion_factor",
	"rate",
	"amount",
	"net_rate",
	"net_amount",
	"base_net_amount",
	"base_amount",
	"lead_time_days",
	"request_for_quotation",
	"request_for_quotation_item",
]

HEADER_FIELDS = [
	"name",
	"supplier",
	"supplier_name",
	"currency",
	"conversion_rate",
	"transaction_date",
	"valid_till",
	"status",
	"docstatus",
	"base_grand_total",
]


# ---------------------------------------------------------------------------
# entry point
# ---------------------------------------------------------------------------


def execute(filters=None):
	filters = frappe._dict(filters or {})
	validate_filters(filters)

	comparison = compare(filters)
	view = resolve_view(filters)
	if view == VIEW_SUPPLIER:
		columns, rows = get_supplier_columns(), supplier_rows(comparison)
	elif view == VIEW_GAPS:
		columns, rows = get_gap_columns(), gap_rows(comparison)
	else:
		columns, rows = get_item_columns(), item_rows(comparison)

	return columns, rows, get_message(comparison)


def validate_filters(filters):
	if not filters.get("company"):
		frappe.throw(_("Company is required"))
	if not (filters.get("from_date") and filters.get("to_date")):
		frappe.throw(_("From Date and To Date are required"))
	if getdate(filters.from_date) > getdate(filters.to_date):
		frappe.throw(_("From Date cannot be after To Date"))
	if flt(filters.get("import_charges_pct")) < 0:
		frappe.throw(_("Import Charges % cannot be negative"))


def resolve_view(filters):
	view = filters.get("view")
	return view if view in VIEWS else VIEW_ITEM


def resolve_origin(filters):
	origin = filters.get("purchase_origin")
	return origin if origin in (ORIGIN_BOTH, ORIGIN_LOCAL, ORIGIN_IMPORT) else ORIGIN_BOTH


def as_list(value) -> list:
	"""A MultiSelectList filter arrives as a list from the desk and as JSON or comma text from anywhere else."""
	if not value:
		return []
	if isinstance(value, (list, tuple, set)):
		return [cstr(v) for v in value if v]
	text = cstr(value).strip()
	if text.startswith("["):
		try:
			return [cstr(v) for v in frappe.parse_json(text) if v]
		except Exception:
			pass
	return [part.strip() for part in text.split(",") if part.strip()]


def docstatus_clause(filters):
	"""A Select with a word for every choice: query_report.js drops a filter whose value is empty, so "both" cannot be blank."""
	status = filters.get("status")
	if status == STATUS_DRAFT:
		return 0
	if status == STATUS_BOTH:
		return ["<", 2]
	return 1


# ---------------------------------------------------------------------------
# reading the quotations and the requests
# ---------------------------------------------------------------------------


def per_stock_unit(amount, rate, stock_qty, conversion_factor) -> float:
	"""The price of ONE stock unit: the line amount over its stock quantity, as core's comparison does."""
	if flt(stock_qty):
		return flt(amount) / flt(stock_qty)
	return flt(rate) / (flt(conversion_factor) or 1)


def build_quote(header, line, company_currency) -> frappe._dict:
	"""One quotation line on one footing: the net price per stock unit, in the quotation's currency and the company's."""
	conversion_rate = flt(header.get("conversion_rate")) or 1
	stock_qty = flt(line.get("stock_qty")) or flt(line.get("qty")) * (flt(line.get("conversion_factor")) or 1)

	amount = flt(line.get("net_amount")) or flt(line.get("amount"))
	base_amount = flt(line.get("base_net_amount")) or flt(line.get("base_amount")) or amount * conversion_rate
	rate = flt(line.get("net_rate")) or flt(line.get("rate"))
	unit_cost_base = per_stock_unit(base_amount, rate * conversion_rate, stock_qty, line.get("conversion_factor"))

	return frappe._dict(
		rfq=cstr(line.get("request_for_quotation")).strip(),
		rfq_item=cstr(line.get("request_for_quotation_item")).strip(),
		item_code=line.get("item_code"),
		item_name=line.get("item_name"),
		supplier=header.get("supplier"),
		supplier_name=header.get("supplier_name"),
		quotation=header.get("name"),
		quotation_item=line.get("name"),
		transaction_date=header.get("transaction_date"),
		valid_till=header.get("valid_till"),
		status=header.get("status"),
		docstatus=cint(header.get("docstatus")),
		quotation_total=flt(header.get("base_grand_total")),
		currency=header.get("currency") or company_currency,
		company_currency=company_currency,
		qty=flt(line.get("qty")),
		uom=line.get("uom"),
		stock_uom=line.get("stock_uom"),
		stock_qty=stock_qty,
		lead_time_days=cint(line.get("lead_time_days")) or None,
		unit_cost_txn=per_stock_unit(amount, rate, stock_qty, line.get("conversion_factor")),
		unit_cost_base=unit_cost_base,
		priced=unit_cost_base > 0,
	)


def get_quotes(filters, company_currency) -> list:
	"""Every quotation line the user may read in the period, with its quotation's header on it."""
	where = {
		"company": filters.company,
		"transaction_date": ["between", [filters.from_date, filters.to_date]],
		"docstatus": docstatus_clause(filters),
	}
	suppliers = as_list(filters.get("supplier"))
	if suppliers:
		where["supplier"] = ["in", suppliers]
	named = as_list(filters.get("supplier_quotation"))
	if named:
		where["name"] = ["in", named]

	headers = frappe.get_list(
		"Supplier Quotation",
		filters=where,
		fields=HEADER_FIELDS,
		order_by="transaction_date asc, name asc",
		limit_page_length=0,
	)
	if not headers:
		return []
	header_of = {header.name: header for header in headers}

	line_filters = {"parenttype": "Supplier Quotation"}
	if filters.get("item_code"):
		line_filters["item_code"] = filters.item_code
	if filters.get("request_for_quotation"):
		line_filters["request_for_quotation"] = filters.request_for_quotation

	lines = fetch_in(
		"Supplier Quotation Item",
		list(header_of),
		in_field="parent",
		filters=line_filters,
		fields=LINE_FIELDS,
		order_by="parent asc, idx asc",
	)
	return [build_quote(header_of[line.parent], line, company_currency) for line in lines]


def get_request_names(quotes, filters) -> list:
	"""The Requests to compare: the one asked for, or those the quotations name plus, when nothing narrows the set, the
	Requests raised in the period that nobody has answered yet."""
	named = filters.get("request_for_quotation")
	if named:
		return [named]

	names = {quote.rfq for quote in quotes if quote.rfq}
	narrowed = filters.get("item_code") or as_list(filters.get("supplier")) or as_list(filters.get("supplier_quotation"))
	if not narrowed and resolve_origin(filters) == ORIGIN_BOTH:
		names.update(
			frappe.get_list(
				"Request for Quotation",
				filters={
					"company": filters.company,
					"transaction_date": ["between", [filters.from_date, filters.to_date]],
					"docstatus": 1,
				},
				pluck="name",
				limit_page_length=0,
			)
		)
	return sorted(names)


def get_request_scope(rfqs, filters):
	"""What each Request for Quotation asked, and of whom.

	Returns (requested, invited):
	    requested  {rfq: {item_code: {item_name, qty (stock units), stock_uom}}}
	    invited    {rfq: [{supplier, supplier_name, quote_status}]}
	An item or supplier filter narrows both, so a comparison set to one item does not list the rest of the request as gaps.
	"""
	requested, invited = {}, {}
	if not rfqs:
		return requested, invited

	only_item = filters.get("item_code")
	only_suppliers = set(as_list(filters.get("supplier")))

	for row in fetch_in(
		"Request for Quotation Item",
		rfqs,
		in_field="parent",
		filters={"parenttype": "Request for Quotation"},
		fields=["parent", "item_code", "item_name", "qty", "stock_qty", "conversion_factor", "stock_uom"],
		order_by="parent asc, idx asc",
	):
		if only_item and row.item_code != only_item:
			continue
		qty = flt(row.stock_qty) or flt(row.qty) * (flt(row.conversion_factor) or 1)
		slot = requested.setdefault(row.parent, {}).setdefault(
			row.item_code, frappe._dict(item_name=row.item_name, qty=0.0, stock_uom=row.stock_uom)
		)
		slot.qty += qty

	for row in fetch_in(
		"Request for Quotation Supplier",
		rfqs,
		in_field="parent",
		filters={"parenttype": "Request for Quotation"},
		fields=["parent", "supplier", "supplier_name", "quote_status"],
		order_by="parent asc, idx asc",
	):
		if only_suppliers and row.supplier not in only_suppliers:
			continue
		invited.setdefault(row.parent, []).append(row)

	return requested, invited


def get_last_rates(items) -> dict:
	"""{item_code: last purchase rate} from the Item master, per stock unit, in company currency."""
	rows = fetch_in("Item", items, fields=["name", "last_purchase_rate"])
	return {row.name: flt(row.last_purchase_rate) for row in rows}


# ---------------------------------------------------------------------------
# putting the quotations on one footing
# ---------------------------------------------------------------------------


def is_expired(quote, today) -> bool:
	if quote.status == "Expired":
		return True
	return bool(quote.valid_till and getdate(quote.valid_till) < getdate(today))


def ineligible_reason(quote, today) -> str:
	"""Why this quotation cannot be recommended, or empty when it can."""
	if quote.status == "Stopped":
		return _("Stopped")
	if is_expired(quote, today):
		return _("Expired")
	if not quote.priced:
		return _("No price")
	return ""


def quote_origin(quote, company_currency, abroad) -> str:
	"""Import when quoted in another currency, or the supplier's own country says it is abroad."""
	if company_currency and quote.currency != company_currency:
		return IMPORT
	if quote.supplier in abroad:
		return IMPORT
	return LOCAL


def landed_unit_cost(quote, import_charges_pct) -> float:
	"""The comparable price: net, per stock unit, company currency, plus the import estimate on an import."""
	cost = flt(quote.unit_cost_base)
	if quote.origin == IMPORT:
		cost *= 1 + flt(import_charges_pct) / 100
	return cost


def sort_key(quote):
	"""Cheapest first; a stated lead time beats an unstated one; the earlier quotation beats the later."""
	return (
		flt(quote.landed_unit_cost, COST_PRECISION),
		quote.lead_time_days or FAR,
		getdate(quote.transaction_date).toordinal() if quote.transaction_date else FAR,
		cstr(quote.quotation),
		cstr(quote.quotation_item),
	)


def rank_group(group):
	"""Rank the quotations of ONE item on ONE request, and mark the recommended one.

	Each supplier competes once, with its lowest eligible quotation. Sets on every quote:
	    rank            1.. for the quotations in the running, None otherwise
	    recommended     1 on the winner
	    pct_over_best   how far above the winner, in percent
	    saving_vs_next  on the winner only: what it saves over the runner-up, for the requested quantity
	    remarks         why a quotation is out, or why the winner won
	"""
	for quote in group:
		quote.rank = None
		quote.recommended = 0
		quote.pct_over_best = None
		quote.saving_vs_next = None
		quote.remarks = quote.ineligible

	running, seen = [], set()
	for quote in sorted((q for q in group if not q.ineligible), key=sort_key):
		if quote.supplier in seen:
			quote.remarks = _("Superseded by a lower quotation from this supplier")
			continue
		seen.add(quote.supplier)
		running.append(quote)
	if not running:
		return

	best = running[0]
	best_cost = flt(best.landed_unit_cost)
	for position, quote in enumerate(running, 1):
		quote.rank = position
		quote.pct_over_best = (flt(quote.landed_unit_cost) / best_cost - 1) * 100 if best_cost else 0.0
	best.recommended = 1

	if len(running) == 1:
		best.remarks = _("Only quotation in the running")
		return

	runner_up = running[1]
	best.saving_vs_next = (flt(runner_up.landed_unit_cost) - best_cost) * flt(best.requested_qty)
	if flt(runner_up.landed_unit_cost, COST_PRECISION) != flt(best.landed_unit_cost, COST_PRECISION):
		best.remarks = _("Lowest landed cost")
	elif (runner_up.lead_time_days or FAR) != (best.lead_time_days or FAR):
		best.remarks = _("Same cost, shorter lead time")
	else:
		best.remarks = _("Same cost and lead time, earlier quotation")


def procurement_check(group, rule, best):
	"""(label, received, needed) for one item against the Purchase Order rules of the company.

	`received` counts different suppliers with a SUBMITTED quotation for the item on the request, expired ones included,
	which is how the Purchase Order rule counts them. A blank label means the company has no rule switched on."""
	received = len({quote.supplier for quote in group if quote.docstatus == 1})
	if not rule.enforce:
		return "", received, 0
	if not group[0].rfq:
		return _("Not raised from an RFQ"), received, 0

	pick = best or sorted(group, key=sort_key)[0]
	if pick.origin == IMPORT:
		needed = rule.import_min
	elif flt(pick.quotation_total) > flt(rule.above):
		needed = rule.local_min
	else:
		needed = 0
	if not needed:
		return _("Not required"), received, 0
	label = _("Met ({0} of {1})") if received >= needed else _("Short ({0} of {1})")
	return label.format(received, needed), received, needed


def compare(filters) -> frappe._dict:
	"""Everything the three views draw from: the quotations on one footing and ranked, the requests, the rules."""
	company_currency, company_country = company_profile(filters.company)
	charges = flt(filters.get("import_charges_pct"))
	today = nowdate()

	quotes = get_quotes(filters, company_currency)
	abroad = suppliers_abroad({quote.supplier for quote in quotes if quote.supplier}, company_country)
	wanted = resolve_origin(filters)

	kept = []
	for quote in quotes:
		quote.origin = quote_origin(quote, company_currency, abroad)
		if wanted == ORIGIN_LOCAL and quote.origin != LOCAL:
			continue
		if wanted == ORIGIN_IMPORT and quote.origin != IMPORT:
			continue
		kept.append(quote)
	quotes = kept

	rfqs = get_request_names(quotes, filters)
	requested, invited = get_request_scope(rfqs, filters)
	items = {quote.item_code for quote in quotes} | {item for asked in requested.values() for item in asked}
	last_rates = get_last_rates(items)
	rule = procurement_rules(filters.company)

	groups = {}
	for quote in quotes:
		wants = requested.get(quote.rfq, {}).get(quote.item_code)
		quote.requested_qty = flt(wants.qty) if wants else flt(quote.stock_qty)
		quote.qty_offered_pct = (flt(quote.stock_qty) / quote.requested_qty * 100) if quote.requested_qty else None
		quote.expired = is_expired(quote, today)
		quote.ineligible = ineligible_reason(quote, today)
		quote.landed_unit_cost = landed_unit_cost(quote, charges)
		quote.import_charge_unit = quote.landed_unit_cost - flt(quote.unit_cost_base)
		quote.landed_total = quote.landed_unit_cost * quote.requested_qty
		quote.last_purchase_rate = last_rates.get(quote.item_code) or None
		quote.vs_last_purchase_pct = (
			(flt(quote.unit_cost_base) / quote.last_purchase_rate - 1) * 100 if quote.last_purchase_rate else None
		)
		groups.setdefault((quote.rfq, quote.item_code), []).append(quote)

	for group in groups.values():
		rank_group(group)
		best = next((quote for quote in group if quote.recommended), None)
		label, received, needed = procurement_check(group, rule, best)
		for quote in group:
			quote.rule_label, quote.quotes_received, quote.quotes_needed = label, received, needed

	return frappe._dict(
		filters=filters,
		company_currency=company_currency,
		charges=charges,
		today=today,
		quotes=quotes,
		groups=groups,
		rfqs=rfqs,
		requested=requested,
		invited=invited,
		rule=rule,
		show_expired=bool(cint(filters.get("include_expired"))),
	)


def visible(comparison, quote) -> bool:
	return comparison.show_expired or not quote.expired


def groups_by_request(comparison) -> dict:
	"""{rfq: {item_code: [quotes]}}"""
	by_rfq = {}
	for (rfq, item), group in comparison.groups.items():
		by_rfq.setdefault(rfq, {})[item] = group
	return by_rfq


# ---------------------------------------------------------------------------
# view 1: item-wise
# ---------------------------------------------------------------------------


def item_rows(comparison) -> list:
	rows = []
	by_rfq = groups_by_request(comparison)

	for rfq in sorted(set(by_rfq) | set(comparison.requested)):
		items = by_rfq.get(rfq, {})
		asked = comparison.requested.get(rfq, {})
		for item in sorted(set(items) | set(asked)):
			group = items.get(item)
			if not group:
				want = asked[item]
				rows.append(
					{
						"request_for_quotation": rfq,
						"item_code": item,
						"item_name": want.item_name,
						"requested_qty": want.qty,
						"stock_uom": want.stock_uom,
						"company_currency": comparison.company_currency,
						"remarks": _("No quotation received"),
					}
				)
				continue
			for quote in sorted(group, key=lambda q: (q.rank is None, q.rank or 0, sort_key(q))):
				if visible(comparison, quote):
					rows.append(item_row(quote))
	return rows


def item_row(quote) -> dict:
	return {
		"request_for_quotation": quote.rfq,
		"item_code": quote.item_code,
		"item_name": quote.item_name,
		"requested_qty": quote.requested_qty,
		"stock_uom": quote.stock_uom,
		"supplier": quote.supplier,
		"supplier_name": quote.supplier_name,
		"quotation": quote.quotation,
		"origin": quote.origin,
		"currency": quote.currency,
		"company_currency": quote.company_currency,
		"qty": quote.qty,
		"uom": quote.uom,
		"qty_offered_pct": quote.qty_offered_pct,
		"unit_cost_txn": quote.unit_cost_txn,
		"unit_cost_base": quote.unit_cost_base,
		"import_charge_unit": quote.import_charge_unit,
		"landed_unit_cost": quote.landed_unit_cost,
		"landed_total": quote.landed_total,
		"rank": quote.rank,
		"recommended": quote.recommended,
		"pct_over_best": quote.pct_over_best,
		"saving_vs_next": quote.saving_vs_next,
		"last_purchase_rate": quote.last_purchase_rate,
		"vs_last_purchase_pct": quote.vs_last_purchase_pct,
		"lead_time_days": quote.lead_time_days,
		"valid_till": quote.valid_till,
		"rule_label": quote.rule_label,
		"remarks": quote.remarks,
	}


# ---------------------------------------------------------------------------
# view 2: supplier-wise
# ---------------------------------------------------------------------------


def supplier_rows(comparison) -> list:
	"""One line per supplier per request: coverage, wins, basket, and the cheapest single award."""
	rows = []
	by_rfq = groups_by_request(comparison)

	for rfq in sorted(set(by_rfq) | set(comparison.requested) | set(comparison.invited)):
		groups = by_rfq.get(rfq, {})
		asked = comparison.requested.get(rfq) or {item: None for item in groups}
		rows.extend(supplier_rows_for(comparison, rfq, groups, asked))
	return rows


def supplier_rows_for(comparison, rfq, groups, asked) -> list:
	"""The suppliers of one request: those it invited and those that quoted, whether invited or not."""
	items = set(asked)
	best_split = sum(quote.landed_total for group in groups.values() for quote in group if quote.recommended)

	invites = {row.supplier: row for row in comparison.invited.get(rfq, [])}
	quoters = {}
	for group in groups.values():
		for quote in group:
			quoters.setdefault(quote.supplier, []).append(quote)

	summaries = []
	for supplier in sorted(set(invites) | set(quoters)):
		quotes = quoters.get(supplier, [])
		shown = [quote for quote in quotes if visible(comparison, quote)]
		in_running = {quote.item_code: quote for quote in quotes if quote.rank is not None}
		won = [quote for quote in in_running.values() if quote.recommended]
		invite = invites.get(supplier)

		summaries.append(
			frappe._dict(
				request_for_quotation=rfq,
				supplier=supplier,
				supplier_name=(invite.supplier_name if invite else None) or next((q.supplier_name for q in quotes), None),
				origin=IMPORT if any(q.origin == IMPORT for q in quotes) else (LOCAL if quotes else None),
				currency=", ".join(sorted({q.currency for q in quotes})) or None,
				company_currency=comparison.company_currency,
				items_requested=len(items),
				items_quoted=len(in_running),
				coverage_pct=(len(in_running) / len(items) * 100) if items else None,
				items_won=len(won),
				basket_total=sum(q.landed_total for q in in_running.values()) if in_running else None,
				won_total=sum(q.landed_total for q in won) if won else None,
				full=bool(items) and items <= set(in_running),
				max_lead_time=max((q.lead_time_days or 0 for q in in_running.values()), default=0) or None,
				earliest_expiry=min((q.valid_till for q in in_running.values() if q.valid_till), default=None),
				quotations=", ".join(sorted({q.quotation for q in shown})) or None,
				response=response_label(invite, quotes, shown),
				basket_rank=None,
				pct_over_best_basket=None,
				premium_vs_split=None,
				recommendation="",
			)
		)

	full_baskets = sorted(
		(row for row in summaries if row.full),
		key=lambda row: (flt(row.basket_total, COST_PRECISION), row.max_lead_time or FAR, row.supplier),
	)
	for position, row in enumerate(full_baskets, 1):
		best = flt(full_baskets[0].basket_total)
		row.basket_rank = position
		row.pct_over_best_basket = (flt(row.basket_total) / best - 1) * 100 if best else 0.0
		row.premium_vs_split = flt(row.basket_total) - best_split

	for row in summaries:
		row.recommendation = supplier_recommendation(row)

	summaries.sort(key=lambda row: (-row.items_won, row.basket_rank or FAR, -row.items_quoted, row.supplier))
	return [dict(row) for row in summaries]


def response_label(invite, quotes, shown) -> str:
	if quotes:
		if not shown:
			return _("Expired")
		return _("Quoted") if invite else _("Quoted (not invited)")
	if invite and invite.quote_status == "Received":
		return _("Answered (outside these filters)")
	return _("No response") if invite else ""


def supplier_recommendation(row) -> str:
	if not row.items_quoted:
		return ""
	if row.items_requested and row.items_won == row.items_requested:
		return _("Recommended: lowest on every item")
	parts = []
	if row.items_won:
		parts.append(_("Recommended for {0} of {1} items").format(row.items_won, row.items_requested))
	if row.basket_rank == 1:
		parts.append(_("Cheapest single award"))
	return "; ".join(parts) or _("Not recommended")


# ---------------------------------------------------------------------------
# view 3: gaps
# ---------------------------------------------------------------------------


def gap_rows(comparison) -> list:
	"""What the requests are missing: who did not answer, what a supplier left out, what nobody quoted, what is short."""
	rows = []
	by_rfq = groups_by_request(comparison)

	for rfq in sorted(set(comparison.requested) | set(comparison.invited)):
		asked = comparison.requested.get(rfq, {})
		groups = by_rfq.get(rfq, {})
		quoted = {}
		for item, group in groups.items():
			for quote in group:
				if quote.priced:
					quoted.setdefault(quote.supplier, set()).add(item)

		invites = {row.supplier: row for row in comparison.invited.get(rfq, [])}
		for supplier in sorted(set(invites) | set(quoted)):
			invite = invites.get(supplier)
			name = invite.supplier_name if invite else None
			if supplier not in quoted:
				rows.append(
					gap_row(
						rfq,
						supplier,
						name,
						gap=_("No response"),
						detail=_("RFQ status: {0}").format(invite.quote_status or _("Pending")),
					)
				)
				continue
			for item in sorted(set(asked) - quoted[supplier]):
				rows.append(
					gap_row(
						rfq,
						supplier,
						name,
						item=item,
						want=asked[item],
						gap=_("Item not quoted"),
						detail=_("Quoted {0} of {1} items").format(len(quoted[supplier] & set(asked)), len(asked)),
					)
				)

		for item in sorted(asked):
			group = groups.get(item)
			if not group:
				rows.append(
					gap_row(rfq, None, None, item=item, want=asked[item], gap=_("No quotation received"), detail="")
				)
				continue
			first = group[0]
			if first.quotes_needed and first.quotes_received < first.quotes_needed:
				rows.append(
					gap_row(
						rfq,
						None,
						None,
						item=item,
						want=asked[item],
						origin=next((q.origin for q in group if q.recommended), first.origin),
						gap=_("Below minimum quotations"),
						detail=_("{0} of {1} different suppliers have quoted").format(
							first.quotes_received, first.quotes_needed
						),
					)
				)
	return rows


def gap_row(rfq, supplier, supplier_name, gap, detail, item=None, want=None, origin=None) -> dict:
	return {
		"request_for_quotation": rfq,
		"supplier": supplier,
		"supplier_name": supplier_name,
		"item_code": item,
		"item_name": want.item_name if want else None,
		"requested_qty": want.qty if want else None,
		"stock_uom": want.stock_uom if want else None,
		"origin": origin,
		"gap": gap,
		"detail": detail,
	}


# ---------------------------------------------------------------------------
# columns and message
# ---------------------------------------------------------------------------


def link(label, fieldname, options, width=140) -> dict:
	return {"label": label, "fieldname": fieldname, "fieldtype": "Link", "options": options, "width": width}


def data(label, fieldname, width=110) -> dict:
	return {"label": label, "fieldname": fieldname, "fieldtype": "Data", "width": width}


def number(label, fieldname, width=90, fieldtype="Float") -> dict:
	return {"label": label, "fieldname": fieldname, "fieldtype": fieldtype, "width": width}


def money(label, fieldname, options="company_currency", width=120, precision=None) -> dict:
	column = {"label": label, "fieldname": fieldname, "fieldtype": "Currency", "options": options, "width": width}
	if precision is not None:
		column["precision"] = precision
	return column


def get_item_columns() -> list:
	return [
		link(_("Request for Quotation"), "request_for_quotation", "Request for Quotation", 160),
		link(_("Item"), "item_code", "Item", 140),
		data(_("Item Name"), "item_name", 170),
		number(_("Requested Qty"), "requested_qty", 100),
		# Data, not a Link to UOM: a query report refuses a Link column whose doctype the reader cannot read
		data(_("Stock UOM"), "stock_uom", 80),
		link(_("Supplier"), "supplier", "Supplier", 150),
		data(_("Supplier Name"), "supplier_name", 170),
		link(_("Supplier Quotation"), "quotation", "Supplier Quotation", 170),
		data(_("Origin"), "origin", 70),
		link(_("Currency"), "currency", "Currency", 80),
		number(_("Quoted Qty"), "qty", 90),
		data(_("UOM"), "uom", 70),
		number(_("Qty Offered %"), "qty_offered_pct", 100, "Percent"),
		money(_("Net Price / Stock Unit (Quote Currency)"), "unit_cost_txn", "currency", 150, precision=4),
		money(_("Net Price / Stock Unit"), "unit_cost_base", precision=4),
		money(_("Import Charges / Unit (Est.)"), "import_charge_unit", precision=4),
		money(_("Landed Cost / Unit"), "landed_unit_cost", precision=4),
		money(_("Landed Total (Requested Qty)"), "landed_total", width=150),
		number(_("Rank"), "rank", 55, "Int"),
		number(_("Recommended"), "recommended", 100, "Check"),
		number(_("% Over Lowest"), "pct_over_best", 100, "Percent"),
		money(_("Saving vs Next Best"), "saving_vs_next"),
		money(_("Last Purchase Rate"), "last_purchase_rate", precision=4),
		number(_("% vs Last Purchase"), "vs_last_purchase_pct", 110, "Percent"),
		number(_("Lead Time (Days)"), "lead_time_days", 100, "Int"),
		{"label": _("Valid Till"), "fieldname": "valid_till", "fieldtype": "Date", "width": 95},
		data(_("Procurement Rule"), "rule_label", 130),
		data(_("Remarks"), "remarks", 260),
	]


def get_supplier_columns() -> list:
	return [
		link(_("Request for Quotation"), "request_for_quotation", "Request for Quotation", 160),
		link(_("Supplier"), "supplier", "Supplier", 150),
		data(_("Supplier Name"), "supplier_name", 180),
		data(_("Response"), "response", 150),
		data(_("Origin"), "origin", 70),
		data(_("Currency"), "currency", 90),
		number(_("Items Requested"), "items_requested", 100, "Int"),
		number(_("Items Quoted"), "items_quoted", 90, "Int"),
		number(_("Coverage %"), "coverage_pct", 90, "Percent"),
		number(_("Items Won"), "items_won", 80, "Int"),
		money(_("Basket Total (Items Quoted)"), "basket_total", width=150),
		money(_("Total of Items Won"), "won_total", width=130),
		number(_("Basket Rank (Full Coverage)"), "basket_rank", 130, "Int"),
		number(_("% Over Best Basket"), "pct_over_best_basket", 120, "Percent"),
		money(_("Premium vs Best Split Award"), "premium_vs_split", width=150),
		number(_("Longest Lead Time (Days)"), "max_lead_time", 130, "Int"),
		{"label": _("Earliest Expiry"), "fieldname": "earliest_expiry", "fieldtype": "Date", "width": 100},
		data(_("Quotations"), "quotations", 220),
		data(_("Recommendation"), "recommendation", 260),
	]


def get_gap_columns() -> list:
	return [
		link(_("Request for Quotation"), "request_for_quotation", "Request for Quotation", 160),
		data(_("Gap"), "gap", 170),
		link(_("Supplier"), "supplier", "Supplier", 150),
		data(_("Supplier Name"), "supplier_name", 180),
		link(_("Item"), "item_code", "Item", 140),
		data(_("Item Name"), "item_name", 170),
		number(_("Requested Qty"), "requested_qty", 100),
		data(_("Stock UOM"), "stock_uom", 80),
		data(_("Origin"), "origin", 70),
		data(_("Detail"), "detail", 280),
	]


def get_message(comparison) -> str:
	filters = comparison.filters
	notes = [
		_(
			"Compared on the net price per stock unit, in {0}, before tax. Import quotations carry {1}% estimated import "
			"charges; local ones carry none."
		).format(comparison.company_currency, flt(comparison.charges, 2))
	]
	if resolve_view(filters) == VIEW_SUPPLIER:
		notes.append(
			_(
				"A supplier wins an item by having the lowest landed cost on it. Basket Rank compares only suppliers that "
				"quoted every item of the request."
			)
		)
	else:
		notes.append(
			_("Green: recommended. Valid Till: orange expires in a week or less, red expires today or has expired.")
		)
	origin = resolve_origin(filters)
	if origin != ORIGIN_BOTH:
		notes.append(_("{0}: a supplier or item with nothing of that origin reads as not quoted.").format(_(origin)))
	return "".join("<div>" + escape_html(note) + "</div>" for note in notes)


# ---------------------------------------------------------------------------
# raising Purchase Orders from the recommendation
# ---------------------------------------------------------------------------


def parse_filters(filters) -> frappe._dict:
	parsed = frappe._dict(frappe.parse_json(filters) if isinstance(filters, str) else (filters or {}))
	validate_filters(parsed)
	return parsed


def recommended_orders(filters) -> list:
	"""The recommended quotation lines grouped by quotation: what a Purchase Order would take from each.

	A line already on a Purchase Order that is not cancelled is left out, so asking twice never orders twice."""
	comparison = compare(filters)
	winners = [quote for quote in comparison.quotes if quote.recommended]
	ordered = set(
		fetch_in(
			"Purchase Order Item",
			[quote.quotation_item for quote in winners],
			in_field="supplier_quotation_item",
			filters={"docstatus": ["<", 2]},
			pluck="supplier_quotation_item",
		)
	)

	orders = {}
	for quote in winners:
		if quote.quotation_item in ordered:
			continue
		order = orders.setdefault(
			quote.quotation,
			frappe._dict(
				quotation=quote.quotation,
				supplier=quote.supplier,
				supplier_name=quote.supplier_name,
				request_for_quotation=quote.rfq,
				origin=quote.origin,
				currency=comparison.company_currency,
				submitted=quote.docstatus == 1,
				items=[],
				total=0.0,
			),
		)
		order["items"].append(quote.quotation_item)
		order["total"] += quote.landed_total
	return sorted(orders.values(), key=lambda order: (order.request_for_quotation, order.quotation))


@frappe.whitelist()
def get_recommended_orders(filters: str) -> list:
	"""What the Create Purchase Orders button offers: one entry per recommended quotation."""
	frappe.has_permission("Purchase Order", "create", throw=True)
	return [dict(order, item_count=len(order["items"])) for order in recommended_orders(parse_filters(filters))]


@frappe.whitelist()
def make_purchase_orders(filters: str, quotations: str) -> list:
	"""Raise a draft Purchase Order for each chosen recommended quotation. Returns [{quotation, purchase_order, error}].

	The recommendation is worked out again here from the filters; only quotations that are in it, and only their
	recommended lines, are ordered. One failing order does not stop the others."""
	from erpnext.buying.doctype.supplier_quotation.supplier_quotation import make_purchase_order

	frappe.has_permission("Purchase Order", "create", throw=True)
	chosen = set(as_list(quotations))
	if not chosen:
		frappe.throw(_("Choose at least one quotation"))

	results = []
	offered = {order.quotation: order for order in recommended_orders(parse_filters(filters))}
	for number_, name in enumerate(sorted(chosen)):
		order = offered.get(name)
		if not order:
			results.append({"quotation": name, "purchase_order": None, "error": _("Not in the recommendation")})
			continue
		if not order.submitted:
			results.append({"quotation": name, "purchase_order": None, "error": _("Submit the quotation first")})
			continue

		savepoint = "sqce_" + cstr(number_)
		frappe.db.savepoint(savepoint)
		try:
			po = make_purchase_order(name, args={"filtered_children": order["items"]})
			moved = bring_required_by_forward(po)
			po.insert()
			note = (
				_("Required By moved to {0} on {1} row(s): the Material Request date had passed").format(
					po.transaction_date, moved
				)
				if moved
				else None
			)
			results.append({"quotation": name, "purchase_order": po.name, "error": None, "note": note})
		except Exception as error:
			frappe.db.rollback(save_point=savepoint)
			results.append({"quotation": name, "purchase_order": None, "error": cstr(error), "note": None})
	return results


def bring_required_by_forward(po) -> int:
	"""Core copies each line's Required By from its Material Request, and refuses an order dated after it. A quotation
	round that outlasts the request's date would then refuse every draft this button makes, so a date already past is
	moved to the order date (the draft is the buyer's to adjust) and the caller says so. Returns the lines moved."""
	order_date = getdate(po.transaction_date or nowdate())
	moved = 0
	for row in po.get("items") or []:
		if row.get("schedule_date") and getdate(row.schedule_date) < order_date:
			row.schedule_date = order_date
			moved += 1
	if po.get("schedule_date") and getdate(po.schedule_date) < order_date:
		po.schedule_date = order_date
	return moved
