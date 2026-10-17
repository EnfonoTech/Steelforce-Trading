# sf_trading/patches/v0_1/name_payment_terms_on_term_less_schedules.py
"""Name the Payment Term on schedule rows that have none, under a term-allocated Payment Terms Template.

A Purchase Invoice drafted before its supplier had a default Payment Terms Template, then saved again after the default arrived,
got the template (CASH, which allocates by payment terms) set by core without its schedule being rebuilt: a one-row schedule with
no term. Core then refuses every Payment Entry against it ("... has Payment Term based allocation enabled. Select a Payment Term
for Row #n") and no term exists to pick. On sft prod, 2026-10-17: Purchase Invoices 20020000371, 20020000378, 20020000379.

For each such document the schedule rows are given the template's terms, in order, when the row count and the portions agree with the
template; anything that does not line up is left alone and logged. Only rows with no term are written, so a second run changes nothing.
The rows are written directly (db.set_value, `modified` kept): the documents are submitted, and only the label of the instalment changes.
"""

import frappe
from frappe.utils import flt

DOCTYPES = ("Purchase Invoice", "Sales Invoice", "Purchase Order", "Sales Order")


def execute():
	log = frappe.logger("sf_trading")
	named = 0
	for template in frappe.get_all("Payment Terms Template", filters={"allocate_payment_based_on_payment_terms": 1}, pluck="name"):
		terms = frappe.get_all(
			"Payment Terms Template Detail",
			filters={"parent": template},
			fields=["payment_term", "invoice_portion"],
			order_by="idx asc",
		)
		if not terms:
			continue
		for doctype in DOCTYPES:
			for name in frappe.get_all(doctype, filters={"payment_terms_template": template, "docstatus": ["<", 2]}, pluck="name"):
				rows = frappe.get_all(
					"Payment Schedule",
					filters={"parent": name, "parenttype": doctype},
					fields=["name", "payment_term", "invoice_portion"],
					order_by="idx asc",
				)
				if not rows or all(r.payment_term for r in rows):
					continue
				if len(rows) != len(terms) or any(abs(flt(r.invoice_portion) - flt(t.invoice_portion)) > 0.01 for r, t in zip(rows, terms)):
					log.warning("name_payment_terms: %s %s has a schedule that does not line up with template %s; left alone", doctype, name, template)
					continue
				for row, term in zip(rows, terms):
					if not row.payment_term:
						frappe.db.set_value("Payment Schedule", row.name, "payment_term", term.payment_term, update_modified=False)
						named += 1
	log.info("name_payment_terms: named %s schedule rows", named)
	frappe.db.commit()
