# Copyright (c) 2026, Enfono Technologies and contributors
# For license information, please see license.txt

"""Purchase Register extended with the receipts nobody has billed yet.

Core's Purchase Register lists Purchase Invoices and nothing else, so the
purchases figure it produces stops at what has been billed. Goods that arrived
and have not been invoiced are real purchases for the trading account, and they
are missing from it.

This report adds them, and adds ONLY the part that is missing. For every
Purchase Receipt in the period it prints the value no Purchase Invoice has
billed, worked out from the same row links the open item reports use
(`sf_trading.open_items`):

    fully billed receipt   -> does not appear at all
    partly billed receipt  -> appears for its remainder only
    unbilled receipt       -> appears in full

The invoice always carries the billed part on its own line, so no value is
counted twice and no value is dropped. A Purchase Invoice that updates stock
needs no receipt and appears once, as an invoice.

WHY THE UNBILLED PART IS MEASURED AS OF THE PERIOD END, NOT TODAY
-----------------------------------------------------------------
`received_items_pending_billing` reconstructs what has been billed from the
counterpart rows filtered by their own posting date, so passing the period end
as `as_on` answers "what was still unbilled on that date". A January receipt
invoiced in February therefore counts as an unbilled January purchase, and the
February invoice counts in February — the value lands in exactly one period and
re-running January next year gives the same answer. Measuring against today
instead would quietly move that value out of January the moment the invoice was
raised, and January would under-report for good.

SCOPE, INHERITED DELIBERATELY FROM THE OPEN ITEM ENGINE
-------------------------------------------------------
Receipt rows are submitted, non-return, stock-item rows whose receipt is not
Closed or Completed, excluding rows raised from an invoice (the bill-first
flow, where the invoice already carries the value). Keeping the same scope
means this report and `Received Items Pending Billing` always agree; a figure
that disagreed with the operational report would be worse than a slightly
narrower one.

Landed Cost Voucher charges are shown per document wherever any were applied.
They are the charges recorded on the document as a whole, not a share of the
unbilled remainder, and they sit in their own column rather than inside the
purchase value, because a Landed Cost Voucher posts to stock valuation and not
to the supplier bill.
"""

import frappe
from frappe import _
from frappe.query_builder import Criterion
from frappe.query_builder.custom import ConstantColumn
from frappe.utils import cint, flt, getdate

from sf_trading.open_items import received_items_pending_billing

INVOICED = "Invoiced"
INVOICED_WITH_STOCK = "Invoiced (Updates Stock)"
DEBIT_NOTE = "Debit Note"
UNBILLED_RECEIPT = "Unbilled Receipt"


def execute(filters=None):
	filters = frappe._dict(filters or {})
	validate_filters(filters)

	rows = get_invoice_rows(filters)
	if cint(filters.get("include_unbilled_receipts", 1)):
		rows += get_receipt_rows(filters)

	attach_landed_cost(rows)
	rows.sort(key=lambda row: (getdate(row["posting_date"]), row["voucher_no"]))

	return get_columns(), rows


def validate_filters(filters):
	if not filters.get("company"):
		frappe.throw(_("Company is required"))
	if not (filters.get("from_date") and filters.get("to_date")):
		frappe.throw(_("From Date and To Date are required"))
	if getdate(filters.from_date) > getdate(filters.to_date):
		frappe.throw(_("From Date cannot be after To Date"))


# ---------------------------------------------------------------------------
# the billed side — Purchase Invoices, as core lists them
# ---------------------------------------------------------------------------


def get_invoice_rows(filters):
	invoice = frappe.qb.DocType("Purchase Invoice")

	query = (
		frappe.qb.from_(invoice)
		.select(
			ConstantColumn("Purchase Invoice").as_("voucher_type"),
			invoice.name.as_("voucher_no"),
			invoice.posting_date,
			invoice.company,
			invoice.supplier,
			invoice.supplier_name,
			invoice.supplier_group,
			invoice.bill_no,
			invoice.bill_date,
			invoice.is_return,
			invoice.update_stock,
			invoice.base_net_total,
			invoice.base_total_taxes_and_charges,
			invoice.base_grand_total,
		)
		.where(
			(invoice.docstatus == 1)
			& (invoice.company == filters.company)
			& (invoice.posting_date >= getdate(filters.from_date))
			& (invoice.posting_date <= getdate(filters.to_date))
		)
	)

	if filters.get("supplier"):
		query = query.where(invoice.supplier == filters.supplier)
	if filters.get("supplier_group"):
		query = query.where(invoice.supplier_group == filters.supplier_group)
	if filters.get("cost_center"):
		query = query.where(
			cost_center_exists("Purchase Invoice Item", invoice.name, filters.cost_center)
		)

	rows = []
	for record in query.run(as_dict=True):
		if cint(record.is_return):
			status = DEBIT_NOTE
		elif cint(record.update_stock):
			status = INVOICED_WITH_STOCK
		else:
			status = INVOICED

		rows.append(
			{
				"voucher_type": record.voucher_type,
				"voucher_no": record.voucher_no,
				"posting_date": record.posting_date,
				"company": record.company,
				"status": status,
				"supplier": record.supplier,
				"supplier_name": record.supplier_name,
				"supplier_group": record.supplier_group,
				"bill_no": record.bill_no,
				"bill_date": record.bill_date,
				"pending_qty": 0.0,
				"net_amount": flt(record.base_net_total),
				"tax_amount": flt(record.base_total_taxes_and_charges),
				"total_amount": flt(record.base_grand_total),
			}
		)
	return rows


def cost_center_exists(child_doctype, parent_field, cost_center):
	child = frappe.qb.DocType(child_doctype)
	return Criterion.exists(
		frappe.qb.from_(child)
		.select(child.name)
		.where((child.parent == parent_field) & (child.cost_center == cost_center))
	)


# ---------------------------------------------------------------------------
# the unbilled side — what the receipts are still carrying
# ---------------------------------------------------------------------------


def get_receipt_rows(filters):
	"""One line per receipt, valued at the part no invoice has billed.

	`as_on` is the period end so the answer is what was outstanding then, and
	the receipt has to have been raised inside the period to belong to it.
	"""
	open_filters = frappe._dict(
		{
			"company": filters.company,
			"as_on": getdate(filters.to_date),
			"party": filters.get("supplier"),
			"cost_center": filters.get("cost_center"),
		}
	)

	from_date = getdate(filters.from_date)
	to_date = getdate(filters.to_date)

	per_receipt = {}
	for row in received_items_pending_billing(open_filters):
		posting_date = getdate(row.posting_date)
		if posting_date < from_date or posting_date > to_date:
			continue

		receipt = per_receipt.get(row.document)
		if receipt is None:
			receipt = {
				"voucher_type": "Purchase Receipt",
				"voucher_no": row.document,
				"posting_date": posting_date,
				"company": row.company,
				"status": UNBILLED_RECEIPT,
				"supplier": row.party,
				"supplier_name": row.party_name,
				"supplier_group": None,
				"bill_no": None,
				"bill_date": None,
				"pending_qty": 0.0,
				"net_amount": 0.0,
				# a receipt carries no supplier tax; the tax arrives with the bill
				"tax_amount": 0.0,
				"total_amount": 0.0,
			}
			per_receipt[row.document] = receipt

		receipt["pending_qty"] += flt(row.pending_qty)
		receipt["net_amount"] += flt(row.pending_amount)
		receipt["total_amount"] += flt(row.pending_amount)

	rows = list(per_receipt.values())
	set_supplier_groups(rows)

	if filters.get("supplier_group"):
		rows = [row for row in rows if row["supplier_group"] == filters.supplier_group]

	for row in rows:
		row["pending_qty"] = flt(row["pending_qty"], 3)
		row["net_amount"] = flt(row["net_amount"], 2)
		row["total_amount"] = flt(row["total_amount"], 2)

	return rows


def set_supplier_groups(rows):
	"""Purchase Receipt has no supplier_group of its own — read it off the party."""
	suppliers = {row["supplier"] for row in rows if row["supplier"]}
	if not suppliers:
		return

	groups = {
		record.name: record.supplier_group
		for record in frappe.get_all(
			"Supplier", filters={"name": ("in", list(suppliers))}, fields=["name", "supplier_group"]
		)
	}
	for row in rows:
		row["supplier_group"] = groups.get(row["supplier"])


# ---------------------------------------------------------------------------
# landed cost
# ---------------------------------------------------------------------------


def attach_landed_cost(rows):
	"""Charges a Landed Cost Voucher applied to each document on the report."""
	for row in rows:
		row["landed_cost_voucher"] = ""
		row["landed_cost_amount"] = 0.0
		row["total_with_landed_cost"] = flt(row["total_amount"])

	names = [row["voucher_no"] for row in rows]
	if not names:
		return

	charge = frappe.qb.DocType("Landed Cost Item")
	voucher = frappe.qb.DocType("Landed Cost Voucher")
	records = (
		frappe.qb.from_(charge)
		.join(voucher)
		.on(voucher.name == charge.parent)
		.select(
			charge.parent.as_("voucher"),
			charge.receipt_document_type.as_("voucher_type"),
			charge.receipt_document.as_("voucher_no"),
			charge.applicable_charges,
		)
		.where((voucher.docstatus == 1) & (charge.receipt_document.isin(names)))
	).run(as_dict=True)

	applied = {}
	for record in records:
		key = (record.voucher_type, record.voucher_no)
		entry = applied.setdefault(key, {"vouchers": [], "charges": 0.0})
		entry["charges"] += flt(record.applicable_charges)
		if record.voucher not in entry["vouchers"]:
			entry["vouchers"].append(record.voucher)

	for row in rows:
		entry = applied.get((row["voucher_type"], row["voucher_no"]))
		if not entry:
			continue
		row["landed_cost_voucher"] = ", ".join(sorted(entry["vouchers"]))
		row["landed_cost_amount"] = flt(entry["charges"], 2)
		row["total_with_landed_cost"] = flt(row["total_amount"] + entry["charges"], 2)


# ---------------------------------------------------------------------------
# columns
# ---------------------------------------------------------------------------


def currency_column(label, fieldname, width=130):
	return {
		"label": label,
		"fieldname": fieldname,
		"fieldtype": "Currency",
		"options": "Company:company:default_currency",
		"width": width,
	}


def get_columns():
	return [
		{"label": _("Type"), "fieldname": "voucher_type", "fieldtype": "Data", "width": 130},
		{
			"label": _("Document"),
			"fieldname": "voucher_no",
			"fieldtype": "Dynamic Link",
			"options": "voucher_type",
			"width": 175,
		},
		{"label": _("Date"), "fieldname": "posting_date", "fieldtype": "Date", "width": 95},
		{"label": _("Status"), "fieldname": "status", "fieldtype": "Data", "width": 150},
		{
			"label": _("Supplier"),
			"fieldname": "supplier",
			"fieldtype": "Link",
			"options": "Supplier",
			"width": 150,
		},
		{"label": _("Supplier Name"), "fieldname": "supplier_name", "fieldtype": "Data", "width": 170},
		{
			"label": _("Supplier Group"),
			"fieldname": "supplier_group",
			"fieldtype": "Link",
			"options": "Supplier Group",
			"width": 120,
		},
		{"label": _("Bill No"), "fieldname": "bill_no", "fieldtype": "Data", "width": 110},
		{"label": _("Bill Date"), "fieldname": "bill_date", "fieldtype": "Date", "width": 95},
		{"label": _("Unbilled Qty"), "fieldname": "pending_qty", "fieldtype": "Float", "width": 100},
		currency_column(_("Net Amount"), "net_amount"),
		currency_column(_("Tax Amount"), "tax_amount", 110),
		currency_column(_("Total"), "total_amount"),
		{
			"label": _("Landed Cost Voucher"),
			"fieldname": "landed_cost_voucher",
			"fieldtype": "Data",
			"width": 170,
		},
		currency_column(_("Landed Cost"), "landed_cost_amount", 120),
		currency_column(_("Total with Landed Cost"), "total_with_landed_cost", 160),
		{
			"label": _("Company"),
			"fieldname": "company",
			"fieldtype": "Link",
			"options": "Company",
			"width": 130,
			"hidden": 1,
		},
	]
