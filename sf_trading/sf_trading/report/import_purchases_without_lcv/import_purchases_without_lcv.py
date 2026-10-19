# apps/sf_trading/sf_trading/report/import_purchases_without_lcv/import_purchases_without_lcv.py
"""Import Purchases Without LCV -- the safety net behind the rule that an import carries its Landed Cost Voucher.

An import purchase is a Purchase Invoice that updates stock and is in a foreign currency or carries a Bayan
number (sf_trading/landed_cost_with_invoice.py). The rule stops one reaching approval without a voucher;
this lists the ones the rule never saw or could not hold:

  No Landed Cost Voucher         a submitted import with no live voucher (every import booked before the rule,
                                 or a voucher cancelled on its own), or a draft one that has none yet
  Voucher Draft On Submitted     the invoice is submitted but its voucher is still a draft: it should have been
                                 submitted with it
  Voucher Out Of Step            a draft invoice whose draft voucher no longer matches it

An import marked "Import Without Landed Cost" is left out: that was a decision, with a reason. So is a small
import, below the amount SF Trading Settings > Small Imports sets for its company: it needs no voucher.
"""

import frappe
from frappe import _
from frappe.utils import cint

from sf_trading import landed_cost_with_invoice as lcv

BATCH = 500

GAP_NONE = "No Landed Cost Voucher"
GAP_DRAFT = "Voucher Draft On Submitted"
GAP_STALE = "Voucher Out Of Step"


def execute(filters=None):
	filters = frappe._dict(filters or {})
	return get_columns(), get_data(filters)


def get_columns():
	return [
		{"label": _("Invoice"), "fieldname": "invoice", "fieldtype": "Link", "options": "Purchase Invoice", "width": 150},
		{"label": _("Posting Date"), "fieldname": "posting_date", "fieldtype": "Date", "width": 100},
		{"label": _("Supplier"), "fieldname": "supplier", "fieldtype": "Link", "options": "Supplier", "width": 180},
		{"label": _("Currency"), "fieldname": "currency", "fieldtype": "Link", "options": "Currency", "width": 70},
		{"label": _("Bayan Value"), "fieldname": "bayan", "fieldtype": "Data", "width": 140},
		{"label": _("Stage"), "fieldname": "stage", "fieldtype": "Data", "width": 150},
		{"label": _("Voucher"), "fieldname": "voucher", "fieldtype": "Data", "width": 190},
		{"label": _("Gap"), "fieldname": "gap", "fieldtype": "Data", "width": 190},
		{"label": _("Detail"), "fieldname": "detail", "fieldtype": "Data", "width": 360},
		{"label": _("Company"), "fieldname": "company", "fieldtype": "Link", "options": "Company", "width": 160},
	]


def _chunks(values, size=BATCH):
	values = list(values)
	for i in range(0, len(values), size):
		yield values[i : i + size]


def _vouchers_by_invoice(names) -> dict:
	"""{invoice: [(voucher, docstatus)]} for the live vouchers naming those invoices, fetched in slices so a
	long list never trips the query parser's token cap."""
	out = {n: [] for n in names}
	for chunk in _chunks(names):
		rows = frappe.get_all(
			lcv.VOUCHER_RECEIPT,
			filters={"parenttype": lcv.VOUCHER, "receipt_document_type": lcv.INVOICE, "receipt_document": ["in", chunk]},
			fields=["parent", "receipt_document"],
		)
		if not rows:
			continue
		live = {
			v.name: v.docstatus
			for v in frappe.get_all(lcv.VOUCHER, filters={"name": ["in", [r.parent for r in rows]], "docstatus": ["<", 2]}, fields=["name", "docstatus"])
		}
		for r in rows:
			if r.parent in live:
				out[r.receipt_document].append((r.parent, live[r.parent]))
	return out


def get_data(filters):
	conditions = {"update_stock": 1, "is_return": 0, "docstatus": ["<", 2]}
	if filters.get("company"):
		conditions["company"] = filters.company
	if filters.get("from_date") and filters.get("to_date"):
		conditions["posting_date"] = ["between", [filters.from_date, filters.to_date]]
	elif filters.get("from_date"):
		conditions["posting_date"] = [">=", filters.from_date]
	elif filters.get("to_date"):
		conditions["posting_date"] = ["<=", filters.to_date]
	invoices = frappe.get_all(
		lcv.INVOICE,
		filters=conditions,
		fields=["name", "company", "posting_date", "supplier", "currency", "docstatus", "workflow_state",
			"custom_bayan_value", "base_grand_total", lcv.ESCAPE_FIELD],
		order_by="posting_date desc, name desc",
	)
	imports = []
	for i in invoices:
		as_doc = frappe._dict(i, doctype=lcv.INVOICE, update_stock=1, is_return=0)
		# an import marked as carrying no landed cost, or one below its company's small-import amount, is no gap
		if lcv.is_import(as_doc) and not cint(i.get(lcv.ESCAPE_FIELD)) and not lcv.is_small_import(as_doc):
			imports.append(i)
	vouchers = _vouchers_by_invoice([i.name for i in imports])
	rows = []
	for inv in imports:
		live = vouchers.get(inv.name, [])
		gap = detail = None
		if not live:
			gap = GAP_NONE
			detail = _("Not raised yet: Create > Landed Cost Voucher on the invoice.") if inv.docstatus == 0 else _(
				"Submitted without one: its stock is valued without freight and customs."
			)
		elif inv.docstatus == 1 and any(ds == 0 for _name, ds in live):
			gap = GAP_DRAFT
			detail = _("Submitted with the invoice normally; open it and submit it, or find out why it was not.")
		elif inv.docstatus == 0:
			full = frappe.get_doc(lcv.INVOICE, inv.name)
			problems = []
			for name, ds in live:
				if ds == 0:
					diff = lcv.differences(frappe.get_doc(lcv.VOUCHER, name), full)
					if diff:
						problems.append("%s: %s" % (name, "; ".join(diff)))
			if problems:
				gap = GAP_STALE
				detail = " | ".join(problems)
		if not gap:
			continue
		if filters.get("gap") and filters.gap != gap:
			continue
		rows.append(
			{
				"invoice": inv.name,
				"posting_date": inv.posting_date,
				"supplier": inv.supplier,
				"currency": inv.currency,
				"bayan": inv.custom_bayan_value,
				"stage": _("Draft") if inv.docstatus == 0 else _("Submitted"),
				"voucher": ", ".join(n for n, _ds in live),
				"gap": gap,
				"detail": detail,
				"company": inv.company,
			}
		)
	return rows
