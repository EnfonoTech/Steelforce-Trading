# Copyright (c) 2026, Enfono Technologies and contributors
# For license information, please see license.txt

"""What each supplier still lacks for the supplier completeness rules (sf_trading.supplier_validation).

The rules apply to existing suppliers as much as new ones and carry no bypass, so this is the work
list to clear before "Enforce Supplier Completeness" is ticked on SF Trading Settings: Tax ID (a
Company supplier), phone, email and an attached file, read exactly as the rule reads them.
"""

import frappe
from frappe import _
from frappe.utils import cstr

from sf_trading.party_contact_cache import party_email_addresses, party_phone_numbers
from sf_trading.supplier_validation import has_attachment, needs_tax_id


def execute(filters=None):
	filters = frappe._dict(filters or {})
	frappe.has_permission("Supplier", "read", throw=True)

	invoices = dict(
		frappe.db.sql(
			"""
			select supplier, count(*) from `tabPurchase Invoice`
			where docstatus = 1 and posting_date >= date_sub(curdate(), interval 90 day)
			group by supplier
			""",
		)
	)

	supplier_filters = {} if filters.include_disabled else {"disabled": 0}
	rows = []
	for supplier in frappe.get_all(
		"Supplier",
		filters=supplier_filters,
		fields=["name", "supplier_name", "supplier_type", "tax_id", "mobile_no", "email_id", "disabled"],
		order_by="supplier_name",
	):
		no_tax_id = needs_tax_id(supplier.supplier_type) and not cstr(supplier.tax_id).strip()
		no_phone = not cstr(supplier.mobile_no).strip() and not party_phone_numbers("Supplier", supplier.name)
		no_email = not cstr(supplier.email_id).strip() and not party_email_addresses("Supplier", supplier.name)
		no_document = not has_attachment(supplier.name)
		gaps = [
			label
			for label, missing in (
				(_("Tax ID"), no_tax_id),
				(_("Phone"), no_phone),
				(_("Email"), no_email),
				(_("Document"), no_document),
			)
			if missing
		]
		if filters.only_incomplete and not gaps:
			continue
		rows.append(
			{
				"supplier": supplier.name,
				"supplier_name": supplier.supplier_name,
				"supplier_type": supplier.supplier_type,
				"disabled": supplier.disabled,
				"missing_tax_id": int(no_tax_id),
				"missing_phone": int(no_phone),
				"missing_email": int(no_email),
				"missing_document": int(no_document),
				"gaps": ", ".join(gaps),
				"invoices_90d": invoices.get(supplier.name, 0),
			}
		)

	rows.sort(key=lambda r: (-r["invoices_90d"], r["supplier_name"] or ""))
	return get_columns(), rows, None, None, get_summary(rows)


def get_columns():
	return [
		{"label": _("Supplier"), "fieldname": "supplier", "fieldtype": "Link", "options": "Supplier", "width": 160},
		{"label": _("Supplier Name"), "fieldname": "supplier_name", "fieldtype": "Data", "width": 220},
		{"label": _("Type"), "fieldname": "supplier_type", "fieldtype": "Data", "width": 90},
		{"label": _("Invoices (90 days)"), "fieldname": "invoices_90d", "fieldtype": "Int", "width": 120},
		{"label": _("Missing"), "fieldname": "gaps", "fieldtype": "Data", "width": 230},
		{"label": _("No Tax ID"), "fieldname": "missing_tax_id", "fieldtype": "Check", "width": 80},
		{"label": _("No Phone"), "fieldname": "missing_phone", "fieldtype": "Check", "width": 80},
		{"label": _("No Email"), "fieldname": "missing_email", "fieldtype": "Check", "width": 80},
		{"label": _("No Document"), "fieldname": "missing_document", "fieldtype": "Check", "width": 100},
		{"label": _("Disabled"), "fieldname": "disabled", "fieldtype": "Check", "width": 80},
	]


def get_summary(rows):
	def count(key):
		return sum(r[key] for r in rows)

	return [
		{"label": _("Suppliers Listed"), "value": len(rows), "datatype": "Int", "indicator": "Blue"},
		{"label": _("No Tax ID"), "value": count("missing_tax_id"), "datatype": "Int", "indicator": "Red"},
		{"label": _("No Phone"), "value": count("missing_phone"), "datatype": "Int", "indicator": "Red"},
		{"label": _("No Email"), "value": count("missing_email"), "datatype": "Int", "indicator": "Red"},
		{"label": _("No Document"), "value": count("missing_document"), "datatype": "Int", "indicator": "Red"},
		{
			"label": _("Invoiced in last 90 days"),
			"value": sum(1 for r in rows if r["invoices_90d"]),
			"datatype": "Int",
			"indicator": "Orange",
		},
	]
