# sf_trading/sf_trading/report/pending_supporting_documents/pending_supporting_documents.py
"""Pending Supporting Documents: every accounting entry posted without the document behind it.

An entry is listed while nothing is attached to it and leaves the list as soon as something is --
the list is worked out from the entries and their attachments every time it runs
(sf_trading.supporting_documents). Which entries need a document is set on SF Trading Settings >
Supporting Documents.
"""

from collections import defaultdict

import frappe
from frappe import _
from frappe.utils import add_months, cint, flt, getdate, nowdate

from sf_trading import supporting_documents as sd

DETAIL = "Detail"
GROUPS = {
	"By Document Type": ("document_type", _("Document Type"), "Link", "DocType"),
	"By Posted By": ("posted_by", _("Posted By"), "Link", "User"),
	"By Branch": ("branch", _("Branch"), "Link", "Branch"),
	"By Party": ("party", _("Party"), "Data", None),
	"By Month": ("month", _("Month"), "Data", None),
}
AGEING = ((0, 7, "0-7"), (8, 30, "8-30"), (31, 90, "31-90"), (91, None, "90+"))


def execute(filters=None):
	filters = frappe._dict(filters or {})
	if not filters.get("company"):
		frappe.throw(_("Please choose a company."))
	filters.from_date = filters.get("from_date") or add_months(getdate(nowdate()), -3)
	filters.to_date = filters.get("to_date") or nowdate()
	doc_types = filters.get("document_type")
	if isinstance(doc_types, str):
		doc_types = frappe.parse_json(doc_types) if doc_types.startswith("[") else doc_types.split(",")
	filters.document_type = [d.strip() for d in (doc_types or []) if d and d.strip()]

	rows = sd.pending_entries(filters)
	status = filters.get("status") or sd.PENDING
	if status != "All":
		rows = [r for r in rows if r.status == status]
	if cint(filters.get("min_days_pending")):
		rows = [r for r in rows if cint(r.days_pending) >= cint(filters.min_days_pending)]
	currency = frappe.get_cached_value("Company", filters.company, "default_currency")
	for r in rows:
		r.month = getdate(r.posting_date).strftime("%Y-%m") if r.posting_date else ""
		r.ageing = bucket(r.days_pending) if r.status == sd.PENDING else ""
		r.currency = currency

	summary = cards(rows, currency)
	view = filters.get("view") or DETAIL
	if view in GROUPS:
		key, label, fieldtype, options = GROUPS[view]
		return grouped_columns(label, fieldtype, options), grouped(rows, key), None, None, summary
	rows.sort(key=lambda r: (-cint(r.days_pending), r.document_type, r.document))
	return detail_columns(), rows, note(), None, summary


def bucket(days):
	for low, high, label in AGEING:
		if days >= low and (high is None or days <= high):
			return label
	return ""


def grouped(rows, key):
	groups = defaultdict(lambda: frappe._dict(count=0, pending=0, amount=0.0, oldest=0, attached=0))
	for r in rows:
		g = groups[r.get(key) or _("(not set)")]
		g.count += 1
		if r.status == sd.PENDING:
			g.pending += 1
			g.amount += flt(r.amount)
			g.oldest = max(g.oldest, cint(r.days_pending))
		else:
			g.attached += 1
	out = []
	for value, g in sorted(groups.items(), key=lambda kv: -kv[1].pending):
		out.append({"group": value, "entries": g.count, "pending": g.pending, "attached": g.attached,
			"pending_amount": flt(g.amount, 3), "oldest_days": g.oldest,
			"pending_share": flt(g.pending * 100.0 / g.count, 1) if g.count else 0})
	return out


def cards(rows, currency):
	pending = [r for r in rows if r.status == sd.PENDING]
	return [
		{"label": _("Pending Entries"), "value": len(pending), "datatype": "Int", "indicator": "Orange" if pending else "Green"},
		{"label": _("Pending Amount"), "value": flt(sum(flt(r.amount) for r in pending), 3), "datatype": "Currency",
			"currency": currency, "indicator": "Orange"},
		{"label": _("Older Than 30 Days"), "value": len([r for r in pending if cint(r.days_pending) > 30]),
			"datatype": "Int", "indicator": "Red"},
		{"label": _("Oldest (days)"), "value": max([cint(r.days_pending) for r in pending], default=0),
			"datatype": "Int", "indicator": "Grey"},
	]


def note():
	parts = []
	for r in sd.rules():
		part = _(r.document_type)
		if r.get("payment_type"):
			part += " (" + _(r.payment_type) + ")"
		if flt(r.get("minimum_amount")):
			part += " " + _("from") + " " + frappe.format_value(r.minimum_amount, "Currency")
		parts.append(part)
	return (_("Entries that need a supporting document") + ": " + ", ".join(parts) + ". "
		+ _("Attach the document to the entry and it leaves this list. The rules are on SF Trading Settings > Supporting Documents."))


def detail_columns():
	return [
		{"label": _("Document Type"), "fieldname": "document_type", "fieldtype": "Link", "options": "DocType", "width": 140},
		{"label": _("Document"), "fieldname": "document", "fieldtype": "Dynamic Link", "options": "document_type", "width": 175},
		{"label": _("Posting Date"), "fieldname": "posting_date", "fieldtype": "Date", "width": 105},
		{"label": _("Days Pending"), "fieldname": "days_pending", "fieldtype": "Int", "width": 100},
		{"label": _("Ageing"), "fieldname": "ageing", "fieldtype": "Data", "width": 70},
		{"label": _("Status"), "fieldname": "status", "fieldtype": "Data", "width": 85},
		{"label": _("Amount"), "fieldname": "amount", "fieldtype": "Currency", "options": "currency", "width": 120},
		{"label": _("Party Type"), "fieldname": "party_type", "fieldtype": "Data", "width": 90},
		{"label": _("Party"), "fieldname": "party", "fieldtype": "Dynamic Link", "options": "party_type", "width": 160},
		{"label": _("Type"), "fieldname": "detail", "fieldtype": "Data", "width": 110},
		{"label": _("Branch"), "fieldname": "branch", "fieldtype": "Link", "options": "Branch", "width": 110},
		{"label": _("Posted By"), "fieldname": "posted_by", "fieldtype": "Link", "options": "User", "width": 170},
		{"label": _("Files"), "fieldname": "attachments", "fieldtype": "Int", "width": 60},
		{"label": _("Remarks"), "fieldname": "remarks", "fieldtype": "Data", "width": 260},
		{"label": _("Currency"), "fieldname": "currency", "fieldtype": "Link", "options": "Currency", "hidden": 1},
	]


def grouped_columns(label, fieldtype, options):
	group = {"label": label, "fieldname": "group", "fieldtype": fieldtype, "width": 200}
	if options:
		group["options"] = options
	return [
		group,
		{"label": _("Entries"), "fieldname": "entries", "fieldtype": "Int", "width": 80},
		{"label": _("Pending"), "fieldname": "pending", "fieldtype": "Int", "width": 80},
		{"label": _("Attached"), "fieldname": "attached", "fieldtype": "Int", "width": 80},
		{"label": _("% Pending"), "fieldname": "pending_share", "fieldtype": "Percent", "width": 90},
		{"label": _("Pending Amount"), "fieldname": "pending_amount", "fieldtype": "Currency", "width": 140},
		{"label": _("Oldest (days)"), "fieldname": "oldest_days", "fieldtype": "Int", "width": 100},
	]
