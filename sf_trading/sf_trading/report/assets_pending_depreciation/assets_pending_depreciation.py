# Copyright (c) 2026, Enfono Technologies and contributors
# For license information, please see license.txt

"""Assets Pending Depreciation: submitted Assets whose purchase is booked but whose depreciation has
not started.

Two kinds, in the Pending column:

* On Hold -- Depreciation On Hold, no schedule yet; it is started from the Asset with Start
  Depreciation (sf_trading/asset_depreciation_hold.py).
* Scheduled -- depreciation is on, but no entry has been posted yet (an Available-for-use Date
  still ahead); Depreciation Starts is the date of its first entry.
"""

import frappe
from frappe import _
from frappe.utils import date_diff, flt, getdate, nowdate

from sf_trading.asset_depreciation_hold import FIELD, HOLD_STATUSES, ON_HOLD

FIELDS = ["name", "asset_name", "asset_category", "company", "location", "purchase_date",
	"available_for_use_date", "purchase_invoice", "purchase_receipt", "gross_purchase_amount", "status",
	"calculate_depreciation"]


def execute(filters=None):
	filters = frappe._dict(filters or {})
	frappe.has_permission("Asset", "read", throw=True)
	rows = get_data(filters)
	return get_columns(), rows, None, None, get_summary(rows, filters)


def get_data(filters):
	conditions = {"docstatus": 1}
	if filters.company:
		conditions["company"] = filters.company
	if filters.asset_category:
		conditions["asset_category"] = filters.asset_category
	if filters.from_date and filters.to_date:
		conditions["purchase_date"] = ["between", [filters.from_date, filters.to_date]]
	elif filters.from_date:
		conditions["purchase_date"] = [">=", filters.from_date]
	elif filters.to_date:
		conditions["purchase_date"] = ["<=", filters.to_date]

	# frappe.get_list, so a user restricted to a company or category sees only theirs
	on_hold = [] if filters.pending == "Scheduled" else frappe.get_list(
		"Asset", filters=dict(conditions, **{FIELD: 1}, calculate_depreciation=0, status=["in", HOLD_STATUSES]),
		fields=FIELDS, order_by="purchase_date")
	# a depreciating Asset reads "Submitted" until its first entry is posted
	scheduled = [] if filters.pending == ON_HOLD else frappe.get_list(
		"Asset", filters=dict(conditions, status="Submitted", calculate_depreciation=1), fields=FIELDS,
		order_by="purchase_date")
	starts = first_depreciation_dates([a.name for a in scheduled])

	today = getdate(nowdate())
	rows = []
	for asset in on_hold + scheduled:
		rows.append({
			"asset": asset.name, "asset_name": asset.asset_name, "asset_category": asset.asset_category,
			"company": asset.company, "location": asset.location,
			"pending": _("Scheduled") if asset.calculate_depreciation else _(ON_HOLD),
			"purchase_date": asset.purchase_date,
			"purchase_doctype": "Purchase Invoice" if asset.purchase_invoice else (
				"Purchase Receipt" if asset.purchase_receipt else None),
			"purchase_document": asset.purchase_invoice or asset.purchase_receipt,
			"gross_purchase_amount": asset.gross_purchase_amount,
			"available_for_use_date": asset.available_for_use_date if asset.calculate_depreciation else None,
			"depreciation_starts": starts.get(asset.name),
			"days_pending": date_diff(today, asset.purchase_date) if asset.purchase_date else None,
		})
	return rows


def first_depreciation_dates(assets) -> dict:
	"""Asset -> the date of the first entry still to be posted on its active schedule."""
	if not assets:
		return {}
	return dict(frappe.db.sql(
		"""select s.asset, min(d.schedule_date) from `tabAsset Depreciation Schedule` s
		join `tabDepreciation Schedule` d on d.parent = s.name
		where s.asset in %s and s.status = 'Active' and s.docstatus = 1 and ifnull(d.journal_entry, '') = ''
		group by s.asset""",
		(assets,),
	))


def get_summary(rows, filters):
	currency = frappe.get_cached_value("Company", filters.company, "default_currency") if filters.company else None
	return [
		{"label": _("On Hold"), "value": sum(1 for r in rows if not r["available_for_use_date"]), "datatype": "Int",
		 "indicator": "Orange"},
		{"label": _("Scheduled"), "value": sum(1 for r in rows if r["available_for_use_date"]), "datatype": "Int",
		 "indicator": "Blue"},
		{"label": _("Value Pending Depreciation"), "value": sum(flt(r["gross_purchase_amount"]) for r in rows),
		 "datatype": "Currency", "currency": currency},
	]


def get_columns():
	return [
		{"label": _("Asset"), "fieldname": "asset", "fieldtype": "Link", "options": "Asset", "width": 150},
		{"label": _("Asset Name"), "fieldname": "asset_name", "fieldtype": "Data", "width": 180},
		{"label": _("Asset Category"), "fieldname": "asset_category", "fieldtype": "Link", "options": "Asset Category", "width": 160},
		{"label": _("Pending"), "fieldname": "pending", "fieldtype": "Data", "width": 90},
		{"label": _("Purchase Date"), "fieldname": "purchase_date", "fieldtype": "Date", "width": 105},
		{"label": _("Purchase Document"), "fieldname": "purchase_document", "fieldtype": "Dynamic Link", "options": "purchase_doctype", "width": 160},
		{"label": _("Gross Purchase Amount"), "fieldname": "gross_purchase_amount", "fieldtype": "Currency", "width": 140},
		{"label": _("Available-for-use Date"), "fieldname": "available_for_use_date", "fieldtype": "Date", "width": 130},
		{"label": _("Depreciation Starts"), "fieldname": "depreciation_starts", "fieldtype": "Date", "width": 125},
		{"label": _("Days Since Purchase"), "fieldname": "days_pending", "fieldtype": "Int", "width": 120},
		{"label": _("Location"), "fieldname": "location", "fieldtype": "Link", "options": "Location", "width": 120},
		{"label": _("Company"), "fieldname": "company", "fieldtype": "Link", "options": "Company", "width": 160},
	]
