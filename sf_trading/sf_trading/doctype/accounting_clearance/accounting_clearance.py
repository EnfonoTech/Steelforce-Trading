# Copyright (c) 2026, Enfono Technologies and contributors
# For license information, please see license.txt

"""Accounting Clearance: what a person (or a branch) still has open on the accounts side, before they go.

The usual case: someone is leaving, or going home on leave, and accounts must first see that nothing they are
involved in is still open. Pick the User -- a salesperson or anyone else -- and every submitted transaction
they are involved in that still carries money is fetched into the table:

* Sales Invoices with an outstanding amount           -- as their salesperson, as their delivery person, or
                                                          made by them;
* Sales Orders not fully invoiced (the amount still to be invoiced) -- the same three ways;
* post-dated cheques they received that are not yet cleared;
* their Employee Advances not yet settled and Expense Claims not yet paid.

The person behind a User is found the way the rest of the app finds them: the Employee whose User ID it is,
the Sales Persons of that Employee or of the user's own Sales Person permission, and the Delivery Persons of
that Employee. An Employee, Sales Person, Delivery Person or Branch can be picked directly as well.

Only submitted documents count -- a draft is not a transaction. The table is refetched on every save and on
opening until the clearance is approved. It is approved by submitting it, which is refused while anything is
still pending; an approved clearance is the record that the person was clear on that day.
"""

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import date_diff, flt, getdate, nowdate

#: who can be cleared -> the doctype the party field links to
PARTY_DOCTYPE = {
	"User": "User",
	"Employee": "Employee",
	"Sales Person": "Sales Person",
	"Delivery Person": "Driver",
	"Branch": "Branch",
}

OPEN_ORDER_STATUSES = ("To Deliver and Bill", "To Bill", "To Deliver")
PDC_OPEN = ("Pending", "Partly Cleared", "Partly Returned")


class AccountingClearance(Document):
	def _validate_links(self):
		# the link check reads party_type to know what `party` points at, and on insert it runs before any
		# hook (before_insert, validate), so set it here -- the form sets it too, an API call may not
		self.party_type = PARTY_DOCTYPE.get(self.clearance_for)
		super()._validate_links()

	def onload(self):
		# an open clearance always shows the books as they stand when it is opened, not as they were at its
		# last save; an approved one keeps the record it was approved on
		if self.docstatus == 0 and not self.is_new():
			saved = self._snapshot()
			self.refresh_items()
			# the form marks itself unsaved when the books moved since the last save, so Save keeps the new list
			self.set_onload("pending_changed", saved != self._snapshot())

	def validate(self):
		self.party_name = get_party_name(self.clearance_for, self.party)
		if self.docstatus == 0:
			self.refresh_items()
			self.status = "Pending" if self.items else "Cleared"

	def before_submit(self):
		self.refresh_items()
		if self.items:
			frappe.throw(
				_("{0} still has {1} pending transaction(s), {2} still owed. Settle them before approving the clearance.").format(
					frappe.bold(self.party_name or self.party),
					len(self.items),
					frappe.format_value(self.total_outstanding, {"fieldtype": "Currency"}),
				),
				title=_("Not Cleared Yet"),
			)
		self.status = "Approved"
		self.approved_on = nowdate()
		self.approved_by = frappe.session.user

	def on_cancel(self):
		self.db_set("status", "Cancelled")

	def refresh_items(self):
		rows = get_pending_transactions(self.clearance_for, self.party, self.company, self.as_on_date)
		self.set("items", [])
		for row in rows:
			self.append("items", row)
		self.pending_count = len(rows)
		self.total_outstanding = sum(flt(r.get("outstanding")) for r in rows if r.get("in_total"))

	def _snapshot(self):
		return (self.pending_count, flt(self.total_outstanding), [(r.reference_doctype, r.reference_name, flt(r.outstanding)) for r in self.items])


def get_party_name(clearance_for, party):
	if not party:
		return None
	field = {"Employee": "employee_name", "Sales Person": "sales_person_name", "Delivery Person": "full_name", "User": "full_name"}.get(clearance_for)
	if not field:
		return party
	return frappe.db.get_value(PARTY_DOCTYPE[clearance_for], party, field) or party


@frappe.whitelist()
def get_pending_transactions(clearance_for, party, company, as_on_date=None):
	"""The pending rows for one party, oldest first; the form calls this the moment a party is picked."""
	if not (clearance_for in PARTY_DOCTYPE and party and company):
		return []
	frappe.has_permission("Accounting Clearance", "read", throw=True)
	as_on = getdate(as_on_date or nowdate())

	rows = []
	if clearance_for == "User":
		rows += _user_rows(party, company, as_on)
	elif clearance_for == "Employee":
		rows += _employee_rows(party, company, as_on)
	elif clearance_for == "Sales Person":
		rows += _sales_person_rows(party, company, as_on)
	elif clearance_for == "Delivery Person":
		rows += _driver_rows(party, company, as_on)
	elif clearance_for == "Branch":
		rows += _branch_rows(party, company, as_on)

	# one document surfaces once, however many ways the person is tied to it (made it AND is its salesperson)
	seen, unique = set(), []
	for row in rows:
		key = (row["reference_doctype"], row["reference_name"])
		if key not in seen:
			seen.add(key)
			unique.append(row)
	unique.sort(key=lambda r: (str(r.get("posting_date") or ""), r["reference_name"]))
	return unique


# ── who a user is ──────────────────────────────────────────────────────────────


def linked_parties(user):
	"""(employee, [sales persons], [delivery persons]) behind a User."""
	employee = frappe.db.get_value("Employee", {"user_id": user}, "name")
	sales_persons = set(
		frappe.get_all("User Permission", filters={"user": user, "allow": "Sales Person"}, pluck="for_value")
	)
	drivers = set()
	if employee:
		sales_persons |= set(frappe.get_all("Sales Person", filters={"employee": employee}, pluck="name"))
		drivers |= set(frappe.get_all("Driver", filters={"employee": employee}, pluck="name"))
	return employee, sorted(sales_persons), sorted(drivers)


# ── row builders ────────────────────────────────────────────────────────────────


def _row(category, doctype, name, posting_date, counterparty, amount, outstanding, status, as_on, in_total=1):
	"""One pending transaction. `in_total` = money actually owed; an order not yet invoiced is listed with the
	amount still to invoice, but that is not money owed yet and is not added to the Total Pending Amount."""
	return {
		"category": category,
		"reference_doctype": doctype,
		"reference_name": name,
		"posting_date": posting_date,
		"counterparty": counterparty,
		"amount": flt(amount),
		"outstanding": flt(outstanding),
		"pending_status": status,
		"days_pending": max(date_diff(as_on, posting_date), 0) if posting_date else 0,
		"in_total": in_total,
	}


def _unpaid_invoices(company, as_on, category=None, **where):
	rows = frappe.get_all(
		"Sales Invoice",
		filters={"company": company, "docstatus": 1, "is_return": 0, "outstanding_amount": [">", 0], **where},
		fields=["name", "posting_date", "due_date", "customer_name", "grand_total", "outstanding_amount", "custom_payment_mode"],
		order_by="posting_date asc",
	)
	out = []
	for r in rows:
		status = r.custom_payment_mode or _("Unpaid")
		if r.due_date and getdate(r.due_date) < as_on:
			status += " · " + _("Overdue")
		out.append(_row(category or _("Sales Invoice Outstanding"), "Sales Invoice", r.name, r.posting_date, r.customer_name,
			r.grand_total, r.outstanding_amount, status, as_on))
	return out


def _open_orders(company, as_on, **where):
	rows = frappe.get_all(
		"Sales Order",
		filters={"company": company, "docstatus": 1, "status": ["in", OPEN_ORDER_STATUSES], "per_billed": ["<", 100], **where},
		fields=["name", "transaction_date", "customer_name", "grand_total", "per_billed", "status"],
		order_by="transaction_date asc",
	)
	return [
		_row(_("Sales Order Not Invoiced"), "Sales Order", r.name, r.transaction_date, r.customer_name, r.grand_total,
			flt(r.grand_total) * (100 - flt(r.per_billed)) / 100, _(r.status), as_on, in_total=0)
		for r in rows
	]


def _uncleared_cheques(company, as_on, **where):
	if not frappe.get_meta("Payment Entry").has_field("custom_pdc_status"):
		return []
	rows = frappe.get_all(
		"Payment Entry",
		filters={"company": company, "docstatus": 1, "custom_pdc_status": ["in", PDC_OPEN], **where},
		fields=["name", "posting_date", "party_name", "paid_amount", "custom_pdc_cleared_amount", "custom_pdc_returned_amount", "custom_pdc_status"],
		order_by="posting_date asc",
	)
	return [
		_row(_("Cheque Not Cleared"), "Payment Entry", r.name, r.posting_date, r.party_name, r.paid_amount,
			flt(r.paid_amount) - flt(r.custom_pdc_cleared_amount) - flt(r.custom_pdc_returned_amount), _(r.custom_pdc_status), as_on)
		for r in rows
	]


def _advances_and_claims(employee, company, as_on):
	rows = []
	if frappe.db.exists("DocType", "Employee Advance"):
		for r in frappe.get_all(
			"Employee Advance",
			filters={"employee": employee, "company": company, "docstatus": 1},
			fields=["name", "posting_date", "advance_amount", "paid_amount", "claimed_amount", "return_amount", "status"],
		):
			unpaid = flt(r.advance_amount) - flt(r.paid_amount)
			unsettled = flt(r.paid_amount) - flt(r.claimed_amount) - flt(r.return_amount)
			if unsettled > 0:
				rows.append(_row(_("Advance Not Settled"), "Employee Advance", r.name, r.posting_date, "", r.advance_amount, unsettled, _(r.status), as_on))
			elif unpaid > 0:
				rows.append(_row(_("Advance Not Paid"), "Employee Advance", r.name, r.posting_date, "", r.advance_amount, unpaid, _(r.status), as_on))
	if frappe.db.exists("DocType", "Expense Claim"):
		for r in frappe.get_all(
			"Expense Claim",
			filters={"employee": employee, "company": company, "docstatus": 1},
			fields=["name", "posting_date", "grand_total", "total_amount_reimbursed", "status"],
		):
			left = flt(r.grand_total) - flt(r.total_amount_reimbursed)
			if left > 0:
				rows.append(_row(_("Expense Claim Unpaid"), "Expense Claim", r.name, r.posting_date, "", r.grand_total, left, _(r.status), as_on))
	return rows


# ── per party ──────────────────────────────────────────────────────────────────


def _sales_person_rows(sales_person, company, as_on):
	return _unpaid_invoices(company, as_on, custom_sales_person=sales_person) + _open_orders(company, as_on, custom_sales_person=sales_person)


def _driver_rows(driver, company, as_on):
	return _unpaid_invoices(company, as_on, category=_("Cash Not Collected"), custom_driver=driver, custom_payment_mode="Cash")


def _user_rows(user, company, as_on):
	employee, sales_persons, drivers = linked_parties(user)
	rows = []
	for driver in drivers:
		rows += _driver_rows(driver, company, as_on)
	for sales_person in sales_persons:
		rows += _sales_person_rows(sales_person, company, as_on)
	# what the user made themselves, whoever the salesperson on it is
	rows += _unpaid_invoices(company, as_on, owner=user) + _open_orders(company, as_on, owner=user)
	rows += _uncleared_cheques(company, as_on, owner=user)
	if employee:
		rows += _advances_and_claims(employee, company, as_on)
	return rows


def _employee_rows(employee, company, as_on):
	user = frappe.db.get_value("Employee", employee, "user_id")
	if user:
		return _user_rows(user, company, as_on)
	# an employee with no login: their own advances and claims, and the roles tied to them
	rows = _advances_and_claims(employee, company, as_on)
	for sales_person in frappe.get_all("Sales Person", filters={"employee": employee}, pluck="name"):
		rows += _sales_person_rows(sales_person, company, as_on)
	for driver in frappe.get_all("Driver", filters={"employee": employee}, pluck="name"):
		rows += _driver_rows(driver, company, as_on)
	return rows


def _branch_rows(branch, company, as_on):
	return (
		_unpaid_invoices(company, as_on, branch=branch)
		+ _open_orders(company, as_on, branch=branch)
		+ _uncleared_cheques(company, as_on, branch=branch)
	)
