# Copyright (c) 2026, Enfono Technologies and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.utils import cint, date_diff, getdate, nowdate

PARTY_NAME_FIELD = {"Customer": "customer_name", "Supplier": "supplier_name"}


def execute(filters=None):
	filters = filters or {}
	columns = _get_columns()
	data = _get_data(filters)
	return columns, data


def _get_columns():
	return [
		{"label": _("Party Type"), "fieldname": "parenttype", "fieldtype": "Data", "width": 90},
		{"label": _("Party"), "fieldname": "party", "fieldtype": "Dynamic Link", "options": "parenttype", "width": 220},
		{"label": _("Document Type"), "fieldname": "document_type", "fieldtype": "Data", "width": 160},
		{"label": _("Document Number"), "fieldname": "document_number", "fieldtype": "Data", "width": 140},
		{"label": _("Expiry Date"), "fieldname": "expiry_date", "fieldtype": "Date", "width": 100},
		{"label": _("Days To Expiry"), "fieldname": "days_to_expiry", "fieldtype": "Int", "width": 110},
		{"label": _("Status"), "fieldname": "status", "fieldtype": "Data", "width": 110},
	]


def _get_data(filters):
	today = getdate(nowdate())
	party_type = filters.get("party_type")
	status_filter = filters.get("status")

	conditions = ["csd.expiry_date IS NOT NULL"]
	values = []
	if party_type:
		conditions.append("csd.parenttype = %s")
		values.append(party_type)
	else:
		conditions.append("csd.parenttype IN ('Customer', 'Supplier')")

	if filters.get("from_date"):
		conditions.append("csd.expiry_date >= %s")
		values.append(filters["from_date"])
	if filters.get("to_date"):
		conditions.append("csd.expiry_date <= %s")
		values.append(filters["to_date"])

	rows = frappe.db.sql(
		f"""
		SELECT csd.parent, csd.parenttype, csd.document_type, csd.document_number, csd.expiry_date
		FROM `tabCustomer Supporting Document` csd
		WHERE {" AND ".join(conditions)}
		ORDER BY csd.expiry_date ASC
		""",
		tuple(values),
		as_dict=True,
	)

	# batch-resolve display names per party type, no N+1
	by_type = {}
	for r in rows:
		by_type.setdefault(r.parenttype, set()).add(r.parent)
	names = {}
	for ptype, parents in by_type.items():
		field = PARTY_NAME_FIELD[ptype]
		for p in frappe.get_all(ptype, filters={"name": ["in", list(parents)]}, fields=["name", field]):
			names[(ptype, p.name)] = p.get(field) or p.name

	out = []
	for r in rows:
		days = date_diff(r.expiry_date, today)
		status = _("Expired") if days < 0 else (_("Expiring Soon") if days <= 30 else _("Valid"))
		if status_filter and status_filter != status:
			continue
		out.append(
			{
				"parenttype": r.parenttype,
				"party": r.parent,
				"party_name": names.get((r.parenttype, r.parent), r.parent),
				"document_type": r.document_type,
				"document_number": r.document_number,
				"expiry_date": r.expiry_date,
				"days_to_expiry": cint(days),
				"status": status,
			}
		)
	return out
