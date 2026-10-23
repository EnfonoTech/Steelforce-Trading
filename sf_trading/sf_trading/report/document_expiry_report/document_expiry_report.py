# Copyright (c) 2026, Enfono Technologies and contributors
# For license information, please see license.txt

"""Document Expiry Report: the supporting documents on Customer and Supplier masters, by expiry date.

Filters: Period -- Daily (expiring today), Weekly (today and the next 7 days), Monthly (today and the next
30 days) or Date Range (From / To Date); blank lists every document. Master (Customer / Supplier) and one
Party of it, Document Type and Status narrow it further.
"""

import frappe
from frappe import _
from frappe.utils import add_days, cint, date_diff, getdate, nowdate

PARTY_NAME_FIELD = {"Customer": "customer_name", "Supplier": "supplier_name"}

DAILY, WEEKLY, MONTHLY, DATE_RANGE = "Daily", "Weekly", "Monthly", "Date Range"
#: how many days after today each period reaches (today itself included)
PERIOD_DAYS = {DAILY: 0, WEEKLY: 7, MONTHLY: 30}


def execute(filters=None):
	filters = filters or {}
	columns = _get_columns()
	data = _get_data(filters)
	return columns, data


def _get_columns():
	return [
		{"label": _("Party Type"), "fieldname": "parenttype", "fieldtype": "Data", "width": 90},
		{"label": _("Party"), "fieldname": "party", "fieldtype": "Dynamic Link", "options": "parenttype", "width": 160},
		{"label": _("Party Name"), "fieldname": "party_name", "fieldtype": "Data", "width": 220},
		{"label": _("Document Type"), "fieldname": "document_type", "fieldtype": "Data", "width": 160},
		{"label": _("Document Number"), "fieldname": "document_number", "fieldtype": "Data", "width": 140},
		{"label": _("Issue Date"), "fieldname": "issue_date", "fieldtype": "Date", "width": 100},
		{"label": _("Expiry Date"), "fieldname": "expiry_date", "fieldtype": "Date", "width": 100},
		{"label": _("Grace Days"), "fieldname": "grace_days", "fieldtype": "Int", "width": 85},
		{"label": _("Financial Implication"), "fieldname": "financial_implication", "fieldtype": "Check", "width": 90},
		{"label": _("Attach"), "fieldname": "attachment", "fieldtype": "Data", "width": 160},
		{"label": _("Days To Expiry"), "fieldname": "days_to_expiry", "fieldtype": "Int", "width": 110},
		{"label": _("Status"), "fieldname": "status", "fieldtype": "Data", "width": 110},
	]


def _period_range(filters, today):
	"""The expiry dates the Period filter covers: from today for Daily / Weekly / Monthly, the From / To
	Date for Date Range (or when no period is picked but dates are), nothing for neither."""
	period = filters.get("period")
	if period in PERIOD_DAYS:
		return today, add_days(today, PERIOD_DAYS[period])
	if period == DATE_RANGE or not period:
		from_date, to_date = filters.get("from_date"), filters.get("to_date")
		if from_date and to_date and getdate(from_date) > getdate(to_date):
			frappe.throw(_("From Date cannot be after To Date."))
		return from_date, to_date
	return None, None


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

	if filters.get("party"):
		conditions.append("csd.parent = %s")
		values.append(filters["party"])
	if filters.get("document_type"):
		conditions.append("csd.document_type = %s")
		values.append(filters["document_type"])

	from_date, to_date = _period_range(filters, today)
	if from_date:
		conditions.append("csd.expiry_date >= %s")
		values.append(from_date)
	if to_date:
		conditions.append("csd.expiry_date <= %s")
		values.append(to_date)

	rows = frappe.db.sql(
		f"""
		SELECT csd.parent, csd.parenttype, csd.document_type, csd.document_number, csd.expiry_date,
			csd.issue_date, csd.grace_days, csd.financial_implication, csd.attachment
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
		# within its grace days a document still counts as valid, though past its date
		status = ("Expired" if days + cint(r.grace_days) < 0
			else "In Grace Period" if days < 0
			else "Expiring Soon" if days <= 30 else "Valid")
		# the filter holds the untranslated value; the row shows it translated
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
				"issue_date": r.issue_date,
				"grace_days": cint(r.grace_days),
				"financial_implication": cint(r.financial_implication),
				"attachment": r.attachment,
				"days_to_expiry": cint(days),
				"status": _(status),
			}
		)
	return out
