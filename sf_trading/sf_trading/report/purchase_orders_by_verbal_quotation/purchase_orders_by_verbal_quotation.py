# Copyright (c) 2026, Enfono Technologies and contributors
# For license information, please see license.txt

"""Purchase Orders by Verbal Quotation: the orders bought on a verbal quote, and the ones that were not, listed apart.

A Purchase Order is a verbal one when `custom_verbal` (Verbal (No Quotation)) is ticked on it. The report lists the
two kinds in two blocks -- With Verbal Quotation, then Without -- each with its count and value, so the share of
spending that skipped written quotations is plain. For each order it also shows the Supplier Quotations and Material
Requests its lines come from (the links core writes on each line), so an order "without" a verbal quote can be seen
to have its quotations, and one "with" to have none.

It reads the orders through `get_list`, so a user sees the Purchase Orders they may open and no others. Values are in the
company's currency (Company is required); the order's own currency and amount sit beside them. A Purchase Order is an
Import when its currency is not the company's, as the procurement rules (purchase_order_procurement.py) decide it.
"""

import frappe
from frappe import _
from frappe.utils import cint, cstr, flt

VERBAL = "custom_verbal"
VERBAL_COMMENT = "custom_verbal_comment"

#: the Quotation Type filter
ALL = "All"
WITH_VERBAL = "With Verbal Quotation"
WITHOUT_VERBAL = "Without Verbal Quotation"

#: the Document Status filter: blank means Draft and Submitted (a cancelled order is no purchase)
DOCSTATUS = {"Draft": 0, "Submitted": 1, "Cancelled": 2}

LOCAL = "Local"
IMPORT = "Import"

CHUNK = 1000


def execute(filters=None):
	filters = frappe._dict(filters or {})
	if not filters.company:
		frappe.throw(_("Pick a Company."))
	if filters.get("quotation_type") not in (None, "", ALL, WITH_VERBAL, WITHOUT_VERBAL):
		frappe.throw(_("{0} is not a Quotation Type.").format(frappe.bold(cstr(filters.quotation_type))))

	company_currency = frappe.get_cached_value("Company", filters.company, "default_currency")
	orders = fetch_orders(filters, company_currency)
	links = fetch_links([order.name for order in orders])
	view = filters.get("quotation_type") or ALL
	rows = build_rows(orders, links, company_currency, view)
	return columns(), rows, None, chart(orders), summary(orders, company_currency)


# ── The data ────────────────────────────────────────────────────────────────────────────────────────────────


def is_verbal(order) -> bool:
	return bool(cint(order.get(VERBAL)))


def order_filters(filters, company_currency, meta) -> list:
	"""The Purchase Order filters for `get_list`."""
	found = [["company", "=", filters.company]]
	if filters.get("docstatus") in DOCSTATUS:
		found.append(["docstatus", "=", DOCSTATUS[filters.docstatus]])
	else:
		found.append(["docstatus", "in", [0, 1]])
	if filters.get("from_date"):
		found.append(["transaction_date", ">=", filters.from_date])
	if filters.get("to_date"):
		found.append(["transaction_date", "<=", filters.to_date])
	if filters.get("supplier"):
		found.append(["supplier", "=", filters.supplier])
	if filters.get("branch") and meta.has_field("branch"):
		found.append(["branch", "=", filters.branch])
	if filters.get("user"):
		found.append(["owner", "=", filters.user])
	if filters.get("purchase_type") == IMPORT:
		found.append(["currency", "!=", company_currency])
	elif filters.get("purchase_type") == LOCAL:
		found.append(["currency", "=", company_currency])
	if meta.has_field(VERBAL):
		if filters.get("quotation_type") == WITH_VERBAL:
			found.append([VERBAL, "=", 1])
		elif filters.get("quotation_type") == WITHOUT_VERBAL:
			found.append([VERBAL, "!=", 1])
	return found


def fetch_orders(filters, company_currency) -> list:
	meta = frappe.get_meta("Purchase Order")
	fields = [
		"name", "transaction_date", "supplier", "currency", "grand_total", "base_grand_total", "status", "docstatus",
		"owner",
	]
	fields += [f for f in ("branch", VERBAL, VERBAL_COMMENT) if meta.has_field(f)]
	return frappe.get_list(
		"Purchase Order",
		filters=order_filters(filters, company_currency, meta),
		fields=fields,
		order_by="transaction_date desc, creation desc",
		limit_page_length=0,
	)


def fetch_links(names) -> dict:
	"""{order: {"material_requests": [...], "quotations": [...]}} from the links on the order's lines."""
	if not names:
		return {}
	links = {}
	line = frappe.qb.DocType("Purchase Order Item")
	for start in range(0, len(names), CHUNK):
		rows = (
			frappe.qb.from_(line)
			.select(line.parent, line.material_request, line.supplier_quotation)
			.where(line.parenttype == "Purchase Order")
			.where(line.parent.isin(names[start : start + CHUNK]))
			.run(as_dict=True)
		)
		for row in rows:
			found = links.setdefault(row.parent, {"material_requests": set(), "quotations": set()})
			if cstr(row.material_request).strip():
				found["material_requests"].add(row.material_request)
			if cstr(row.supplier_quotation).strip():
				found["quotations"].add(row.supplier_quotation)
	return {name: {key: sorted(values) for key, values in found.items()} for name, found in links.items()}


# ── The rows (pure: orders in, rows out) ────────────────────────────────────────────────────────────────────


def order_row(order, links, company_currency) -> dict:
	found = links.get(order.name) or {}
	quotations = found.get("quotations") or []
	return {
		"purchase_order": order.name,
		"transaction_date": order.transaction_date,
		"supplier": order.supplier,
		"branch": order.get("branch"),
		"purchase_type": _("Import") if order.currency != company_currency else _("Local"),
		"verbal": 1 if is_verbal(order) else 0,
		"verbal_comment": order.get(VERBAL_COMMENT),
		"currency": order.currency,
		"grand_total": flt(order.grand_total),
		"company_currency": company_currency,
		"base_grand_total": flt(order.base_grand_total),
		"status": order.status,
		"quotation_count": len(quotations),
		"supplier_quotations": ", ".join(quotations),
		"material_requests": ", ".join(found.get("material_requests") or []),
		"owner": order.owner,
		"is_group": 0,
	}


def group_row(label, orders, company_currency) -> dict:
	return {
		"purchase_order": f"{label} ({len(orders)})",
		"company_currency": company_currency,
		"base_grand_total": sum(flt(o.base_grand_total) for o in orders),
		"quotation_count": None,
		"is_group": 1,
	}


def build_rows(orders, links, company_currency, view=ALL) -> list:
	"""The orders in two blocks, With Verbal Quotation then Without, each headed by its count and value; a Total row
	closes the report when both blocks are shown."""
	blocks = []
	if view in (ALL, WITH_VERBAL):
		blocks.append((_("With Verbal Quotation"), [o for o in orders if is_verbal(o)]))
	if view in (ALL, WITHOUT_VERBAL):
		blocks.append((_("Without Verbal Quotation"), [o for o in orders if not is_verbal(o)]))

	rows = []
	for label, block in blocks:
		rows.append(group_row(label, block, company_currency))
		rows += [order_row(order, links, company_currency) for order in block]
	if len(blocks) > 1:
		rows.append(group_row(_("Total"), orders, company_currency))
	return rows


# ── Columns, chart, summary ─────────────────────────────────────────────────────────────────────────────────


def _column(label, fieldname, fieldtype="Data", width=100, **extra):
	return {"label": _(label), "fieldname": fieldname, "fieldtype": fieldtype, "width": width, **extra}


def columns() -> list:
	return [
		_column("Purchase Order", "purchase_order", "Link", 190, options="Purchase Order"),
		_column("Date", "transaction_date", "Date", 95),
		_column("Supplier", "supplier", "Link", 170, options="Supplier"),
		_column("Branch", "branch", "Link", 100, options="Branch"),
		_column("Purchase Type", "purchase_type", "Data", 95),
		_column("Verbal Quotation", "verbal", "Check", 100),
		_column("Verbal Comment", "verbal_comment", "Data", 230),
		_column("Currency", "currency", "Link", 70, options="Currency"),
		_column("Grand Total", "grand_total", "Currency", 120, options="currency"),
		_column("Grand Total (Company Currency)", "base_grand_total", "Currency", 150, options="company_currency"),
		_column("Status", "status", "Data", 130),
		_column("Quotations", "quotation_count", "Int", 90),
		_column("Supplier Quotations", "supplier_quotations", "Data", 200),
		_column("Material Requests", "material_requests", "Data", 200),
		_column("Created By", "owner", "Link", 170, options="User"),
	]


def chart(orders):
	if not orders:
		return None
	verbal = sum(1 for o in orders if is_verbal(o))
	return {
		"data": {
			"labels": [_("With Verbal Quotation"), _("Without Verbal Quotation")],
			"datasets": [{"name": _("Purchase Orders"), "values": [verbal, len(orders) - verbal]}],
		},
		"type": "donut",
	}


def summary(orders, company_currency) -> list:
	verbal = [o for o in orders if is_verbal(o)]
	plain = [o for o in orders if not is_verbal(o)]
	share = f"{len(verbal) * 100.0 / len(orders):.1f}%" if orders else "-"
	return [
		{"value": len(verbal), "label": _("With Verbal Quotation"), "datatype": "Int", "indicator": "Orange"},
		{"value": len(plain), "label": _("Without Verbal Quotation"), "datatype": "Int", "indicator": "Green"},
		{
			"value": sum(flt(o.base_grand_total) for o in verbal),
			"label": _("Value With Verbal Quotation"),
			"datatype": "Currency",
			"currency": company_currency,
			"indicator": "Orange",
		},
		{
			"value": sum(flt(o.base_grand_total) for o in plain),
			"label": _("Value Without Verbal Quotation"),
			"datatype": "Currency",
			"currency": company_currency,
			"indicator": "Green",
		},
		{"value": share, "label": _("Orders on a Verbal Quotation"), "datatype": "Data", "indicator": "Blue"},
	]
