# sf_trading/salesperson_by_company.py
"""Restrict the Sales Team's "Sales Person" picker to salespersons valid for the document's Company.

A Salesperson may be scoped to one or more Companies through a new child table,
"Salesperson Company" (`custom_companies` on Sales Person, fixtures/custom_field.json). Sales
Invoice, Sales Order and Delivery Note all carry the salesman in ERPNext's own `sales_team` child
table (doctype "Sales Team"), and every one of them shares the same "Sales Person" Link field --
so the restriction is enforced once here and wired onto all three from
public/js/salesperson_by_company.js via hooks.py doctype_js.

**An empty `custom_companies` table means unrestricted, not "restricted to nothing".** Every
Salesperson already on a site predates this field, so if "no rows" meant "not valid for any
company" the very first save after this ships would empty the picker for the entire sales team on
every invoice. Unrestricted is also the sane default going forward: most Salespersons at a
single-company site, or one who genuinely sells across every branch of a group, should never need
a row at all -- the table exists only for the salesperson who must be kept OFF another company's
documents.

**Why a LEFT JOIN and not `frappe.db.get_list` plus a Python filter loop.** This query runs on
every keystroke the Sales Person picker is open, on a grid a user may open and close many times
while building one invoice. A get_list-then-filter approach reads every Salesperson row on the
site to answer one company's question -- fine at ten salespersons, an N+1-shaped slowdown at the
size a distributor's sales team actually reaches. A single indexed join answers it in one
statement regardless of how many salespersons exist.
"""

import frappe
from frappe.utils import cint


@frappe.whitelist()
@frappe.validate_and_sanitize_search_inputs
def get_salesperson_query(doctype, txt, searchfield, start, page_len, filters):
	"""Link query for the Sales Team's "sales_person" field, filtered by `filters["company"]`.

	Returns a Salesperson when EITHER it has no `custom_companies` rows at all (unrestricted) OR
	one of its rows names the given company. A blank/missing company -- an unsaved document with
	no Company chosen yet -- is treated as "nothing to filter on" and returns every salesperson,
	which is the field's behaviour before this feature existed.

	`doctype` and `searchfield` are accepted for signature compatibility with Frappe's standard
	Link-query contract; the searched column is always Sales Person's own name (its autoname is
	`field:sales_person_name`, so the two are identical).
	"""
	filters = filters or {}
	company = (filters.get("company") or "").strip()
	values = {
		"txt": f"%{txt or ''}%",
		"start": cint(start),
		"page_len": cint(page_len) or 20,
	}

	if not company:
		return frappe.db.sql(
			"""
			select sp.name
			from `tabSales Person` sp
			where sp.name like %(txt)s
			order by sp.name
			limit %(start)s, %(page_len)s
			""",
			values,
			as_list=True,
		)

	values["company"] = company
	return frappe.db.sql(
		"""
		select sp.name
		from `tabSales Person` sp
		left join `tabSalesperson Company` sc
		  on sc.parent = sp.name and sc.parenttype = 'Sales Person'
		where sp.name like %(txt)s
		group by sp.name
		having count(sc.name) = 0 or sum(sc.company = %(company)s) > 0
		order by sp.name
		limit %(start)s, %(page_len)s
		""",
		values,
		as_list=True,
	)
