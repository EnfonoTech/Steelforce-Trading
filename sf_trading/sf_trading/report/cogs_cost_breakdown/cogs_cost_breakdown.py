# Copyright (c) 2026, Enfono Technologies and contributors
# For license information, please see license.txt

"""How the Cost of Goods Sold on a Delivery Note or Sales Invoice was reached.

One block per voucher: the COGS the stock ledger gives, what the GL booked for it (Cost of Goods Sold,
or Stock Delivered But Not Billed for a Delivery Note the company holds until invoiced), the difference,
and what it was sold for with the margin; under it one row per item line (warehouse, valuation method,
qty, rate, COGS, margin and a Check that says whether the replay agrees with the ledger); under each
line the documents the warehouse stock was made of, pro rata (sf_trading/cogs_breakdown.py). When stock
reached the warehouse by a transfer the source row is that transfer, with a link to Transfer Cost
Breakdown -- or, with Expand Transfer Chain ticked, the documents *its* source warehouse held, a few
hops back. A Purchase Receipt that carried landed cost or taxes in valuation is split into price,
taxes and landed cost.

The voucher totals (COGS, GL Booked, Difference, Sales) always cover the whole voucher, whatever the
Item and Warehouse filters hide from the lines below. A Sales Invoice that moved no stock itself is
answered by the Delivery Notes it billed. A return line shows as negative COGS, without a source
breakdown. Read only: it recomputes on every open.

Permissions: `frappe.db.sql` applies none, so the User Permissions on Company, Customer, Branch,
Warehouse and Cost Center are applied here, the way the desk list applies them.
"""

from collections import defaultdict

import frappe
from frappe import _
from frappe.utils import add_days, cint, flt, getdate, nowdate

from sf_trading import cogs_breakdown as cb

VOUCHER_TYPES = ("Delivery Note", "Sales Invoice")
#: past this many item lines the source documents are left out -- pick a voucher to see them
MAX_EXPANDED_LINES = 300
#: past this many vouchers only the newest are read -- every one costs reads of its own
MAX_VOUCHERS = 1000
#: the Check column, by the engine's flag
CHECK_LABELS = {cb.OK: "Matches ledger", cb.NEGATIVE: "Negative stock", cb.DIFFERS: "Replay differs"}


def execute(filters=None):
	filters = frappe._dict(filters or {})
	if not filters.company:
		frappe.throw(_("Company is required"))
	if filters.voucher_no and not filters.voucher_type:
		frappe.throw(_("Pick the Voucher Type of {0}.").format(filters.voucher_no))
	for doctype in VOUCHER_TYPES:
		frappe.has_permission(doctype, "read", throw=True)

	scope = cb.permission_scope()
	for doctype in VOUCHER_TYPES:
		if not cb.permitted(scope, "Company", filters.company, doctype):
			frappe.throw(
				_("You do not have permission to access Company {0}.").format(filters.company),
				frappe.PermissionError,
			)

	messages = []
	vouchers = get_vouchers(filters, messages)
	headers = get_headers(vouchers)
	vouchers = [v for v in vouchers if headers.get(v) and visible_header(headers[v], v[0], filters, scope)]
	if len(vouchers) > MAX_VOUCHERS:
		messages.append(
			_(
				"Showing the latest {0} of {1} vouchers. Narrow the dates, or pick a customer, item or voucher."
			).format(MAX_VOUCHERS, len(vouchers))
		)
		vouchers = vouchers[:MAX_VOUCHERS]

	entries = cb.entries_for_vouchers(vouchers)
	item_rows = get_item_rows(vouchers)
	vouchers = [v for v in vouchers if visible_lines(entries.get(v, []), item_rows.get(v, []), v[0], scope)]

	shown = {v: [e for e in entries.get(v, []) if line_matches(e, filters)] for v in vouchers}
	vouchers = [v for v in vouchers if shown[v]]
	hidden = sum(len(entries[v]) - len(shown[v]) for v in vouchers)
	if hidden:
		messages.append(
			_(
				"The Item / Warehouse filter hides {0} line(s) of these vouchers. COGS, GL Booked, Sales and "
				"Difference still cover the whole voucher."
			).format(hidden)
		)

	line_count = sum(len(shown[v]) for v in vouchers)
	expand = line_count <= MAX_EXPANDED_LINES
	if not expand:
		messages.append(
			_(
				"{0} item lines: source documents are shown for one voucher at a time. Pick a Voucher No to see them."
			).format(line_count)
		)

	currency = frappe.get_cached_value("Company", filters.company, "default_currency")
	booked = cb.gl_cogs_many(vouchers)
	rows, counts, chain_spent = build_rows(
		vouchers, entries, shown, headers, item_rows, booked, filters, expand, currency
	)
	messages += check_messages(counts, chain_spent)
	return get_columns(), rows, "<br>".join(messages) or None, None, get_summary(rows, currency)


def get_vouchers(filters, messages) -> list:
	"""(voucher_type, voucher_no) pairs, newest first."""
	if filters.voucher_no:
		return typed_voucher(filters, messages)

	conditions = [
		"company = %(company)s",
		"is_cancelled = 0",
		"voucher_type in %(types)s",
		"posting_date between %(from_date)s and %(to_date)s",
	]
	values = {
		"company": filters.company,
		"types": [filters.voucher_type] if filters.voucher_type else list(VOUCHER_TYPES),
		"from_date": getdate(filters.from_date or add_days(nowdate(), -30)),
		"to_date": getdate(filters.to_date or nowdate()),
	}
	if filters.item_code:
		conditions.append("item_code = %(item)s")
		values["item"] = filters.item_code
	if filters.warehouse:
		conditions.append("warehouse = %(warehouse)s")
		values["warehouse"] = filters.warehouse
	rows = frappe.db.sql(
		f"""select voucher_type, voucher_no, max(posting_date) as latest from `tabStock Ledger Entry`
		where {" and ".join(conditions)}
		group by voucher_type, voucher_no order by latest desc, voucher_no desc""",
		values,
	)
	return [(r[0], r[1]) for r in rows]


def typed_voucher(filters, messages) -> list:
	"""The one voucher the user named -- checked against the user's own permission on that document."""
	voucher_type, voucher_no = filters.voucher_type, filters.voucher_no
	frappe.has_permission(voucher_type, "read", doc=voucher_no, throw=True)
	header = get_headers([(voucher_type, voucher_no)]).get((voucher_type, voucher_no))
	if not header:
		frappe.throw(_("{0} {1} was not found.").format(_(voucher_type), voucher_no))
	if header.company != filters.company:
		frappe.throw(
			_("{0} belongs to {1}, not {2}. Change the Company filter.").format(
				voucher_no, header.company, filters.company
			)
		)
	if header.docstatus != 1:
		state = _("a draft") if header.docstatus == 0 else _("cancelled")
		messages.append(
			_("{0} is {1}: only a submitted voucher has a cost to break down.").format(voucher_no, state)
		)
		return []
	if cb.voucher_entries(voucher_type, voucher_no):
		return [(voucher_type, voucher_no)]
	if voucher_type == "Sales Invoice":
		return billed_notes(voucher_no, messages)
	messages.append(_("{0} moved no stock.").format(voucher_no))
	return []


def billed_notes(invoice: str, messages) -> list:
	"""An invoice that moved no stock itself: the Delivery Notes it billed, or why there are none."""
	item = frappe.qb.DocType("Sales Invoice Item")
	notes = (
		frappe.qb.from_(item)
		.select(item.delivery_note)
		.distinct()
		.where((item.parent == invoice) & (item.delivery_note.isnotnull()) & (item.delivery_note != ""))
		.run(pluck=True)
	)
	submitted = [n for n in notes if frappe.db.get_value("Delivery Note", n, "docstatus") == 1]
	if submitted:
		messages.append(
			_("{0} moved no stock itself; its cost is on the Delivery Notes it billed: {1}.").format(
				invoice, ", ".join(submitted)
			)
		)
		return [("Delivery Note", n) for n in submitted]

	# invoiced ahead of delivery (sbnd.py): the cost is booked at the rate frozen on the row,
	# and the stock -- with the breakdown -- moves on the Delivery Note that follows
	messages.append(
		_(
			"{0} moved no stock and billed no Delivery Note. GL Booked: {1}. Its stock leaves on "
			"the Delivery Note made from it; open the breakdown from there."
		).format(invoice, frappe.format(cb.gl_cogs("Sales Invoice", invoice), "Currency"))
	)
	return []


def get_headers(vouchers) -> dict:
	"""{(voucher_type, voucher_no): company, customer, branch, docstatus}, a query per type and chunk."""
	by_type = defaultdict(list)
	for voucher_type, voucher_no in vouchers:
		by_type[voucher_type].append(voucher_no)

	out = {}
	for voucher_type, names in by_type.items():
		doc = frappe.qb.DocType(voucher_type)
		columns = [doc.name, doc.company, doc.customer, doc.docstatus]
		if frappe.get_meta(voucher_type).has_field("branch"):
			columns.append(doc.branch)
		for chunk in cb.chunks(names):
			for h in frappe.qb.from_(doc).select(*columns).where(doc.name.isin(chunk)).run(as_dict=True):
				out[(voucher_type, h.name)] = h
	return out


def get_item_rows(vouchers) -> dict:
	"""{(voucher_type, voucher_no): its item rows -- name, warehouse, cost centre, net amount}."""
	by_type = defaultdict(list)
	for voucher_type, voucher_no in vouchers:
		by_type[voucher_type].append(voucher_no)

	out = defaultdict(list)
	for voucher_type, names in by_type.items():
		item = frappe.qb.DocType(cb.SALES_ITEM_DOCTYPE[voucher_type])
		for chunk in cb.chunks(names):
			rows = (
				frappe.qb.from_(item)
				.select(item.name, item.parent, item.warehouse, item.cost_center, item.base_net_amount)
				.where(item.parent.isin(chunk))
				.run(as_dict=True)
			)
			for r in rows:
				out[(voucher_type, r.parent)].append(r)
	return out


def visible_header(header, voucher_type: str, filters, scope) -> bool:
	"""The customer filter, and the User Permissions that sit on the voucher's own header."""
	if filters.customer and header.customer != filters.customer:
		return False
	return all(
		cb.permitted(scope, doctype, header.get(field), voucher_type)
		for doctype, field in (("Company", "company"), ("Customer", "customer"), ("Branch", "branch"))
	)


def visible_lines(entries, item_rows, voucher_type: str, scope) -> bool:
	"""False when any row of the voucher lies in a warehouse or cost centre the user is restricted away from.

	Frappe holds a document back when ANY of its rows falls outside a User Permission, so a voucher
	with one forbidden line is not shown at all -- a partial voucher would put a wrong total on screen."""
	if not scope.get("Warehouse") and not scope.get("Cost Center"):
		return True
	return all(cb.permitted(scope, "Warehouse", e.warehouse, voucher_type) for e in entries) and all(
		cb.permitted(scope, "Warehouse", r.warehouse, voucher_type)
		and cb.permitted(scope, "Cost Center", r.cost_center, voucher_type)
		for r in item_rows
	)


def line_matches(entry, filters) -> bool:
	return not (
		(filters.item_code and entry.item_code != filters.item_code)
		or (filters.warehouse and entry.warehouse != filters.warehouse)
	)


def build_rows(vouchers, entries, shown, headers, item_rows, booked, filters, expand, currency):
	"""The report rows, the count of lines by Check flag, and whether the chain budget ran out."""
	explained = {}
	budget = cb.new_budget()
	if expand:
		outgoing = [e for v in vouchers for e in shown[v] if e.actual_qty < 0]
		snapshots = cb.sources_before_many(outgoing)
		for e in outgoing:
			ex = explained[e.name] = cb.explain(e, snapshots[e.name])
			if cint(filters.expand_chain):
				cb.attach_chain(ex.rows, e.item_code, budget)
	totals = receipt_totals_of(explained.values())

	sales = {r.name: flt(r.base_net_amount) for rows in item_rows.values() for r in rows}
	counts = defaultdict(int)
	rows = []
	for voucher in vouchers:
		whole = entries[voucher]
		cogs = sum(-flt(e.stock_value_difference) for e in whole)
		sold = voucher_sales(whole, sales)
		voucher_booked = flt(booked.get(voucher))
		rows.append(
			{
				"indent": 0,
				"doc_type": voucher[0],
				"document": voucher[1],
				"posting_date": whole[0].posting_date,
				"customer": headers[voucher].customer,
				"value": cogs,
				"gl_cogs": voucher_booked,
				"difference": cogs - voucher_booked,
				"sales": sold,
				"margin": (sold - cogs) if sold is not None else None,
				"margin_pct": margin_pct(sold, cogs),
				"currency": currency,
			}
		)
		for e in shown[voucher]:
			qty = -flt(e.actual_qty)
			value = -flt(e.stock_value_difference)
			line_sales = sales.get(e.voucher_detail_no)
			ex = explained.get(e.name)
			rows.append(
				{
					"indent": 1,
					"item_code": e.item_code,
					"item_name": frappe.get_cached_value("Item", e.item_code, "item_name"),
					"warehouse": e.warehouse,
					"valuation_method": cb.valuation_method(e.item_code),
					"qty": qty,
					"rate": (value / qty) if qty else 0,
					"value": value,
					"sales": line_sales,
					"margin": (line_sales - value) if line_sales is not None else None,
					"margin_pct": margin_pct(line_sales, value),
					"check": _(CHECK_LABELS[ex.flag]) if ex else None,
					"check_flag": ex.flag if ex else None,
					"currency": currency,
				}
			)
			if ex:
				counts[ex.flag] += 1
				emit_sources(ex.rows, 2, e, totals, filters.company, currency, rows)
	return rows, counts, budget.spent


def voucher_sales(entries, sales: dict):
	"""What the stock lines of the voucher were sold for, or None when the voucher has no item rows to read."""
	details = {e.voucher_detail_no for e in entries if e.voucher_detail_no in sales}
	return sum(sales[d] for d in details) if details else None


def margin_pct(sold, cost):
	return ((sold - cost) / sold * 100) if sold else None


def receipt_totals_of(explanations) -> dict:
	"""The price / tax / landed-cost totals of every receipt a source row names, in one set of reads."""
	pairs = {
		(s.voucher_type, s.voucher_no)
		for ex in explanations
		for s, _level in cb.walk(ex.rows)
		if s.get("voucher_type") in cb.RECEIPT_ITEM_DOCTYPE
	}
	return cb.receipt_totals(pairs) if pairs else {}


def emit_sources(tree, indent, entry, totals, company, currency, rows) -> None:
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
			"warehouse": s.get("warehouse") or entry.warehouse,
			"source_type": cb.source_type(s),
			"value": s.value,
			"currency": currency,
			"breakdown": 1 if transfer else 0,
			"company": company,
			"gap": 1 if s.get("gap") else 0,
		}
		if s.qty:
			# cost no document explains and no stock stands behind carries a value only
			row.update(qty=s.qty, rate=s.rate)
		rows.append(row)
		kids = list(s.get("children") or [])
		if s.get("voucher_type") in cb.RECEIPT_ITEM_DOCTYPE:
			kids = cb.receipt_components(s, totals, entry.item_code) + kids
		emit_sources(kids, indent + 1, entry, totals, company, currency, rows)


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
	if chain_spent:
		messages.append(
			_(
				"Transfer chains were followed for the first {0} transfers only; the rest keep their Transfer Cost "
				"Breakdown link."
			).format(cb.MAX_CHAIN_LOOKUPS)
		)
	return messages


def get_summary(rows, currency):
	vouchers = [r for r in rows if r["indent"] == 0]
	cogs = sum(flt(r["value"]) for r in vouchers)
	booked = sum(flt(r["gl_cogs"]) for r in vouchers)
	priced = [r for r in vouchers if r["sales"] is not None]
	sold = sum(flt(r["sales"]) for r in priced)
	cost = sum(flt(r["value"]) for r in priced)
	return [
		{"label": _("Vouchers"), "value": len(vouchers), "datatype": "Int"},
		{"label": _("COGS"), "value": cogs, "datatype": "Currency", "currency": currency},
		{"label": _("GL Booked"), "value": booked, "datatype": "Currency", "currency": currency},
		{
			"label": _("Difference"),
			"value": cogs - booked,
			"datatype": "Currency",
			"currency": currency,
			"indicator": "Green" if abs(cogs - booked) < 0.01 else "Red",
		},
		{"label": _("Sales"), "value": sold, "datatype": "Currency", "currency": currency},
		{"label": _("Margin %"), "value": margin_pct(sold, cost) or 0, "datatype": "Percent"},
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
		{
			"label": _("Customer"),
			"fieldname": "customer",
			"fieldtype": "Link",
			"options": "Customer",
			"width": 160,
		},
		{"label": _("Item"), "fieldname": "item_code", "fieldtype": "Link", "options": "Item", "width": 120},
		{"label": _("Item Name"), "fieldname": "item_name", "fieldtype": "Data", "width": 160},
		{
			"label": _("Warehouse"),
			"fieldname": "warehouse",
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
			"label": _("COGS / Value"),
			"fieldname": "value",
			"fieldtype": "Currency",
			"options": "currency",
			"width": 120,
		},
		{
			"label": _("GL Booked"),
			"fieldname": "gl_cogs",
			"fieldtype": "Currency",
			"options": "currency",
			"width": 110,
		},
		{
			"label": _("Difference"),
			"fieldname": "difference",
			"fieldtype": "Currency",
			"options": "currency",
			"width": 100,
		},
		{
			"label": _("Sales"),
			"fieldname": "sales",
			"fieldtype": "Currency",
			"options": "currency",
			"width": 110,
		},
		{
			"label": _("Margin"),
			"fieldname": "margin",
			"fieldtype": "Currency",
			"options": "currency",
			"width": 100,
		},
		{"label": _("Margin %"), "fieldname": "margin_pct", "fieldtype": "Percent", "width": 80},
		{"label": _("Check"), "fieldname": "check", "fieldtype": "Data", "width": 120},
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
