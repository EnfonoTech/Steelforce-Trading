# sf_trading/sales_order_limits.py
"""Sales Order limits by Customer and by Salesperson: the Driver's cash limit and payment days, on the other two parties.

A Driver already carries two figures (api/sales_invoice_override.py): `Payment Collection Days`, how long an unsettled Cash
invoice may stay unpaid, and `Cash Collection Limit`, how much uncollected cash they may hold at once, plus optional
per-branch caps. A new document is refused once the driver is already past either one (0 = no cap). This is the same rule for
a Customer and for a Salesperson, checked on the Sales Order:

* Payment Collection Days -- the party has a Cash invoice, still unpaid, older than that many days;
* Cash Collection Limit   -- the party's unpaid Cash invoices already add up to the limit or more;
* Branch Cash Limits      -- the same total narrowed to the order's own branch, against that branch's own cap.

"Uncollected cash" is the outstanding amount of the party's submitted Cash-mode Sales Invoices of the order's company. Credit
invoices are left out: a credit customer is governed by the Credit Limit and Credit Days. The order's own prospective amount is
not counted (an order has no outstanding yet): the NEXT order is refused once the party is at or over the limit, exactly as for the
driver. A Credit-mode order is not checked here, as a Credit-mode document is not checked for the driver.

The customer's figures and the salesperson's figures are independent: an order is refused when EITHER is reached. A party with
no figure set (0) costs no query at all. The figures are Custom Fields on Customer and on Sales Person (fixtures/custom_field.json),
the branch caps a Party Branch Cash Limit table on each. The old fixed cap of 2 open orders per customer (GS Issue 18) is gone.
"""

import frappe
from frappe import _
from frappe.utils import cint, flt, fmt_money, get_link_to_form, getdate, nowdate

CREDIT_MODE = "Credit"

#: (party doctype, the field of the order that names it, label, the clause of the invoice query that selects it)
PARTIES = (
	("Customer", "customer", "Customer", "customer = %(party)s"),
	("Sales Person", "custom_sales_person", "Salesperson", "custom_sales_person = %(party)s"),
)

# the invoices that count as uncollected cash
_UNPAID_CASH = " AND company = %(company)s AND docstatus = 1 AND is_return = 0 AND outstanding_amount > 0 AND custom_payment_mode = 'Cash'"
_CLAUSE = {party_type: clause for party_type, _field, _label, clause in PARTIES}


def get_limits(party_type, party) -> frappe._dict:
	"""{days, cash, branch: {branch: cap}} of one Customer / Sales Person; all zero while its fields do not exist yet.

	The meta is asked first, because the code can be pulled before `bench migrate` has created the fields, and a read of a column
	that is not there would break every Sales Order save until it has.
	"""
	meta = frappe.get_meta(party_type)
	if not (meta.has_field("custom_payment_days") and meta.has_field("custom_cash_limit")):
		return frappe._dict(days=0, cash=0.0, branch={})

	row = frappe.db.get_value(party_type, party, ["custom_payment_days", "custom_cash_limit"], as_dict=True) or {}
	branch = {}
	if meta.has_field("custom_branch_cash_limits"):
		for r in frappe.get_all(
			"Party Branch Cash Limit", filters={"parent": party, "parenttype": party_type}, fields=["branch", "cash_limit"]
		):
			if flt(r.cash_limit) > 0:
				branch[r.branch] = flt(r.cash_limit)
	return frappe._dict(days=cint(row.get("custom_payment_days")), cash=flt(row.get("custom_cash_limit")), branch=branch)


def overdue_invoice(party_type, party, company, as_of, days):
	"""The party's oldest unpaid Cash invoice that is more than `days` days old on `as_of`, or None."""
	rows = frappe.db.sql(
		"SELECT name, posting_date, outstanding_amount FROM `tabSales Invoice` WHERE "
		+ _CLAUSE[party_type]
		+ _UNPAID_CASH
		+ " AND DATEDIFF(%(as_of)s, posting_date) > %(days)s ORDER BY posting_date ASC LIMIT 1",
		{"party": party, "company": company, "as_of": as_of, "days": cint(days)},
		as_dict=True,
	)
	return rows[0] if rows else None


def uncollected_cash(party_type, party, company, branch=None) -> float:
	"""Sum of the party's unpaid Cash invoices, optionally only those raised at `branch`."""
	query = "SELECT COALESCE(SUM(outstanding_amount), 0) AS total FROM `tabSales Invoice` WHERE " + _CLAUSE[party_type] + _UNPAID_CASH
	params = {"party": party, "company": company}
	if branch:
		query += " AND branch = %(branch)s"
		params["branch"] = branch
	rows = frappe.db.sql(query, params, as_dict=True)
	return flt(rows[0].total) if rows else 0.0


def validate_sales_order_limits(doc, method=None):
	"""validate on Sales Order: refuse a new order for a customer or a salesperson already past a cash limit or the day limit."""
	if doc.get("custom_payment_mode") == CREDIT_MODE:
		return

	as_of = getdate(doc.get("transaction_date") or nowdate())
	for party_type, field, label, _clause in PARTIES:
		party = doc.get(field)
		if not party:
			continue
		limits = get_limits(party_type, party)
		if not (limits.days or limits.cash or limits.branch):
			continue

		if limits.days:
			invoice = overdue_invoice(party_type, party, doc.company, as_of, limits.days)
			if invoice:
				_refuse_overdue(doc, label, party, invoice, limits.days)

		if limits.cash:
			held = uncollected_cash(party_type, party, doc.company)
			if held >= limits.cash:
				_refuse_over_limit(doc, label, party, held, limits.cash)

		branch = doc.get("branch")
		if branch and limits.branch.get(branch):
			held = uncollected_cash(party_type, party, doc.company, branch)
			if held >= limits.branch[branch]:
				_refuse_over_limit(doc, label, party, held, limits.branch[branch], branch=branch)


def _refuse_overdue(doc, label, party, invoice, days):
	frappe.throw(
		_(
			"%s %s has an unpaid cash invoice %s dated %s with %s still outstanding. Payment was due within %s day(s). "
			"Please collect and record the payment before taking another order."
		)
		% (
			_(label),
			frappe.bold(party),
			get_link_to_form("Sales Invoice", invoice.name),
			invoice.posting_date,
			fmt_money(invoice.outstanding_amount, currency=doc.get("currency")),
			days,
		),
		title=_("%s Cash Payment Overdue") % _(label),
	)


def _refuse_over_limit(doc, label, party, held, limit, branch=None):
	scope = (_("at Branch %s") % branch) if branch else _("overall")
	frappe.throw(
		_(
			"%s %s is already holding %s in uncollected cash %s, at or over the Cash Limit of %s. "
			"Please collect and record the payment before taking another order."
		)
		% (
			_(label),
			frappe.bold(party),
			fmt_money(held, currency=doc.get("currency")),
			scope,
			fmt_money(limit, currency=doc.get("currency")),
		),
		title=_("%s Cash Limit Exceeded") % _(label),
	)


def validate_branch_cash_limit_allocation(doc, method=None):
	"""validate on Customer and on Sales Person: one row per branch, and the branch caps may not add up to more than the overall limit.

	The same check the Driver has (validate_driver_branch_cash_limit_allocation): the branches that carry a cap of their own may not
	collectively promise more than the party's overall Cash Collection Limit.
	"""
	rows = doc.get("custom_branch_cash_limits") or []
	seen = set()
	for row in rows:
		if row.get("branch") in seen:
			frappe.throw(
				_("Row #%s: Branch %s is listed twice in Branch Cash Limits. Keep one row per branch.")
				% (row.get("idx"), frappe.bold(row.get("branch"))),
				title=_("Duplicate Branch"),
			)
		seen.add(row.get("branch"))

	allocated = sum(flt(r.get("cash_limit")) for r in rows if flt(r.get("cash_limit")) > 0)
	if allocated <= 0:
		return
	total = flt(doc.get("custom_cash_limit"))
	if allocated > total:
		frappe.throw(
			_(
				"Branch Cash Limits add up to %s, more than the overall Cash Collection Limit of %s. "
				"Lower a branch's Cash Limit, or raise the Cash Collection Limit above."
			)
			% (fmt_money(allocated), fmt_money(total)),
			title=_("Branch Cash Limits Exceed Total"),
		)
