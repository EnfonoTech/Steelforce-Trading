# Copyright (c) 2026, Enfono Technologies and contributors
# For license information, please see license.txt

"""How a Material Transfer's cost was reached in its source warehouse.

One row per transferred line: source and target warehouse, valuation method, qty, the transfer rate
(the source's outgoing rate, which is also the rate it arrives at in the target), value, the source
balance just before it (qty, value, Moving Average rate) and a Check that says whether the replay
agrees with the ledger. Under each line, the documents that balance was made of -- receipts,
purchases, a Stock Reconciliation, earlier transfers -- pro rata (sf_trading/cogs_breakdown.py). An
earlier transfer carries its own link back to this report, so a chain is followed one hop at a time --
or, with Expand Transfer Chain ticked, a few hops back in the same table. A Purchase Receipt that
carried landed cost or taxes in valuation is split into price, taxes and landed cost.

Received Value is what the receiving leg of the same row is worth in the target warehouse, and Value
Gap is what left minus what arrived. They are the same posting, so any gap beyond a fil's rounding is
stock that arrived worth less (or more) than it left -- e.g. a repost that left the receiving entry at
zero value. Filters: Branch (transfers into or out of the warehouses its Branch Configuration lists --
a warehouse can sit under several branches, so this says "touches", not "belongs to"), Only Gaps, and
Only Lines To Review (the Check is not a match, or the value gap is not nil). Read only: it
recomputes on every open.

Permissions: `frappe.db.sql` applies none, so the User Permissions on Company, Warehouse and Cost
Center are applied here, the way the desk list applies them.
"""

from collections import defaultdict

import frappe
from frappe import _
from frappe.utils import add_days, cint, flt, getdate, nowdate

from sf_trading import cogs_breakdown as cb

#: past this many transfer lines the source documents are left out -- pick a Stock Entry to see them
MAX_EXPANDED_LINES = 300
#: Only Lines To Review checks every line against the ledger, so it may look at more
MAX_REVIEW_LEGS = 3000
#: past this many transfer lines only the newest are read
MAX_LEGS = 2000
CHECK_LABELS = {
	cb.OK: "Matches ledger",
	cb.NEGATIVE: "Negative stock",
	cb.DIFFERS: "Replay differs",
	cb.GAP: "Received value differs",
}


def execute(filters=None):
	filters = frappe._dict(filters or {})
	if not filters.company:
		frappe.throw(_("Company is required"))
	frappe.has_permission("Stock Entry", "read", throw=True)

	scope = cb.permission_scope()
	if not cb.permitted(scope, "Company", filters.company, "Stock Entry"):
		frappe.throw(
			_("You do not have permission to access Company {0}.").format(filters.company),
			frappe.PermissionError,
		)
	if filters.stock_entry:
		frappe.has_permission("Stock Entry", "read", doc=filters.stock_entry, throw=True)

	messages = []
	warehouses = None
	if filters.branch:
		warehouses = cb.branch_warehouses(filters.branch)
		if not warehouses:
			messages.append(
				_(
					"Branch {0} lists no warehouses in its Branch Configuration, so no transfer can be matched to it."
				).format(filters.branch)
			)
			return get_columns(), [], "<br>".join(messages), None, get_summary([], {}, None)

	legs = get_outgoing_legs(filters, warehouses)
	allowed = permitted_entries(legs, scope)
	legs = [e for e in legs if e.voucher_no in allowed]
	incoming = cb.incoming_legs(e.voucher_detail_no for e in legs)
	if cint(filters.only_gaps):
		legs = [e for e in legs if value_gap(e, incoming)[1]]
	if len(legs) > MAX_LEGS:
		messages.append(
			_(
				"Showing the latest {0} of {1} transfer lines. Narrow the dates, or pick an item, warehouse or Stock Entry."
			).format(MAX_LEGS, len(legs))
		)
		legs = legs[:MAX_LEGS]

	review = cint(filters.only_review)
	checkable = len(legs) <= (MAX_REVIEW_LEGS if review else MAX_EXPANDED_LINES)
	if review and not checkable:
		messages.append(
			_(
				"{0} transfer lines are more than Only Lines To Review can check at once. Narrow the dates or pick "
				"a branch, item or Stock Entry."
			).format(len(legs))
		)
		return get_columns(), [], "<br>".join(messages), None, get_summary([], {}, None)

	explained = {}
	if checkable:
		snapshots = cb.sources_before_many(legs)
		explained = {e.name: cb.explain(e, snapshots[e.name]) for e in legs}
	flags = {e.name: line_flag(explained.get(e.name), value_gap(e, incoming)[1]) for e in legs}
	shown = [e for e in legs if not review or flags[e.name] in cb.REVIEW_FLAGS]
	if len(shown) < len(legs):
		messages.append(
			_("Only Lines To Review hides {0} transfer line(s) that match the ledger.").format(
				len(legs) - len(shown)
			)
		)

	expand = checkable and len(shown) <= MAX_EXPANDED_LINES
	if not expand:
		messages.append(
			_(
				"{0} transfer lines: source documents are shown for one Stock Entry at a time. Pick a Stock Entry to see them."
			).format(len(shown))
		)

	currency = frappe.get_cached_value("Company", filters.company, "default_currency")
	rows, counts, chain_spent = build_rows(shown, filters, expand, currency, explained, incoming, flags)
	messages += check_messages(counts, chain_spent)
	return get_columns(), rows, "<br>".join(messages) or None, None, get_summary(shown, incoming, currency)


def value_gap(leg, incoming: dict):
	"""(sent - received, whether that is more than rounding), or (None, False) when no receiving leg is on
	file. Both legs are one posting, so the tolerance is the currency's rounding and no more."""
	got = incoming.get(leg.voucher_detail_no)
	if not got:
		return None, False
	gap = -flt(leg.stock_value_difference) - got.value
	return gap, abs(gap) > cb.tolerance() or abs(-flt(leg.actual_qty) - got.qty) > cb.QTY_TOLERANCE


def line_flag(explanation, has_gap: bool):
	"""The replay's flag, or the value gap when the replay has nothing to say against the line."""
	flag = explanation.flag if explanation else None
	if has_gap and flag in (None, cb.OK):
		return cb.GAP
	return flag


def get_outgoing_legs(filters, branch_warehouses=None) -> list:
	"""The source-warehouse leg of each Material Transfer line, with the line's target warehouse."""
	conditions = [
		"sle.company = %(company)s",
		"sle.is_cancelled = 0",
		"sle.voucher_type = 'Stock Entry'",
		"sle.actual_qty < 0",
		"se.purpose = 'Material Transfer'",
		"se.docstatus = 1",
	]
	values = {"company": filters.company}
	if filters.stock_entry:
		conditions.append("sle.voucher_no = %(stock_entry)s")
		values["stock_entry"] = filters.stock_entry
	else:
		conditions.append("sle.posting_date between %(from_date)s and %(to_date)s")
		values["from_date"] = getdate(filters.from_date or add_days(nowdate(), -30))
		values["to_date"] = getdate(filters.to_date or nowdate())
	for field, column in (
		("item_code", "sle.item_code"),
		("source_warehouse", "sle.warehouse"),
		("target_warehouse", "sed.t_warehouse"),
	):
		if filters.get(field):
			conditions.append(f"{column} = %({field})s")
			values[field] = filters.get(field)
	if branch_warehouses:
		conditions.append("(sle.warehouse in %(branch_wh)s or sed.t_warehouse in %(branch_wh)s)")
		values["branch_wh"] = tuple(sorted(branch_warehouses))
	if filters.item_group:
		conditions.append(
			"exists (select 1 from `tabItem` i where i.name = sle.item_code and i.item_group = %(item_group)s)"
		)
		values["item_group"] = filters.item_group

	return frappe.db.sql(
		f"""select {cb.sle_fields("sle")}, sed.t_warehouse as target_warehouse
		from `tabStock Ledger Entry` sle
		join `tabStock Entry` se on se.name = sle.voucher_no
		left join `tabStock Entry Detail` sed on sed.name = sle.voucher_detail_no
		where {" and ".join(conditions)}
		order by posted desc, sle.creation desc""",
		values,
		as_dict=True,
	)


def permitted_entries(legs, scope) -> set:
	"""The Stock Entries of `legs` the user may see under their Warehouse and Cost Center permissions.

	Frappe holds a document back when ANY of its rows falls outside a User Permission, so a transfer
	with one forbidden line is not shown at all. Nothing is read for a user restricted on neither."""
	names = {e.voucher_no for e in legs}
	if not scope.get("Warehouse") and not scope.get("Cost Center"):
		return names

	forbidden = set()
	for chunk in cb.chunks(sorted(names)):
		for r in entry_rows(chunk):
			if not (
				cb.permitted(scope, "Warehouse", r.s_warehouse, "Stock Entry")
				and cb.permitted(scope, "Warehouse", r.t_warehouse, "Stock Entry")
				and cb.permitted(scope, "Cost Center", r.cost_center, "Stock Entry")
			):
				forbidden.add(r.parent)
	return names - forbidden


def entry_rows(parents) -> list:
	"""The item rows of the given Stock Entries -- the warehouses and cost centre each moves stock through."""
	detail = frappe.qb.DocType("Stock Entry Detail")
	return (
		frappe.qb.from_(detail)
		.select(detail.parent, detail.s_warehouse, detail.t_warehouse, detail.cost_center)
		.where(detail.parent.isin(list(parents)))
		.run(as_dict=True)
	)


def build_rows(legs, filters, expand, currency, explained, incoming, flags):
	"""The report rows, the count of lines by Check flag, and whether the chain budget ran out."""
	budget = cb.new_budget()
	if expand and cint(filters.expand_chain):
		for e in legs:
			cb.attach_chain(explained[e.name].rows, e.item_code, budget)
	pairs = {
		(s.voucher_type, s.voucher_no)
		for e in legs
		if expand
		for s, _level in cb.walk(explained[e.name].rows)
		if s.get("voucher_type") in cb.RECEIPT_ITEM_DOCTYPE
	}
	totals = cb.receipt_totals(pairs) if pairs else {}
	items = cb.item_info(e.item_code for e in legs)
	default_method = cb.default_valuation_method()

	counts = defaultdict(int)
	rows = []
	for e in legs:
		qty = -flt(e.actual_qty)
		value = -flt(e.stock_value_difference)
		before = cb.balance_before(e)
		gap, has_gap = value_gap(e, incoming)
		got = incoming.get(e.voucher_detail_no)
		flag = flags[e.name]
		item = items.get(e.item_code)
		rows.append(
			{
				"indent": 0,
				"doc_type": "Stock Entry",
				"document": e.voucher_no,
				"posting_date": e.posting_date,
				"item_code": e.item_code,
				"item_name": item.item_name if item else e.item_code,
				"source_warehouse": e.warehouse,
				"target_warehouse": e.target_warehouse,
				"valuation_method": (item.valuation_method if item else None) or default_method,
				"qty": qty,
				"rate": (value / qty) if qty else 0,
				"value": value,
				"received_value": got.value if got else None,
				"value_gap": gap,
				"differs": 1 if has_gap else 0,
				"balance_qty": before.qty,
				"balance_value": before.value,
				"balance_rate": before.rate,
				"check": _(CHECK_LABELS[flag]) if flag else None,
				"check_flag": flag,
				"currency": currency,
			}
		)
		if flag:
			counts[flag] += 1
		if has_gap and flag != cb.GAP:
			counts[cb.GAP] += 1
		ex = explained.get(e.name)
		if ex and expand:
			emit_sources(ex.rows, 1, e, totals, filters.company, currency, rows)
	return rows, counts, budget.spent


def emit_sources(tree, indent, leg, totals, company, currency, rows) -> None:
	"""Flatten a line's source tree into rows, a receipt's price / tax / landed-cost split beneath it."""
	for s in tree:
		if s.get("component"):
			rows.append(
				{
					"indent": indent,
					"source_type": s.component,
					"rate": s.rate,
					"value": s.value,
					"component": 1,
					"currency": currency,
				}
			)
			continue
		transfer = bool(s.voucher_type) and not s.followed and cb.is_transfer(s.voucher_type, s.voucher_no)
		row = {
			"indent": indent,
			"doc_type": s.voucher_type,
			"document": s.voucher_no,
			"posting_date": s.posting_date,
			"source_warehouse": s.get("warehouse") or leg.warehouse,
			"source_type": cb.source_type(s),
			"value": s.value,
			"currency": currency,
			"breakdown": 1 if transfer else 0,
			"company": company,
			# the item the link opens the transfer on: a transfer may move several items
			"breakdown_item": leg.item_code if transfer else None,
			"gap": 1 if s.get("gap") else 0,
		}
		if s.qty:
			# cost no document explains and no stock stands behind carries a value only
			row.update(qty=s.qty, rate=s.rate)
		rows.append(row)
		kids = list(s.get("children") or [])
		if s.get("voucher_type") in cb.RECEIPT_ITEM_DOCTYPE:
			kids = cb.receipt_components(s, totals, leg.item_code) + kids
		emit_sources(kids, indent + 1, leg, totals, company, currency, rows)


def check_messages(counts, chain_spent) -> list:
	messages = []
	if counts.get(cb.NEGATIVE):
		messages.append(
			_(
				"{0} line(s) left more than the bin held (negative stock in the ledger): the part that came from "
				"no stock is its own row, at the item's last valuation rate."
			).format(counts[cb.NEGATIVE])
		)
	if counts.get(cb.DIFFERS):
		messages.append(
			_(
				"{0} line(s) do not agree with the ledger replay by more than rounding: see the Check column; an "
				"'Not explained' row carries the difference."
			).format(counts[cb.DIFFERS])
		)
	if counts.get(cb.GAP):
		messages.append(
			_(
				"{0} transfer line(s) arrived worth a different amount than they left: see Value Gap. The receiving "
				"entry in the target warehouse is the one to look at."
			).format(counts[cb.GAP])
		)
	if chain_spent:
		messages.append(
			_(
				"Transfer chains were followed for the first {0} transfers only; the rest keep their Transfer Cost "
				"Breakdown link."
			).format(cb.MAX_CHAIN_LOOKUPS)
		)
	return messages


def get_summary(legs, incoming, currency):
	value = sum(-flt(e.stock_value_difference) for e in legs)
	apart = [e for e in legs if value_gap(e, incoming)[1]]
	return [
		{"label": _("Transfers"), "value": len({e.voucher_no for e in legs}), "datatype": "Int"},
		{"label": _("Lines"), "value": len(legs), "datatype": "Int"},
		{"label": _("Value Transferred"), "value": value, "datatype": "Currency", "currency": currency},
		{
			"label": _("Lines With A Value Gap"),
			"value": len(apart),
			"datatype": "Int",
			"indicator": "Red" if apart else "Green",
		},
		{
			"label": _("Value Gap"),
			"value": sum(value_gap(e, incoming)[0] or 0 for e in apart),
			"datatype": "Currency",
			"currency": currency,
			"indicator": "Red" if apart else "Green",
		},
	]


def get_columns():
	return [
		{
			"label": _("Type"),
			"fieldname": "doc_type",
			"fieldtype": "Link",
			"options": "DocType",
			"width": 170,
		},
		{
			"label": _("Document"),
			"fieldname": "document",
			"fieldtype": "Dynamic Link",
			"options": "doc_type",
			"width": 180,
		},
		{"label": _("Date"), "fieldname": "posting_date", "fieldtype": "Date", "width": 95},
		{"label": _("Item"), "fieldname": "item_code", "fieldtype": "Link", "options": "Item", "width": 120},
		{"label": _("Item Name"), "fieldname": "item_name", "fieldtype": "Data", "width": 160},
		{
			"label": _("Source Warehouse"),
			"fieldname": "source_warehouse",
			"fieldtype": "Link",
			"options": "Warehouse",
			"width": 120,
		},
		{
			"label": _("Target Warehouse"),
			"fieldname": "target_warehouse",
			"fieldtype": "Link",
			"options": "Warehouse",
			"width": 120,
		},
		{"label": _("Valuation Method"), "fieldname": "valuation_method", "fieldtype": "Data", "width": 120},
		{"label": _("Source"), "fieldname": "source_type", "fieldtype": "Data", "width": 190},
		{"label": _("Qty"), "fieldname": "qty", "fieldtype": "Float", "precision": 3, "width": 80},
		{
			"label": _("Rate"),
			"fieldname": "rate",
			"fieldtype": "Currency",
			"options": "currency",
			"precision": 4,
			"width": 110,
		},
		{
			"label": _("Value"),
			"fieldname": "value",
			"fieldtype": "Currency",
			"options": "currency",
			"width": 120,
		},
		{
			"label": _("Received Value"),
			"fieldname": "received_value",
			"fieldtype": "Currency",
			"options": "currency",
			"width": 120,
		},
		{
			"label": _("Value Gap"),
			"fieldname": "value_gap",
			"fieldtype": "Currency",
			"options": "currency",
			"width": 100,
		},
		{
			"label": _("Balance Qty Before"),
			"fieldname": "balance_qty",
			"fieldtype": "Float",
			"precision": 3,
			"width": 110,
		},
		{
			"label": _("Balance Value Before"),
			"fieldname": "balance_value",
			"fieldtype": "Currency",
			"options": "currency",
			"width": 130,
		},
		{
			"label": _("Average Rate Before"),
			"fieldname": "balance_rate",
			"fieldtype": "Currency",
			"options": "currency",
			"precision": 4,
			"width": 120,
		},
		{"label": _("Check"), "fieldname": "check", "fieldtype": "Data", "width": 170},
		{"label": _("Cost Breakdown"), "fieldname": "breakdown", "fieldtype": "Data", "width": 140},
		{
			"label": _("Currency"),
			"fieldname": "currency",
			"fieldtype": "Link",
			"options": "Currency",
			"hidden": 1,
			"width": 80,
		},
	]
