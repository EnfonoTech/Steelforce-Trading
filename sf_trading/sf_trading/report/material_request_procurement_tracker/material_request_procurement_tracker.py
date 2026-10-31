# Copyright (c) 2026, Enfono Technologies and contributors
# For license information, please see license.txt

"""Material Request Procurement Tracker: where every Material Request has got to in the buying chain.

The chain is Material Request -> Request for Quotation -> Supplier Quotation -> Purchase Order. This report starts from the
Material Requests of type Purchase and follows each line down it, so a buyer sees at once which requests have no Request
for Quotation, which Requests are still waiting on suppliers, which have every quotation in, and which are ordered.

THREE VIEWS (the `View` filter)
-------------------------------
Material Request-wise  one line per Material Request: items, how many have an RFQ, a quotation, an order, the RFQs with how
                       many of their suppliers have answered, the quotations and the orders, and the stage.
RFQ-wise               one line per Request for Quotation raised from those requests: its Material Requests, suppliers
                       invited and answered, who is still outstanding, and whether it is complete.
Item-wise              one line per Material Request line, the same facts for that item.

THE STAGES
----------
Worked out per line, most advanced first:

    Ordered                      the ordered quantity covers the requested one
    Partly Ordered               some of it is on Purchase Orders
    All Quotations Received      every supplier invited on its Request for Quotation has a submitted quotation for the line
    Quotations Partly Received   some have, some have not
    Awaiting Quotations          a submitted Request for Quotation, no submitted quotation for the line yet
    RFQ Not Submitted            a Request for Quotation exists but is still a draft
    No RFQ                       no Request for Quotation names the line

A Material Request takes the stage of its LEAST advanced line, so one line with no RFQ keeps the request at No RFQ and the
counts beside it show how far the rest have gone. An RFQ is Complete when every supplier invited has a submitted
quotation on it, whichever items they quoted.

A quotation counts once it is SUBMITTED; a draft is work in progress. A line is matched to its RFQ line by the row link core
writes (`material_request_item`, `request_for_quotation_item`), and by item code where an older document carries none.
Ordered quantity is the Material Request line's own `ordered_qty`, which the Purchase Order keeps up to date.

The summary cards and the chart count Material Requests by stage over everything the other filters keep, so they do not
change with the Stage filter.
"""

from __future__ import annotations

import frappe
from frappe import _
from frappe.utils import cint, cstr, flt, getdate, nowdate

from sf_trading.query import fetch_in

VIEW_MR = "Material Request-wise"
VIEW_RFQ = "RFQ-wise"
VIEW_ITEM = "Item-wise"
VIEWS = (VIEW_MR, VIEW_RFQ, VIEW_ITEM)

NO_RFQ = "No RFQ"
RFQ_DRAFT = "RFQ Not Submitted"
AWAITING = "Awaiting Quotations"
PARTLY = "Quotations Partly Received"
COMPLETE = "All Quotations Received"
PART_ORDERED = "Partly Ordered"
ORDERED = "Ordered"
STAGES = (NO_RFQ, RFQ_DRAFT, AWAITING, PARTLY, COMPLETE, PART_ORDERED, ORDERED)
RANK = {stage: position for position, stage in enumerate(STAGES)}

ALL = "All"
#: the statuses of a Material Request worth filtering a purchase tracker by
STATUSES = ("Pending", "Partially Ordered", "Ordered", "Stopped", "Draft")


def execute(filters=None):
	filters = frappe._dict(filters or {})
	validate_filters(filters)

	world = collect(filters)
	view = resolve_view(filters)
	if view == VIEW_RFQ:
		columns, rows = get_rfq_columns(), rfq_rows(world, filters)
	elif view == VIEW_ITEM:
		columns, rows = get_item_columns(), item_rows(world, filters)
	else:
		columns, rows = get_request_columns(), request_rows(world, filters)

	return columns, rows, get_message(), chart_of(world), summary_of(world)


def validate_filters(filters):
	if not filters.get("company"):
		frappe.throw(_("Company is required"))
	if not (filters.get("from_date") and filters.get("to_date")):
		frappe.throw(_("From Date and To Date are required"))
	if getdate(filters.from_date) > getdate(filters.to_date):
		frappe.throw(_("From Date cannot be after To Date"))


def resolve_view(filters):
	view = filters.get("view")
	return view if view in VIEWS else VIEW_MR


def wanted_stage(filters):
	stage = filters.get("stage")
	return stage if stage in STAGES else None


# ---------------------------------------------------------------------------
# reading the chain
# ---------------------------------------------------------------------------


def get_material_requests(filters) -> list:
	"""The Material Requests of type Purchase the user may read, in the period."""
	status = filters.get("status")
	where = {
		"company": filters.company,
		"material_request_type": "Purchase",
		"transaction_date": ["between", [filters.from_date, filters.to_date]],
		"docstatus": 0 if status == "Draft" else 1,
	}
	if filters.get("material_request"):
		where["name"] = filters.material_request
	if filters.get("raised_by"):
		where["owner"] = filters.raised_by
	if status in STATUSES and status != "Draft":
		where["status"] = status
	return frappe.get_list(
		"Material Request",
		filters=where,
		fields=["name", "transaction_date", "schedule_date", "status", "owner"],
		order_by="transaction_date desc, name desc",
		limit_page_length=0,
	)


def get_items(names, filters) -> list:
	where = {"parenttype": "Material Request"}
	for field in ("item_code", "item_group", "warehouse"):
		if filters.get(field):
			where[field] = filters.get(field)
	return fetch_in(
		"Material Request Item",
		names,
		in_field="parent",
		filters=where,
		fields=[
			"name",
			"parent",
			"idx",
			"item_code",
			"item_name",
			"qty",
			"stock_qty",
			"uom",
			"stock_uom",
			"warehouse",
			"schedule_date",
			"ordered_qty",
		],
		order_by="parent asc, idx asc",
	)


def collect(filters) -> frappe._dict:
	"""Everything the views draw from: the requests, their lines, and the chain under each line, already worked out."""
	requests = get_material_requests(filters)
	by_request = {}
	for item in get_items([request.name for request in requests], filters):
		by_request.setdefault(item.parent, []).append(item)
	requests = [request for request in requests if by_request.get(request.name)]
	names = [request.name for request in requests]

	rfq_lines = fetch_in(
		"Request for Quotation Item",
		names,
		in_field="material_request",
		filters={"parenttype": "Request for Quotation", "docstatus": ["<", 2]},
		fields=["name", "parent", "material_request", "material_request_item", "item_code"],
	)
	wanted = filters.get("request_for_quotation")
	if wanted:
		rfq_lines = [line for line in rfq_lines if line.parent == wanted]
	rfq_names = sorted({line.parent for line in rfq_lines})

	heads = {
		row.name: row
		for row in fetch_in("Request for Quotation", rfq_names, fields=["name", "docstatus", "status", "transaction_date"])
	}
	invited = {}
	for row in fetch_in(
		"Request for Quotation Supplier",
		rfq_names,
		in_field="parent",
		filters={"parenttype": "Request for Quotation"},
		fields=["parent", "supplier", "supplier_name"],
		order_by="parent asc, idx asc",
	):
		invited.setdefault(row.parent, {})[row.supplier] = row.supplier_name or row.supplier

	quote_lines = fetch_in(
		"Supplier Quotation Item",
		rfq_names,
		in_field="request_for_quotation",
		filters={"parenttype": "Supplier Quotation", "docstatus": 1},
		fields=["parent", "request_for_quotation", "request_for_quotation_item", "item_code"],
	)
	quote_heads = {
		row.name: row
		for row in fetch_in(
			"Supplier Quotation", sorted({line.parent for line in quote_lines}), fields=["name", "supplier", "supplier_name"]
		)
	}
	order_lines = fetch_in(
		"Purchase Order Item",
		names,
		in_field="material_request",
		filters={"parenttype": "Purchase Order", "docstatus": ["<", 2]},
		fields=["parent", "material_request", "material_request_item"],
	)

	world = frappe._dict(
		requests=requests,
		rfq_lines=rfq_lines,
		rfq_heads=heads,
		invited=invited,
		quote_lines=quote_lines,
		quote_heads=quote_heads,
		order_lines=order_lines,
		rfq_names=rfq_names,
	)
	# indexes, so each line is found without walking every list again
	world.rfq_by_request = group(rfq_lines, "material_request")
	world.rfq_lines_by_rfq = group(rfq_lines, "parent")
	world.quote_by_rfq = group(quote_lines, "request_for_quotation")
	world.orders_by_item = {}
	for line in order_lines:
		world.orders_by_item.setdefault(line.material_request_item, set()).add(line.parent)
	world.answers = {}
	for rfq, lines in world.quote_by_rfq.items():
		for line in lines:
			head = quote_heads.get(line.parent)
			if head:
				world.answers.setdefault(rfq, {})[head.supplier] = head.supplier_name or head.supplier
	world.lines = {}
	for request in requests:
		world.lines[request.name] = [line_state(item, world) for item in by_request[request.name]]
	keep_supplier(world, filters)
	return world


def group(rows, fieldname) -> dict:
	grouped = {}
	for row in rows:
		grouped.setdefault(row.get(fieldname), []).append(row)
	return grouped


def keep_supplier(world, filters):
	"""The Supplier filter keeps the requests whose Requests for Quotation invited that supplier."""
	supplier = filters.get("supplier")
	if not supplier:
		return
	world.requests = [
		request
		for request in world.requests
		if any(supplier in world.invited.get(rfq, {}) for line in world.lines[request.name] for rfq in line.rfqs)
	]


def line_state(item, world) -> frappe._dict:
	"""Where one Material Request line has got to."""
	rfq_lines = [
		line
		for line in world.rfq_by_request.get(item.parent, [])
		if line.material_request_item == item.name or (not line.material_request_item and line.item_code == item.item_code)
	]
	rfqs = sorted({line.parent for line in rfq_lines})
	submitted = [rfq for rfq in rfqs if world.rfq_heads.get(rfq) and world.rfq_heads[rfq].docstatus == 1]

	invited = {}
	for rfq in submitted:
		invited.update(world.invited.get(rfq, {}))

	rfq_line_names = {line.name for line in rfq_lines}
	answered, quotations = {}, set()
	for rfq in submitted:
		for line in world.quote_by_rfq.get(rfq, []):
			matches = line.request_for_quotation_item in rfq_line_names or (
				not line.request_for_quotation_item and line.item_code == item.item_code
			)
			head = world.quote_heads.get(line.parent)
			if matches and head:
				answered[head.supplier] = head.supplier_name or head.supplier
				quotations.add(line.parent)

	pending = {supplier: name for supplier, name in invited.items() if supplier not in answered}
	requested = flt(item.stock_qty) or flt(item.qty)
	ordered = min(flt(item.ordered_qty), requested) if requested else flt(item.ordered_qty)

	return frappe._dict(
		item=item,
		rfqs=rfqs,
		submitted=submitted,
		invited=invited,
		answered=answered,
		pending=pending,
		quotations=sorted(quotations),
		orders=sorted(world.orders_by_item.get(item.name, set())),
		requested=requested,
		ordered=ordered,
		quote_stage=quote_stage(rfqs, submitted, invited, answered),
		stage=stage_of(rfqs, submitted, invited, answered, requested, flt(item.ordered_qty)),
	)


def quote_stage(rfqs, submitted, invited, answered) -> str:
	"""The stage a line has reached on quotations alone, whatever has been ordered."""
	if not rfqs:
		return NO_RFQ
	if not submitted:
		return RFQ_DRAFT
	if not answered:
		return AWAITING
	if set(invited) - set(answered):
		return PARTLY
	return COMPLETE


def stage_of(rfqs, submitted, invited, answered, requested, ordered) -> str:
	if requested and ordered >= requested - 0.000001:
		return ORDERED
	if ordered > 0:
		return PART_ORDERED
	return quote_stage(rfqs, submitted, invited, answered)


def request_stage(lines) -> str:
	"""A Material Request is as far on as its least advanced line."""
	return min((line.stage for line in lines), key=lambda stage: RANK[stage])


# ---------------------------------------------------------------------------
# view 1: Material Request-wise
# ---------------------------------------------------------------------------


def request_rows(world, filters) -> list:
	rows = []
	today = getdate(nowdate())
	stage_wanted = wanted_stage(filters)
	for request in world.requests:
		lines = world.lines[request.name]
		stage = request_stage(lines)
		if stage_wanted and stage != stage_wanted:
			continue
		rfqs = sorted({rfq for line in lines for rfq in line.rfqs})
		submitted = [rfq for rfq in rfqs if world.rfq_heads.get(rfq) and world.rfq_heads[rfq].docstatus == 1]
		invited = {s for rfq in submitted for s in world.invited.get(rfq, {})}
		answered = {a for rfq in submitted for a in rfq_answers(world, rfq)}
		requested = sum(line.requested for line in lines)
		ordered = sum(line.ordered for line in lines)
		rows.append(
			{
				"material_request": request.name,
				"transaction_date": request.transaction_date,
				"schedule_date": request.schedule_date,
				"status": request.status,
				"raised_by": request.owner,
				"items": len(lines),
				"items_with_rfq": sum(1 for line in lines if line.rfqs),
				"items_quoted": sum(1 for line in lines if line.answered),
				"items_fully_quoted": sum(1 for line in lines if line.quote_stage == COMPLETE),
				"items_ordered": sum(1 for line in lines if line.stage == ORDERED),
				"ordered_pct": (ordered / requested * 100) if requested else 0.0,
				"rfqs": ", ".join(rfqs),
				"rfq_progress": "; ".join(progress_of(world, rfq) for rfq in rfqs),
				"rfqs_complete": "%s / %s" % (sum(1 for rfq in submitted if rfq_is_complete(world, rfq)), len(rfqs)),
				"suppliers_invited": len(invited),
				"suppliers_answered": len(answered),
				"quotations": len({q for line in lines for q in line.quotations}),
				"orders": ", ".join(sorted({o for line in lines for o in line.orders})),
				"stage": stage,
				"days_open": (today - getdate(request.transaction_date)).days if request.transaction_date else None,
				"overdue": 1 if request.schedule_date and getdate(request.schedule_date) < today and stage != ORDERED else 0,
				"compare": _("Compare") if rfqs else "",
			}
		)
	return rows


def rfq_answers(world, rfq) -> dict:
	"""{supplier: name} of the suppliers with a submitted quotation on this Request for Quotation, any item."""
	return world.answers.get(rfq, {})


def rfq_is_complete(world, rfq) -> bool:
	invited = world.invited.get(rfq, {})
	return bool(invited) and not (set(invited) - set(rfq_answers(world, rfq)))


def progress_of(world, rfq) -> str:
	"""'PUR-RFQ-1 3/3' : suppliers answered of suppliers invited."""
	head = world.rfq_heads.get(rfq)
	invited = world.invited.get(rfq, {})
	done = len(set(invited) & set(rfq_answers(world, rfq)))
	text = "%s %s/%s" % (rfq, done, len(invited))
	return text if head and head.docstatus == 1 else text + " (" + _("draft") + ")"


# ---------------------------------------------------------------------------
# view 2: RFQ-wise
# ---------------------------------------------------------------------------


def rfq_rows(world, filters) -> list:
	rows = []
	today = getdate(nowdate())
	stage_wanted = wanted_stage(filters)
	supplier = filters.get("supplier")
	for rfq in world.rfq_names:
		head = world.rfq_heads.get(rfq)
		invited = world.invited.get(rfq, {})
		if supplier and supplier not in invited:
			continue
		answered = rfq_answers(world, rfq)
		pending = {s: n for s, n in invited.items() if s not in answered}
		lines = world.rfq_lines_by_rfq.get(rfq, [])
		requests = sorted({line.material_request for line in lines if line.material_request})
		submitted = bool(head and head.docstatus == 1)
		if not submitted:
			stage = RFQ_DRAFT
		elif not answered:
			stage = AWAITING
		elif pending:
			stage = PARTLY
		else:
			stage = COMPLETE
		if stage_wanted and stage != stage_wanted:
			continue
		rows.append(
			{
				"request_for_quotation": rfq,
				"transaction_date": head.transaction_date if head else None,
				"status": (head.status if head else "") or "",
				"material_requests": ", ".join(requests),
				"items": len({line.item_code for line in lines}),
				"suppliers_invited": len(invited),
				"suppliers_answered": len(set(invited) & set(answered)),
				"answered_names": ", ".join(sorted(answered.values())),
				"pending_names": ", ".join(sorted(pending.values())),
				"quotations": len({line.parent for line in world.quote_by_rfq.get(rfq, [])}),
				"stage": stage,
				"complete": 1 if stage == COMPLETE else 0,
				"days_open": (today - getdate(head.transaction_date)).days if head and head.transaction_date else None,
				"compare": _("Compare"),
			}
		)
	return rows


# ---------------------------------------------------------------------------
# view 3: item-wise
# ---------------------------------------------------------------------------


def item_rows(world, filters) -> list:
	rows = []
	stage_wanted = wanted_stage(filters)
	for request in world.requests:
		for line in world.lines[request.name]:
			if stage_wanted and line.stage != stage_wanted:
				continue
			item = line.item
			rows.append(
				{
					"material_request": request.name,
					"transaction_date": request.transaction_date,
					"item_code": item.item_code,
					"item_name": item.item_name,
					"qty": flt(item.qty),
					"uom": item.uom,
					"warehouse": item.warehouse,
					"schedule_date": item.schedule_date or request.schedule_date,
					"rfqs": ", ".join(line.rfqs),
					"suppliers_invited": len(line.invited),
					"suppliers_answered": len(line.answered),
					"pending_names": ", ".join(sorted(line.pending.values())),
					"quotations": ", ".join(line.quotations),
					"ordered_qty": line.ordered,
					"ordered_pct": (line.ordered / line.requested * 100) if line.requested else 0.0,
					"orders": ", ".join(line.orders),
					"stage": line.stage,
					"compare": _("Compare") if line.rfqs else "",
				}
			)
	return rows


# ---------------------------------------------------------------------------
# the cards and the chart
# ---------------------------------------------------------------------------


def counts_by_stage(world) -> dict:
	counts = {stage: 0 for stage in STAGES}
	for request in world.requests:
		counts[request_stage(world.lines[request.name])] += 1
	return counts


def summary_of(world) -> list:
	counts = counts_by_stage(world)
	waiting = counts[RFQ_DRAFT] + counts[AWAITING]
	ordered = counts[PART_ORDERED] + counts[ORDERED]
	return [
		{"value": len(world.requests), "label": _("Material Requests"), "datatype": "Int", "indicator": "blue"},
		{"value": counts[NO_RFQ], "label": _("No RFQ Yet"), "datatype": "Int", "indicator": "red"},
		{"value": waiting, "label": _("Waiting for Quotations"), "datatype": "Int", "indicator": "orange"},
		{"value": counts[PARTLY], "label": _("Quotations Partly Received"), "datatype": "Int", "indicator": "orange"},
		{"value": counts[COMPLETE], "label": _("All Quotations Received"), "datatype": "Int", "indicator": "green"},
		{"value": ordered, "label": _("Ordered or Partly Ordered"), "datatype": "Int", "indicator": "blue"},
	]


def chart_of(world) -> dict:
	counts = counts_by_stage(world)
	return {
		"data": {
			"labels": [_(stage) for stage in STAGES],
			"datasets": [{"name": _("Material Requests"), "values": [counts[stage] for stage in STAGES]}],
		},
		"type": "bar",
		"colors": ["#6b7280"],
	}


# ---------------------------------------------------------------------------
# columns and message
# ---------------------------------------------------------------------------


def link(label, fieldname, options, width=140) -> dict:
	return {"label": label, "fieldname": fieldname, "fieldtype": "Link", "options": options, "width": width}


def data(label, fieldname, width=110) -> dict:
	return {"label": label, "fieldname": fieldname, "fieldtype": "Data", "width": width}


def number(label, fieldname, width=90, fieldtype="Int") -> dict:
	return {"label": label, "fieldname": fieldname, "fieldtype": fieldtype, "width": width}


def date(label, fieldname, width=95) -> dict:
	return {"label": label, "fieldname": fieldname, "fieldtype": "Date", "width": width}


def get_request_columns() -> list:
	return [
		link(_("Material Request"), "material_request", "Material Request", 150),
		date(_("Date"), "transaction_date"),
		date(_("Required By"), "schedule_date"),
		data(_("Status"), "status", 110),
		data(_("Raised By"), "raised_by", 150),
		data(_("Stage"), "stage", 190),
		number(_("Items"), "items", 60),
		number(_("With RFQ"), "items_with_rfq", 80),
		number(_("Quoted"), "items_quoted", 70),
		number(_("Fully Quoted"), "items_fully_quoted", 100),
		number(_("Ordered"), "items_ordered", 75),
		number(_("Ordered %"), "ordered_pct", 90, "Percent"),
		data(_("RFQs"), "rfqs", 190),
		data(_("Suppliers Answered / Invited per RFQ"), "rfq_progress", 260),
		data(_("RFQs Complete"), "rfqs_complete", 100),
		number(_("Suppliers Invited"), "suppliers_invited", 100),
		number(_("Suppliers Answered"), "suppliers_answered", 110),
		number(_("Quotations"), "quotations", 90),
		data(_("Purchase Orders"), "orders", 190),
		number(_("Days Open"), "days_open", 80),
		number(_("Overdue"), "overdue", 70, "Check"),
		data(_("Compare"), "compare", 80),
	]


def get_rfq_columns() -> list:
	return [
		link(_("Request for Quotation"), "request_for_quotation", "Request for Quotation", 160),
		date(_("Date"), "transaction_date"),
		data(_("Status"), "status", 90),
		data(_("Material Requests"), "material_requests", 190),
		data(_("Stage"), "stage", 190),
		number(_("Complete"), "complete", 80, "Check"),
		number(_("Items"), "items", 60),
		number(_("Suppliers Invited"), "suppliers_invited", 100),
		number(_("Suppliers Answered"), "suppliers_answered", 110),
		number(_("Quotations"), "quotations", 90),
		data(_("Answered By"), "answered_names", 240),
		data(_("Still Outstanding"), "pending_names", 240),
		number(_("Days Open"), "days_open", 80),
		data(_("Compare"), "compare", 80),
	]


def get_item_columns() -> list:
	return [
		link(_("Material Request"), "material_request", "Material Request", 150),
		date(_("Date"), "transaction_date"),
		link(_("Item"), "item_code", "Item", 140),
		data(_("Item Name"), "item_name", 180),
		number(_("Qty"), "qty", 80, "Float"),
		data(_("UOM"), "uom", 70),
		data(_("Warehouse"), "warehouse", 140),
		date(_("Required By"), "schedule_date"),
		data(_("Stage"), "stage", 190),
		data(_("RFQs"), "rfqs", 190),
		number(_("Suppliers Invited"), "suppliers_invited", 100),
		number(_("Suppliers Answered"), "suppliers_answered", 110),
		data(_("Still Outstanding"), "pending_names", 220),
		data(_("Quotations"), "quotations", 200),
		number(_("Ordered Qty"), "ordered_qty", 90, "Float"),
		number(_("Ordered %"), "ordered_pct", 90, "Percent"),
		data(_("Purchase Orders"), "orders", 190),
		data(_("Compare"), "compare", 80),
	]


def get_message() -> str:
	return (
		"<div>"
		+ frappe.utils.escape_html(
			_(
				"A Material Request is as far on as its least advanced line. Quotations count once submitted. "
				"Compare opens Supplier Quotation Comparison Extended on the same request."
			)
		)
		+ "</div>"
	)
