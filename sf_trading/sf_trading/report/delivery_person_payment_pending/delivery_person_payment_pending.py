# Copyright (c) 2026, Enfono Technologies and contributors
# For license information, please see license.txt

"""Cash a delivery person still owes the company: invoice by invoice, or person by person.

A delivery person (`custom_driver` on Sales Invoice) collects the cash for the invoices they
deliver. The two rules that stop them taking more work are applied here exactly as the invoice
applies them (sf_trading.api.sales_invoice_override):

* **Days** -- an unpaid invoice older than the person's Payment Collection Days (`custom_payment_days`,
  1 when unset) makes them Overdue, and blocks their next cash sale.
* **Amount** -- once everything they hold reaches their Cash Collection Limit (`custom_cash_limit`,
  0 = no cap), the next cash sale is blocked too.

Outstanding is the invoice's own outstanding amount, the figure both rules read. A paid invoice is
shown only on request, with the date its last payment was recorded and how many days that took.
"""

from collections import defaultdict

import frappe
from frappe import _
from frappe.utils import add_days, cint, date_diff, flt, getdate, today

VIEW_INVOICE = "Invoice Wise"
VIEW_PERSON = "Delivery Person Summary"

PENDING = "Pending"
OVERDUE = "Overdue"
PAID = "Paid"
WITHIN = "Within Days"

TOLERANCE = 0.0005
PERMISSION_COLUMNS = (("Customer", "customer"), ("Branch", "branch"))

INVOICES_SQL = """
	select si.name as invoice, si.posting_date, si.customer, si.customer_name, si.branch,
		si.custom_driver as driver, si.custom_payment_mode as payment_mode,
		si.base_grand_total, si.base_rounded_total, si.outstanding_amount as outstanding,
		si.custom_sales_person as sales_person
	from `tabSales Invoice` si
	where si.docstatus = 1 and si.is_return = 0 and si.company = %(company)s
		and ifnull(si.custom_driver, '') != ''
		and (%(driver)s = '' or si.custom_driver = %(driver)s)
		and (%(customer)s = '' or si.customer = %(customer)s)
		and (%(from_date)s = '' or si.posting_date >= %(from_date)s)
		and (%(to_date)s = '' or si.posting_date <= %(to_date)s)
		and (%(include_paid)s = 1 or si.outstanding_amount > 0)
	order by si.posting_date, si.name
"""

LAST_COLLECTION_SQL = """
	select against_voucher_no as invoice, max(posting_date) as collected_on
	from `tabPayment Ledger Entry`
	where against_voucher_type = 'Sales Invoice' and delinked = 0
		and voucher_no != against_voucher_no and against_voucher_no in %(invoices)s
	group by against_voucher_no
"""


def execute(filters=None):
	filters = frappe._dict(filters or {})
	frappe.has_permission("Sales Invoice", "read", throw=True)
	if not filters.company:
		frappe.throw(_("Company is required"))

	as_on = getdate(today())
	status = filters.status or PENDING
	currency = frappe.get_cached_value("Company", filters.company, "default_currency")

	invoices = get_invoices(filters, include_paid=status in (PAID, "All"))
	drivers = get_drivers({r.driver for r in invoices} | ({filters.driver} if filters.driver else set()))
	scope = get_permitted_scope()
	branches = set(filters.get("branch") or [])
	invoices = [
		r for r in invoices if is_permitted(r, scope) and (not branches or r.branch in branches)
	]
	if invoices:
		collected = last_collections([r.invoice for r in invoices if flt(r.outstanding) <= TOLERANCE])
	else:
		collected = {}

	for row in invoices:
		describe(row, drivers.get(row.driver), as_on, collected, currency)

	if status == OVERDUE:
		invoices = [r for r in invoices if r.status == OVERDUE]
	elif status == PENDING:
		invoices = [r for r in invoices if r.status in (OVERDUE, WITHIN)]
	elif status == PAID:
		invoices = [r for r in invoices if r.status == PAID]

	people = summarise(invoices, drivers, currency)
	summary = get_summary(people, invoices, currency)
	if (filters.view or VIEW_INVOICE) == VIEW_PERSON:
		return person_columns(), people, None, person_chart(people), summary
	return invoice_columns(), invoices, None, None, summary


def get_invoices(filters, include_paid) -> list:
	return frappe.db.sql(
		INVOICES_SQL,
		{
			"company": filters.company,
			"driver": filters.driver or "",
			"customer": filters.customer or "",
			"from_date": filters.from_date or "",
			"to_date": filters.to_date or "",
			"include_paid": 1 if include_paid else 0,
		},
		as_dict=True,
	)


def get_drivers(names) -> dict:
	names = [n for n in names if n]
	if not names:
		return {}
	fields = ["name", "full_name", "status", "custom_payment_days", "custom_cash_limit"]
	return {r.name: r for r in frappe.get_all("Driver", filters={"name": ["in", names]}, fields=fields)}


def last_collections(invoices) -> dict:
	result = {}
	for start in range(0, len(invoices), 500):
		chunk = invoices[start : start + 500]
		for r in frappe.db.sql(LAST_COLLECTION_SQL, {"invoices": tuple(chunk)}, as_dict=True):
			result[r.invoice] = r.collected_on
	return result


def document_total(row) -> float:
	"""The invoice's payable total. rounded_total carries junk on migrated invoices here, so it is
	trusted only when it is set and within a fils of the grand total."""
	grand = flt(row.base_grand_total, 3)
	rounded = flt(row.base_rounded_total, 3)
	return rounded if rounded and abs(rounded - grand) < 1 else grand


def describe(row, driver, as_on, collected, currency):
	driver = driver or frappe._dict(full_name=row.driver, custom_payment_days=0, custom_cash_limit=0)
	row.driver_name = driver.full_name or row.driver
	# the invoice's own rule reads `custom_payment_days or 1`
	row.payment_days = cint(driver.custom_payment_days) or 1
	row.total = document_total(row)
	row.outstanding = flt(row.outstanding, 3)
	row.paid = flt(row.total - row.outstanding, 3)
	row.due_date = add_days(row.posting_date, row.payment_days)
	row.age = date_diff(as_on, row.posting_date)
	row.currency = currency
	if row.outstanding <= TOLERANCE:
		row.status = PAID
		row.collected_on = collected.get(row.invoice)
		row.days_taken = date_diff(row.collected_on, row.posting_date) if row.collected_on else None
		row.days_overdue = None
		return
	# same comparison as validate_driver_payment: DATEDIFF(today, posting_date) > payment days
	row.days_overdue = row.age - row.payment_days if row.age > row.payment_days else 0
	row.status = OVERDUE if row.age > row.payment_days else WITHIN


def summarise(invoices, drivers, currency) -> list:
	people = defaultdict(
		lambda: frappe._dict(
			pending_invoices=0, pending=0.0, overdue_invoices=0, overdue=0.0, paid_invoices=0,
			paid=0.0, oldest=None, branches=set(), max_days_overdue=0,
		)
	)
	for r in invoices:
		p = people[r.driver]
		p.branches.add(r.branch or "")
		if r.status == PAID:
			p.paid_invoices += 1
			p.paid += r.total
			continue
		p.pending_invoices += 1
		p.pending += r.outstanding
		if r.status == OVERDUE:
			p.overdue_invoices += 1
			p.overdue += r.outstanding
			p.max_days_overdue = max(p.max_days_overdue, r.days_overdue or 0)
		if not p.oldest or getdate(r.posting_date) < getdate(p.oldest):
			p.oldest = r.posting_date

	rows = []
	for name, p in people.items():
		driver = drivers.get(name) or frappe._dict()
		limit = flt(driver.get("custom_cash_limit"))
		blocked = []
		if p.overdue_invoices:
			blocked.append(_("Days"))
		if limit and p.pending >= limit:
			blocked.append(_("Cash Limit"))
		rows.append(
			{
				"driver": name,
				"driver_name": driver.get("full_name") or name,
				"driver_status": driver.get("status"),
				"branches": ", ".join(sorted(b for b in p.branches if b)),
				"payment_days": cint(driver.get("custom_payment_days")) or 1,
				"cash_limit": limit or None,
				"pending_invoices": p.pending_invoices,
				"pending": flt(p.pending, 3),
				"overdue_invoices": p.overdue_invoices,
				"overdue": flt(p.overdue, 3),
				"max_days_overdue": p.max_days_overdue or None,
				"oldest_pending": p.oldest,
				"limit_used": flt(p.pending / limit * 100, 1) if limit else None,
				"blocked": " + ".join(blocked),
				"paid_invoices": p.paid_invoices or None,
				"paid": flt(p.paid, 3) or None,
				"currency": currency,
			}
		)
	rows.sort(key=lambda r: (-r["overdue"], -r["pending"], r["driver_name"] or ""))
	return rows


def get_permitted_scope(user=None):
	"""doctype -> values the user may see. Raw SQL applies no permission filtering."""
	from frappe.permissions import get_user_permissions

	permissions = get_user_permissions(user or frappe.session.user) or {}
	scope = {}
	for doctype, _fieldname in PERMISSION_COLUMNS:
		allowed = {row.get("doc") for row in (permissions.get(doctype) or []) if row.get("doc")}
		if allowed:
			scope[doctype] = allowed
	return scope


def is_permitted(row, scope):
	for doctype, fieldname in PERMISSION_COLUMNS:
		allowed = scope.get(doctype)
		value = row.get(fieldname)
		if allowed and value and value not in allowed:
			return False
	return True


def money(fieldname, label, width=110):
	return {"fieldname": fieldname, "label": label, "fieldtype": "Currency", "options": "currency", "width": width}


CURRENCY_COLUMN = {"fieldname": "currency", "label": _("Currency"), "fieldtype": "Link", "options": "Currency", "hidden": 1}


def invoice_columns():
	return [
		{"fieldname": "driver", "label": _("Delivery Person"), "fieldtype": "Link", "options": "Driver", "width": 130},
		{"fieldname": "driver_name", "label": _("Name"), "fieldtype": "Data", "width": 150},
		{"fieldname": "branch", "label": _("Branch"), "fieldtype": "Link", "options": "Branch", "width": 75},
		{"fieldname": "invoice", "label": _("Invoice"), "fieldtype": "Link", "options": "Sales Invoice", "width": 120},
		{"fieldname": "posting_date", "label": _("Invoice Date"), "fieldtype": "Date", "width": 95},
		{"fieldname": "customer", "label": _("Customer"), "fieldtype": "Link", "options": "Customer", "width": 110},
		{"fieldname": "customer_name", "label": _("Customer Name"), "fieldtype": "Data", "width": 170},
		{"fieldname": "payment_mode", "label": _("Payment Mode"), "fieldtype": "Data", "width": 90},
		money("total", _("Invoice Total")),
		money("paid", _("Collected")),
		money("outstanding", _("Pending")),
		{"fieldname": "payment_days", "label": _("Collection Days"), "fieldtype": "Int", "width": 90},
		{"fieldname": "due_date", "label": _("Collect By"), "fieldtype": "Date", "width": 95},
		{"fieldname": "age", "label": _("Age (Days)"), "fieldtype": "Int", "width": 80},
		{"fieldname": "days_overdue", "label": _("Days Overdue"), "fieldtype": "Int", "width": 90},
		{"fieldname": "status", "label": _("Status"), "fieldtype": "Data", "width": 95},
		{"fieldname": "collected_on", "label": _("Collected On"), "fieldtype": "Date", "width": 95},
		{"fieldname": "days_taken", "label": _("Days Taken"), "fieldtype": "Int", "width": 80},
		{"fieldname": "sales_person", "label": _("Sales Person"), "fieldtype": "Link", "options": "Sales Person", "width": 120},
		CURRENCY_COLUMN,
	]


def person_columns():
	return [
		{"fieldname": "driver", "label": _("Delivery Person"), "fieldtype": "Link", "options": "Driver", "width": 130},
		{"fieldname": "driver_name", "label": _("Name"), "fieldtype": "Data", "width": 160},
		{"fieldname": "driver_status", "label": _("Status"), "fieldtype": "Data", "width": 70},
		{"fieldname": "branches", "label": _("Branches"), "fieldtype": "Data", "width": 100},
		{"fieldname": "blocked", "label": _("Blocked By"), "fieldtype": "Data", "width": 120},
		{"fieldname": "pending_invoices", "label": _("Pending Invoices"), "fieldtype": "Int", "width": 100},
		money("pending", _("Pending"), 120),
		{"fieldname": "overdue_invoices", "label": _("Overdue Invoices"), "fieldtype": "Int", "width": 100},
		money("overdue", _("Overdue"), 120),
		{"fieldname": "max_days_overdue", "label": _("Most Days Overdue"), "fieldtype": "Int", "width": 110},
		{"fieldname": "oldest_pending", "label": _("Oldest Pending"), "fieldtype": "Date", "width": 100},
		{"fieldname": "payment_days", "label": _("Collection Days"), "fieldtype": "Int", "width": 90},
		money("cash_limit", _("Cash Limit")),
		{"fieldname": "limit_used", "label": _("Limit Used %"), "fieldtype": "Percent", "width": 90},
		{"fieldname": "paid_invoices", "label": _("Paid Invoices"), "fieldtype": "Int", "width": 90},
		money("paid", _("Paid")),
		CURRENCY_COLUMN,
	]


def get_summary(people, invoices, currency):
	open_rows = [r for r in invoices if r.status != PAID]
	overdue = [r for r in invoices if r.status == OVERDUE]
	blocked = sum(1 for p in people if p["blocked"])
	return [
		{"label": _("Delivery Persons Holding Cash"), "value": sum(1 for p in people if p["pending_invoices"]), "datatype": "Int", "indicator": "Blue"},
		{"label": _("Pending"), "value": flt(sum(r.outstanding for r in open_rows), 3), "datatype": "Currency", "currency": currency, "indicator": "Orange"},
		{"label": _("Overdue"), "value": flt(sum(r.outstanding for r in overdue), 3), "datatype": "Currency", "currency": currency, "indicator": "Red"},
		{"label": _("Overdue Invoices"), "value": len(overdue), "datatype": "Int", "indicator": "Red" if overdue else "Green"},
		{"label": _("Blocked Delivery Persons"), "value": blocked, "datatype": "Int", "indicator": "Red" if blocked else "Green"},
	]


def person_chart(people):
	shown = [p for p in people if p["pending"]][:15]
	if not shown:
		return None
	return {
		"data": {
			"labels": [p["driver_name"] for p in shown],
			"datasets": [
				{"name": _("Within Days"), "values": [flt(p["pending"] - p["overdue"], 3) for p in shown]},
				{"name": _("Overdue"), "values": [p["overdue"] for p in shown]},
			],
		},
		"type": "bar",
		"barOptions": {"stacked": 1},
		"fieldtype": "Currency",
		"colors": ["#5e64ff", "#ff5858"],
	}
