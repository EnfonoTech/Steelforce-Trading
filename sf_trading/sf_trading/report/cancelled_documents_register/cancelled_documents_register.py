# Copyright (c) 2026, Enfono Technologies and contributors
# For license information, please see license.txt

"""Every cancelled selling, buying, stock and accounting document: who cancelled it, and when.

Who cancelled, and when, is read from the document's own Version history -- the save that moved
`docstatus` from 1 to 2 -- because `modified_by` is only the last person to touch the record, which
need not be the one who cancelled it. A document whose doctype keeps no history (Quotation) falls
back to `modified_by` / `modified`, which for a cancelled document is almost always the cancel itself.

Where the cancel went through an approval (the Sales Order Cancellation workflow), the history also
holds the request: the save that moved `workflow_state` into a cancellation state while the document
was still submitted. That user and time are the Initiated By / Initiated On columns, and the reason
the request carried is shown where the document keeps one.

The date filters are on the cancel date, not the document's own date.
"""

import json

import frappe
from frappe import _
from frappe.utils import flt, get_datetime, getdate

from sf_trading.sales_target import allowed_branches

#: doctype, its own date field, party type (or the field that holds it), party field, party name field,
#: amount field (company currency, or None where the document carries no single amount). The vouchers
#: this site raises and cancels; ones it does not cancel (Landed Cost Voucher, Stock Reconciliation, POS Invoice, ...) are left out.
SOURCES = (
	("Quotation", "transaction_date", "quotation_to", "party_name", "customer_name", "base_grand_total"),
	("Sales Order", "transaction_date", "Customer", "customer", "customer_name", "base_grand_total"),
	("Delivery Note", "posting_date", "Customer", "customer", "customer_name", "base_grand_total"),
	("Sales Invoice", "posting_date", "Customer", "customer", "customer_name", "base_grand_total"),
	("Material Request", "transaction_date", None, None, None, None),
	("Purchase Order", "transaction_date", "Supplier", "supplier", "supplier_name", "base_grand_total"),
	("Purchase Receipt", "posting_date", "Supplier", "supplier", "supplier_name", "base_grand_total"),
	("Purchase Invoice", "posting_date", "Supplier", "supplier", "supplier_name", "base_grand_total"),
	("Payment Request", "transaction_date", "party_type", "party", "party_name", "grand_total"),
	("Payment Advice", "transaction_date", "party_type", "party", "party_name", "amount"),
	("Payment Entry", "posting_date", "party_type", "party", "party_name", "base_paid_amount"),
	("Journal Entry", "posting_date", None, None, None, "total_debit"),
	("Stock Entry", "posting_date", None, None, None, "total_outgoing_value"),
)
DOCTYPES = [s[0] for s in SOURCES]
#: the reason a cancel or a cancellation request carried, on the documents that keep one
REASON_FIELDS = ("custom_cancellation_remark", "custom_cancellation_reason")


def execute(filters=None):
	filters = frappe._dict(filters or {})
	if not filters.company:
		frappe.throw(_("Company is required"))
	from_date = getdate(filters.from_date) if filters.from_date else None
	to_date = getdate(filters.to_date) if filters.to_date else None

	branches = allowed_branches()
	if filters.branch and branches is not None and filters.branch not in branches:
		frappe.throw(_("You are not permitted to see branch {0}.").format(frappe.bold(filters.branch)))
	if filters.branch:
		branches = [filters.branch]

	currency = frappe.get_cached_value("Company", filters.company, "default_currency")
	data = []
	for source in SOURCES:
		doctype = source[0]
		if filters.document_type and filters.document_type != doctype:
			continue
		# a doctype the user cannot read is left out, not refused: the register spans many
		if not frappe.db.exists("DocType", doctype) or not frappe.has_permission(doctype, "read"):
			continue
		data.extend(get_rows(source, filters, from_date, branches, currency))

	data = [
		r for r in data
		if (not from_date or getdate(r["cancelled_on"]) >= from_date)
		and (not to_date or getdate(r["cancelled_on"]) <= to_date)
		and (not filters.cancelled_by or r["cancelled_by"] == filters.cancelled_by)
	]
	data.sort(key=lambda r: get_datetime(r["cancelled_on"]), reverse=True)
	return get_columns(), data, None, None, get_summary(data)


def get_rows(source, filters, from_date, branches, currency):
	doctype, date_field, party_type, party_field, party_name_field, amount_field = source
	meta = frappe.get_meta(doctype)

	fields = ["name", "modified", "modified_by", f"{date_field} as document_date"]
	if amount_field:
		fields.append(f"{amount_field} as amount")
	if party_field:
		fields += [f"{party_field} as party", f"{party_name_field} as party_name"]
		if meta.has_field(party_type):
			fields.append(f"{party_type} as party_type")
	if meta.has_field("branch"):
		fields.append("branch")
	reason_fields = [f for f in REASON_FIELDS if meta.has_field(f)]
	fields += reason_fields

	conditions = {"docstatus": 2, "company": filters.company}
	# a cancel is never later than the record's last change, so this narrows without losing any
	if from_date:
		conditions["modified"] = [">=", from_date]
	if branches is not None:
		if not meta.has_field("branch"):
			# branch-restricted users see only what carries a branch they hold
			return []
		conditions["branch"] = ["in", branches]

	docs = frappe.get_all(doctype, filters=conditions, fields=fields)
	if not docs:
		return []
	history = cancel_history(doctype, [d.name for d in docs])

	rows = []
	for d in docs:
		h = history.get(d.name, {})
		rows.append({
			"document_type": doctype,
			"document": d.name,
			"document_date": d.document_date,
			"branch": d.get("branch"),
			"party_type": d.get("party_type") or (party_type if party_field and not meta.has_field(party_type) else None),
			"party": d.get("party"),
			"party_name": d.get("party_name"),
			"amount": flt(d.amount) if amount_field else None,
			"currency": currency,
			"cancelled_by": h.get("cancelled_by") or d.modified_by,
			"cancelled_on": h.get("cancelled_on") or d.modified,
			"initiated_by": h.get("initiated_by"),
			"initiated_on": h.get("initiated_on"),
			"reason": d.get("custom_cancellation_remark") or d.get("custom_cancellation_reason"),
		})
	return rows


def cancel_history(doctype, names) -> dict:
	"""{name: cancelled_by/on, initiated_by/on} read from the Version history of these documents."""
	out = {}
	for chunk_start in range(0, len(names), 500):
		chunk = names[chunk_start : chunk_start + 500]
		versions = frappe.get_all(
			"Version",
			filters={"ref_doctype": doctype, "docname": ["in", chunk]},
			fields=["docname", "owner", "creation", "data"],
			order_by="creation asc",
		)
		for v in versions:
			try:
				changed = json.loads(v.data or "{}").get("changed") or []
			except ValueError:
				continue
			changes = {c[0]: c for c in changed if isinstance(c, (list, tuple)) and len(c) == 3}
			entry = out.setdefault(v.docname, {})
			if "docstatus" in changes and changes["docstatus"][1] == 1 and changes["docstatus"][2] == 2:
				entry["cancelled_by"], entry["cancelled_on"] = v.owner, v.creation
			elif "workflow_state" in changes and "cancel" in str(changes["workflow_state"][2] or "").lower():
				# a request: the state moved toward cancellation while the document stayed submitted.
				# The latest one wins -- a request that was rejected and raised again is the one that counted.
				entry["initiated_by"], entry["initiated_on"] = v.owner, v.creation
	return out


def get_summary(data):
	via_request = sum(1 for r in data if r.get("initiated_by"))
	return [
		{"label": _("Cancelled Documents"), "value": len(data), "datatype": "Int"},
		{"label": _("Through a Cancellation Request"), "value": via_request, "datatype": "Int"},
		{"label": _("Cancelled Directly"), "value": len(data) - via_request, "datatype": "Int"},
	]


def get_columns():
	return [
		{"label": _("Document Type"), "fieldname": "document_type", "fieldtype": "Link", "options": "DocType", "width": 130},
		{"label": _("Document"), "fieldname": "document", "fieldtype": "Dynamic Link", "options": "document_type", "width": 170},
		{"label": _("Document Date"), "fieldname": "document_date", "fieldtype": "Date", "width": 105},
		{"label": _("Branch"), "fieldname": "branch", "fieldtype": "Link", "options": "Branch", "width": 90},
		{"label": _("Party Type"), "fieldname": "party_type", "fieldtype": "Data", "width": 90},
		{"label": _("Party"), "fieldname": "party", "fieldtype": "Dynamic Link", "options": "party_type", "width": 180},
		{"label": _("Amount"), "fieldname": "amount", "fieldtype": "Currency", "options": "currency", "width": 120},
		{"label": _("Initiated By"), "fieldname": "initiated_by", "fieldtype": "Link", "options": "User", "width": 160},
		{"label": _("Initiated On"), "fieldname": "initiated_on", "fieldtype": "Datetime", "width": 150},
		{"label": _("Cancelled By"), "fieldname": "cancelled_by", "fieldtype": "Link", "options": "User", "width": 160},
		{"label": _("Cancelled On"), "fieldname": "cancelled_on", "fieldtype": "Datetime", "width": 150},
		{"label": _("Reason"), "fieldname": "reason", "fieldtype": "Data", "width": 220},
		{"label": _("Currency"), "fieldname": "currency", "fieldtype": "Link", "options": "Currency", "hidden": 1, "width": 80},
	]
