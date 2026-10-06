# Copyright (c) 2026, Enfono Technologies and contributors
# For license information, please see license.txt

"""Invoice-wise and customer-wise breakdown of ERPNext's "Customer Acquisition and Loyalty".

The standard report counts, per month or territory, the first-ever submitted Sales
Invoice of each customer (for the company) as a "new customer" and every later
invoice as a "repeat customer", with revenue split the same way. This report
applies exactly that rule but lists the invoices themselves (or one row per
customer), so the total row of a month/territory drilled from the standard report
equals that cell there.
"""

import frappe
from frappe import _
from frappe.query_builder.functions import Min
from frappe.utils import flt, getdate


def execute(filters=None):
	filters = frappe._dict(filters or {})
	# the rows are Sales Invoices, so the right to read them gates the report as well as the right
	# to open it (the report's own roles come from the standard report it drills from)
	frappe.has_permission("Sales Invoice", "read", throw=True)
	if not filters.company:
		frappe.throw(_("Company is required"))
	filters.from_date = getdate(filters.from_date)
	filters.to_date = getdate(filters.to_date)
	if filters.from_date > filters.to_date:
		frappe.throw(_("From Date cannot be after To Date"))

	currency = frappe.get_cached_value("Company", filters.company, "default_currency")
	invoices = get_invoices(filters, currency)
	customers = aggregate(invoices, currency)

	if filters.view == "Customer Wise":
		rows = [
			row
			for row in customers.values()
			if not (filters.customer_type == "New" and not row.new_customers)
			and not (filters.customer_type == "Repeat" and not row.repeat_customers)
		]
		rows.sort(key=lambda r: r.total_revenue, reverse=True)
		return get_customer_columns(), rows, None, get_chart(rows), get_summary(rows, currency)

	if filters.customer_type:
		invoices = [inv for inv in invoices if inv.is_new == (filters.customer_type == "New")]
	# the cards and chart describe the invoices listed, but New/Existing stays the customer's
	shown = aggregate(invoices, currency)
	for name, row in shown.items():
		row.status = customers[name].status
	shown = sorted(shown.values(), key=lambda r: r.total_revenue, reverse=True)
	return get_invoice_columns(), invoices, None, get_chart(shown), get_summary(shown, currency)


def get_invoices(filters, currency):
	"""Submitted invoices in the window, oldest first, each marked new or repeat."""
	si = frappe.qb.DocType("Sales Invoice")
	customer = frappe.qb.DocType("Customer")

	def base(query):
		query = query.where((si.docstatus == 1) & (si.company == filters.company))
		if filters.customer:
			query = query.where(si.customer == filters.customer)
		if filters.customer_group:
			query = query.where(customer.customer_group.isin(get_descendants("Customer Group", filters.customer_group)))
		return query

	# Customers already invoiced before the window: none of their invoices in it are "new".
	prior = dict(
		base(
			frappe.qb.from_(si)
			.inner_join(customer)
			.on(customer.name == si.customer)
			.select(si.customer, Min(si.posting_date))
			.where(si.posting_date < filters.from_date)
			.groupby(si.customer)
		).run()
	)

	invoices = base(
		frappe.qb.from_(si)
		.inner_join(customer)
		.on(customer.name == si.customer)
		.select(
			si.name,
			si.customer,
			customer.customer_name,
			customer.customer_group,
			customer.territory.as_("customer_territory"),
			si.territory,
			si.cost_center,
			si.branch,
			si.custom_sales_person,
			si.posting_date,
			si.base_grand_total,
		)
		.where(si.posting_date[filters.from_date : filters.to_date])
		.orderby(si.posting_date)
		.orderby(si.posting_time)
		.orderby(si.creation)
	).run(as_dict=True)

	# Territory, cost center, branch and sales person are properties of the invoice, so they
	# are applied after deciding new vs repeat: a customer first invoiced by another branch or
	# salesman is a repeat customer here too, as the standard report counts it company-wide.
	invoice_filters = {}
	for fieldname, doctype, tree in (
		("territory", "Territory", True),
		("cost_center", "Cost Center", True),
		("branch", "Branch", False),
		("custom_sales_person", "Sales Person", True),
	):
		value = filters.get("sales_person" if fieldname == "custom_sales_person" else fieldname)
		if value:
			invoice_filters[fieldname] = set(get_descendants(doctype, value) if tree else [value])
	permitted = get_permitted_scope()
	needs_item_values = {"cost_center", "branch"} & set(invoice_filters) or {"Cost Center", "Branch"} & set(permitted)
	item_values = get_item_dimensions(filters) if needs_item_values else {}

	def matches(inv):
		for fieldname, allowed in invoice_filters.items():
			# cost center and branch often sit on the item rows only, so a hit there counts
			values = {inv[fieldname]} | item_values.get((inv.name, fieldname), set())
			if not values & allowed:
				return False
		return True

	first_seen = dict(prior)
	out = []
	for inv in invoices:
		# decided company-wide, before any filter or permission narrows what is shown
		inv.is_new = inv.customer not in first_seen
		first_seen.setdefault(inv.customer, inv.posting_date)
		if not matches(inv) or not is_permitted(inv, permitted, item_values):
			continue
		inv.first_invoice_date = first_seen[inv.customer]
		inv.type = _("First Invoice") if inv.is_new else _("Repeat Invoice")
		# a customer whose first invoice falls in the window is new for the whole window, so
		# their second January invoice is a repeat invoice of a NEW customer
		inv.is_new_customer = inv.first_invoice_date >= filters.from_date
		inv.customer_status = _("New Customer") if inv.is_new_customer else _("Existing Customer")
		inv.new_customer_revenue = flt(inv.base_grand_total) if inv.is_new else 0.0
		inv.repeat_customer_revenue = 0.0 if inv.is_new else flt(inv.base_grand_total)
		inv.currency = currency
		out.append(inv)
	return out


# Which User Permission narrows which column of the invoice.
PERMISSION_COLUMNS = (("Branch", "branch"), ("Cost Center", "cost_center"), ("Customer", "customer"))


def get_permitted_scope(user=None):
	"""doctype -> the values the user may see, for the doctypes they are restricted on.

	`frappe.qb` applies no permission filtering, so without this a user restricted to one branch
	or cost center would see every branch's invoices here while the desk list is restricted.
	A user with no User Permissions (Administrator, most head-office logins) gets an empty scope
	and is unaffected.
	"""
	from frappe.permissions import get_user_permissions

	permissions = get_user_permissions(user or frappe.session.user) or {}
	scope = {}
	for doctype, _fieldname in PERMISSION_COLUMNS:
		allowed = {row.get("doc") for row in (permissions.get(doctype) or []) if row.get("doc")}
		if allowed:
			scope[doctype] = allowed
	return scope


def is_permitted(inv, scope, item_values):
	"""False when the invoice lies wholly outside a restriction the user has.

	A blank value passes, the way frappe treats an empty link field under a User Permission, and
	branch / cost center may sit on the item rows only, so a hit there counts, as in `matches`.
	"""
	for doctype, fieldname in PERMISSION_COLUMNS:
		allowed = scope.get(doctype)
		if not allowed:
			continue
		values = {inv.get(fieldname)} | item_values.get((inv.name, fieldname), set())
		values.discard(None)
		values.discard("")
		if values and not values & allowed:
			return False
	return True


def aggregate(invoices, currency):
	"""customer -> one row summing its invoices, in first-invoice order."""
	out = {}
	for inv in invoices:
		row = out.get(inv.customer)
		if not row:
			row = out[inv.customer] = frappe._dict(
				customer=inv.customer,
				customer_name=inv.customer_name,
				customer_group=inv.customer_group,
				territory=inv.customer_territory,
				first_invoice_date=inv.first_invoice_date,
				new_customers=0,
				repeat_customers=0,
				new_customer_revenue=0.0,
				repeat_customer_revenue=0.0,
				currency=currency,
			)
		row["new_customers" if inv.is_new else "repeat_customers"] += 1
		row.new_customer_revenue += inv.new_customer_revenue
		row.repeat_customer_revenue += inv.repeat_customer_revenue
		row.last_invoice_date = inv.posting_date

	for row in out.values():
		row.status = _("New") if row.new_customers else _("Existing")
		row.total = row.new_customers + row.repeat_customers
		row.total_revenue = row.new_customer_revenue + row.repeat_customer_revenue
	return out


def get_item_dimensions(filters):
	"""(invoice, fieldname) -> cost centers / branches on its item rows, for the window."""
	si = frappe.qb.DocType("Sales Invoice")
	item = frappe.qb.DocType("Sales Invoice Item")
	rows = (
		frappe.qb.from_(item)
		.inner_join(si)
		.on(si.name == item.parent)
		.select(item.parent, item.cost_center, item.branch)
		.where(
			(si.docstatus == 1)
			& (si.company == filters.company)
			& si.posting_date[filters.from_date : filters.to_date]
		)
		.distinct()
	).run(as_dict=True)
	out = {}
	for row in rows:
		for fieldname in ("cost_center", "branch"):
			if row[fieldname]:
				out.setdefault((row.parent, fieldname), set()).add(row[fieldname])
	return out


def get_descendants(doctype, name):
	lft, rgt = frappe.db.get_value(doctype, name, ["lft", "rgt"]) or (0, 0)
	return frappe.get_all(doctype, filters={"lft": [">=", lft], "rgt": ["<=", rgt]}, pluck="name") or [name]


def get_invoice_columns():
	return [
		{"label": _("Invoice"), "fieldname": "name", "fieldtype": "Link", "options": "Sales Invoice", "width": 210},
		{"label": _("Date"), "fieldname": "posting_date", "fieldtype": "Date", "width": 115},
		{"label": _("Customer"), "fieldname": "customer", "fieldtype": "Link", "options": "Customer", "width": 160},
		{"label": _("Customer Name"), "fieldname": "customer_name", "fieldtype": "Data", "width": 180},
		{"label": _("Customer Status"), "fieldname": "customer_status", "fieldtype": "Data", "width": 155},
		{"label": _("Invoice Type"), "fieldname": "type", "fieldtype": "Data", "width": 120},
		{"label": _("Customer Since"), "fieldname": "first_invoice_date", "fieldtype": "Date", "width": 110},
		{"label": _("Customer Group"), "fieldname": "customer_group", "fieldtype": "Link", "options": "Customer Group", "width": 130},
		{"label": _("Territory"), "fieldname": "territory", "fieldtype": "Link", "options": "Territory", "width": 120},
		{"label": _("Branch"), "fieldname": "branch", "fieldtype": "Link", "options": "Branch", "width": 120},
		{"label": _("Cost Center"), "fieldname": "cost_center", "fieldtype": "Link", "options": "Cost Center", "width": 130},
		{"label": _("Sales Person"), "fieldname": "custom_sales_person", "fieldtype": "Link", "options": "Sales Person", "width": 130},
		{"label": _("New Customer Revenue"), "fieldname": "new_customer_revenue", "fieldtype": "Currency", "options": "currency", "width": 160},
		{"label": _("Repeat Customer Revenue"), "fieldname": "repeat_customer_revenue", "fieldtype": "Currency", "options": "currency", "width": 170},
		{"label": _("Grand Total"), "fieldname": "base_grand_total", "fieldtype": "Currency", "options": "currency", "width": 140},
	]


def get_customer_columns():
	return [
		{"label": _("Customer"), "fieldname": "customer", "fieldtype": "Link", "options": "Customer", "width": 160},
		{"label": _("Customer Name"), "fieldname": "customer_name", "fieldtype": "Data", "width": 200},
		{"label": _("Customer Group"), "fieldname": "customer_group", "fieldtype": "Link", "options": "Customer Group", "width": 130},
		{"label": _("Territory"), "fieldname": "territory", "fieldtype": "Link", "options": "Territory", "width": 120},
		{"label": _("Status"), "fieldname": "status", "fieldtype": "Data", "width": 80},
		{"label": _("Customer Since"), "fieldname": "first_invoice_date", "fieldtype": "Date", "width": 110},
		{"label": _("Last Invoice"), "fieldname": "last_invoice_date", "fieldtype": "Date", "width": 110},
		{"label": _("New Customers"), "fieldname": "new_customers", "fieldtype": "Int", "width": 110},
		{"label": _("Repeat Invoices"), "fieldname": "repeat_customers", "fieldtype": "Int", "width": 110},
		{"label": _("Total Invoices"), "fieldname": "total", "fieldtype": "Int", "width": 105},
		{"label": _("New Customer Revenue"), "fieldname": "new_customer_revenue", "fieldtype": "Currency", "options": "currency", "width": 160},
		{"label": _("Repeat Customer Revenue"), "fieldname": "repeat_customer_revenue", "fieldtype": "Currency", "options": "currency", "width": 170},
		{"label": _("Total Revenue"), "fieldname": "total_revenue", "fieldtype": "Currency", "options": "currency", "width": 150},
	]


def get_summary(rows, currency):
	new = sum(1 for r in rows if r.new_customers or r.status == _("New"))
	total_revenue = sum(r.total_revenue for r in rows)
	return [
		{"label": _("Active Customers"), "value": len(rows), "datatype": "Int", "indicator": "Blue"},
		{"label": _("New Customers"), "value": new, "datatype": "Int", "indicator": "Green"},
		{"label": _("Existing Customers"), "value": len(rows) - new, "datatype": "Int", "indicator": "Orange"},
		{"label": _("Total Invoices"), "value": sum(r.total for r in rows), "datatype": "Int", "indicator": "Blue"},
		{
			"label": _("New Customer Revenue"),
			"value": sum(r.new_customer_revenue for r in rows),
			"datatype": "Currency",
			"currency": currency,
			"indicator": "Green",
		},
		{
			"label": _("Repeat Customer Revenue"),
			"value": sum(r.repeat_customer_revenue for r in rows),
			"datatype": "Currency",
			"currency": currency,
			"indicator": "Orange",
		},
		{"label": _("Total Revenue"), "value": total_revenue, "datatype": "Currency", "currency": currency, "indicator": "Blue"},
	]


def get_chart(rows):
	top = rows[:10]
	if not top:
		return None
	return {
		"data": {
			"labels": [r.customer_name or r.customer for r in top],
			"datasets": [
				{"name": _("New Customer Revenue"), "values": [r.new_customer_revenue for r in top]},
				{"name": _("Repeat Customer Revenue"), "values": [r.repeat_customer_revenue for r in top]},
			],
		},
		"type": "bar",
		"barOptions": {"stacked": 1},
		"fieldtype": "Currency",
		"title": _("Top 10 Customers by Revenue"),
	}
