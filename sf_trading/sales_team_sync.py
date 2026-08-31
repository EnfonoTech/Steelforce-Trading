# sf_trading/sales_team_sync.py
"""Mirror `custom_sales_person` into ERPNext's own Sales Team table, on the server.

This site records the salesman in a custom header field. ERPNext records him in the `sales_team`
child table, and EVERY native sales-person feature reads that table and nothing else: Sales
Person-wise Transaction Summary, Gross Profit grouped by Sales Person, Sales Analytics by sales
person, commission, the Sales Person tree's own targets.

A mirror already existed -- in `public/js/sales_invoice.js`, on the field's change event. It fires
only when a human edits that field on a form, so on production, of 3,204 submitted August 2026
invoices, 3,204 carried `custom_sales_person` and **162** carried a Sales Team row. Every core
report was therefore reading 5% of the sales and showing one salesman 3,973.116 where he had sold
52,828.415. Nothing was wrong with the money -- only with who the reports could see.

The rule here is deliberately timid: fill the table only when it is EMPTY. A row put there by a
human, or injected by ERPNext from the customer master, is somebody's decision and is left alone
(on production 2 of the 162 name a different person from the header field -- those 2 stay).
"""

import frappe
from frappe import _
from frappe.query_builder.functions import Coalesce
from frappe.utils import cint, flt

# the selling documents that carry the custom field; one without it simply returns
MIRRORED = ("Sales Invoice", "Sales Order", "Delivery Note", "POS Invoice")


def usable_sales_person(person: str | None) -> bool:
	"""Whether a Sales Person may legally sit in a Sales Team row.

	Server-side erpnext enforces only `enabled` -- it throws "Sales Person X is disabled" on save.
	It does NOT stop a GROUP node going in, but a group there double-counts against its own
	children in every report that walks the tree, so both are refused here.
	"""
	if not person:
		return False
	row = frappe.get_cached_value("Sales Person", person, ["enabled", "is_group"], as_dict=True)
	return bool(row and cint(row.enabled) and not cint(row.is_group))


def set_sales_team(doc, method=None):
	"""before_validate: put the header's salesman into the Sales Team table when it is empty.

	`before_validate` and not `validate`: erpnext computes `allocated_amount` inside
	calculate_taxes_and_totals during validate, so a row appended afterwards would carry a zero
	amount until the next save.
	"""
	person = (doc.get("custom_sales_person") or "").strip()
	if not person:
		return
	if doc.get("sales_team"):
		# already decided -- by a person, or by the customer master through get_party_details
		return
	if not usable_sales_person(person):
		return

	doc.append("sales_team", {"sales_person": person, "allocated_percentage": 100})


def _pending(doctype: str, company: str | None, from_date: str | None, to_date: str | None,
             limit: int) -> list:
	"""Submitted documents that name a salesman on the header and carry no Sales Team row."""
	doc = frappe.qb.DocType(doctype)
	team = frappe.qb.DocType("Sales Team")

	eligible = (doc.amount_eligible_for_commission
	            if frappe.db.has_column(doctype, "amount_eligible_for_commission")
	            else doc.base_net_total)
	date_field = (doc.posting_date if frappe.db.has_column(doctype, "posting_date")
	              else doc.transaction_date)

	query = (
		frappe.qb.from_(doc)
		.left_join(team).on((team.parent == doc.name) & (team.parenttype == doctype))
		.select(doc.name, doc.custom_sales_person.as_("person"), eligible.as_("eligible"))
		.where(doc.docstatus == 1)
		.where(Coalesce(doc.custom_sales_person, "") != "")
		.where(team.name.isnull())
		.orderby(doc.name)
		.limit(limit)
	)
	if company:
		query = query.where(doc.company == company)
	if from_date:
		query = query.where(date_field >= from_date)
	if to_date:
		query = query.where(date_field <= to_date)
	return query.run(as_dict=True)


@frappe.whitelist()
def backfill(doctype: str = "Sales Invoice", company: str | None = None,
             from_date: str | None = None, to_date: str | None = None,
             limit: int = 2000, dry_run: int = 1) -> dict:
	"""Give already-submitted history the Sales Team row it never got.

	The child row is inserted on its own rather than through `parent.save()`. Saving a submitted
	Sales Invoice takes the `update_after_submit` path, which fires every `on_update_after_submit`
	hook on the document -- on this site that reaches ksa_compliance's ZATCA handling. Reporting
	only needs the row to exist in `tabSales Team`, so the parent is left completely untouched:
	no hooks, no `modified` bump, no version rows, no risk to an e-invoice already filed.

	Because nothing recalculates on this path, `allocated_amount` is written here from the
	document's own `amount_eligible_for_commission` -- exactly what erpnext's
	calculate_contribution would have multiplied by 100%.
	"""
	dry_run = cint(dry_run)
	if doctype not in MIRRORED:
		frappe.throw(_("{0} does not carry a sales team").format(doctype))
	if not frappe.db.has_column(doctype, "custom_sales_person"):
		frappe.throw(_("{0} has no custom_sales_person field on this site").format(doctype))
	frappe.has_permission(doctype, "write", throw=True)

	rows = _pending(doctype, company, from_date, to_date, min(cint(limit) or 2000, 20000))

	done, skipped = [], []
	for r in rows:
		if not usable_sales_person(r.person):
			# a disabled or group salesman would be unfixable through the UI afterwards
			skipped.append({"name": r.name, "person": r.person, "reason": "not an enabled leaf"})
			continue
		if dry_run:
			done.append(r.name)
			continue
		try:
			frappe.db.savepoint("sf_sales_team_row")
			row = frappe.new_doc("Sales Team")
			row.parent = r.name
			row.parenttype = doctype
			row.parentfield = "sales_team"
			row.idx = 1
			row.sales_person = r.person
			row.allocated_percentage = 100
			row.allocated_amount = flt(r.eligible)
			row.insert(ignore_permissions=True)
			done.append(r.name)
		except Exception as e:
			frappe.db.rollback(save_point="sf_sales_team_row")
			skipped.append({"name": r.name, "person": r.person, "reason": str(e)[:140]})

	if not dry_run:
		frappe.db.commit()
	return {
		"doctype": doctype,
		"dry_run": bool(dry_run),
		"matched": len(rows),
		"written": 0 if dry_run else len(done),
		"would_write": len(done) if dry_run else 0,
		"skipped_count": len(skipped),
		"skipped": skipped[:50],
	}
