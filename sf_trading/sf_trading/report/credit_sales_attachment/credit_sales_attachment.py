# Copyright (c) 2026, Enfono Technologies and contributors
# For license information, please see license.txt

"""Credit Sales Attachment: every credit sale (Sales Invoice with Payment Mode "Credit") and
whether anything is attached to it -- the attached files listed with their links, or "No".

Attachment is not mandatory on an invoice; this only shows which credit invoices carry one. "Has
an attachment" means a File attached to the invoice, the same test Pending Supporting Documents
uses (sf_trading/supporting_documents.py).

Invoices are read through frappe.get_list, so the user's User Permissions (company, branch, cost
center, customer...) narrow the rows exactly as they narrow the Sales Invoice list.
"""

from html import unescape

import frappe
from frappe import _
from frappe.utils import escape_html

CHUNK = 1000


def execute(filters=None):
	filters = frappe._dict(filters or {})
	frappe.has_permission("Sales Invoice", "read", throw=True)
	if not filters.company:
		frappe.throw(_("Company is required"))
	if filters.from_date and filters.to_date and filters.from_date > filters.to_date:
		frappe.throw(_("From Date cannot be after To Date"))
	return get_columns(), get_data(filters)


def get_data(filters):
	invoices = frappe.get_list(
		"Sales Invoice",
		filters=get_conditions(filters),
		fields=[
			"name",
			"posting_date",
			"customer",
			"customer_name",
			"branch",
			"cost_center",
			"custom_sales_person as sales_person",
			"base_grand_total as grand_total",
			"outstanding_amount",
		],
		order_by="posting_date asc, name asc",
		limit_page_length=0,
	)
	files = attached_files([inv.name for inv in invoices])

	wanted = filters.get("attachment")
	data = []
	for inv in invoices:
		inv_files = files.get(inv.name, [])
		if wanted == "With Attachment" and not inv_files:
			continue
		if wanted == "Without Attachment" and inv_files:
			continue
		inv.has_attachment = _("Yes") if inv_files else _("No")
		inv.attachments = len(inv_files)
		inv.files = ", ".join(
			'<a href="{0}" target="_blank">{1}</a>'.format(
				# File.file_name is often stored already escaped; unescape first so it is escaped once
				escape_html(f.file_url),
				escape_html(unescape(f.file_name or f.file_url)),
			)
			for f in inv_files
		)
		data.append(inv)
	return data


def get_conditions(filters):
	conditions = {
		"docstatus": 1,
		"is_return": 0,
		"custom_payment_mode": "Credit",
		"company": filters.company,
	}
	if filters.from_date and filters.to_date:
		conditions["posting_date"] = ["between", [filters.from_date, filters.to_date]]
	elif filters.from_date:
		conditions["posting_date"] = [">=", filters.from_date]
	elif filters.to_date:
		conditions["posting_date"] = ["<=", filters.to_date]
	if filters.customer:
		conditions["customer"] = filters.customer
	if filters.branch:
		conditions["branch"] = filters.branch
	# tree doctypes: a parent picks up everything beneath it
	if filters.cost_center:
		conditions["cost_center"] = ["descendants of (inclusive)", filters.cost_center]
	if filters.sales_person:
		conditions["custom_sales_person"] = ["descendants of (inclusive)", filters.sales_person]
	return conditions


def attached_files(names) -> dict:
	"""invoice -> its attached files (name, url), oldest first."""
	out = {}
	for i in range(0, len(names), CHUNK):
		for f in frappe.get_all(
			"File",
			filters={
				"attached_to_doctype": "Sales Invoice",
				"attached_to_name": ["in", names[i : i + CHUNK]],
				"is_folder": 0,
			},
			fields=["attached_to_name", "file_name", "file_url"],
			order_by="creation asc",
		):
			out.setdefault(f.attached_to_name, []).append(f)
	return out


def get_columns():
	return [
		{"label": _("Sales Invoice"), "fieldname": "name", "fieldtype": "Link", "options": "Sales Invoice", "width": 150},
		{"label": _("Posting Date"), "fieldname": "posting_date", "fieldtype": "Date", "width": 105},
		{"label": _("Customer"), "fieldname": "customer", "fieldtype": "Link", "options": "Customer", "width": 150},
		{"label": _("Customer Name"), "fieldname": "customer_name", "fieldtype": "Data", "width": 180},
		{"label": _("Branch"), "fieldname": "branch", "fieldtype": "Link", "options": "Branch", "width": 110},
		{"label": _("Cost Center"), "fieldname": "cost_center", "fieldtype": "Link", "options": "Cost Center", "width": 130},
		{"label": _("Sales Person"), "fieldname": "sales_person", "fieldtype": "Link", "options": "Sales Person", "width": 130},
		{"label": _("Grand Total"), "fieldname": "grand_total", "fieldtype": "Currency", "width": 120},
		{"label": _("Outstanding"), "fieldname": "outstanding_amount", "fieldtype": "Currency", "width": 120},
		{"label": _("Attachment"), "fieldname": "has_attachment", "fieldtype": "Data", "width": 100},
		{"label": _("No. of Files"), "fieldname": "attachments", "fieldtype": "Int", "width": 95},
		{"label": _("Attached Files"), "fieldname": "files", "fieldtype": "HTML", "width": 260},
	]
