# Copyright (c) 2026, Enfono Technologies and contributors
# For license information, please see license.txt

"""Advances paid to suppliers and received from customers, against Purchase Orders and Sales Orders.

What is an advance here
-----------------------
Money that moved before the invoice it is for. Four shapes, and the report names which one a row is:

* **Order Advance** -- a Payment Entry that names a Purchase Order or a Sales Order. ERPNext records it
  in one place only, the Advance Payment Ledger (`Advance Payment Ledger Entry`), never in the GL or
  the Payment Ledger, so that is where this report reads it (see sf_trading's Pending Advance PO).
* **Payment on Account** -- a Payment Entry with an unallocated amount: money taken from, or paid to,
  a party that no invoice, order or journal accounts for.
* **Journal Advance** -- a Journal Entry row ticked *Is Advance* on a customer or supplier that names
  no reference.
* **Order Field Only** -- an order whose `advance_paid` says money was taken but no payment is
  behind it any more.

The last three are the ones *marked as advance without a supporting voucher*. They are the point of the
report and are shown in red, with a Supporting Voucher column that says NONE, and a Status of
"No Supporting Voucher" that the Status filter can isolate.

Applied or not
--------------
ERPNext replaces a Payment Entry's order reference with a reference to the invoice when that invoice
takes the advance, but it leaves the order's own advance figure and ledger rows standing. Reading the
order's `advance_paid` therefore overstates what is still unspent (on this site it did by about six
times -- see the memory note on the advance ledger). So *adjusted* is measured the other way round: how
much of the payment has been allocated to the invoices that bill the same order. What is left is
*unapplied*, and an unapplied advance on an order that is already fully billed is called out, because
paying that invoice pays the supplier twice.

Dates and currency
------------------
The advance ledger carries no date of its own, so the date of an advance is its Payment Entry's
posting date. Amounts are shown in the company currency; the party's own currency is named.
"""

from collections import defaultdict

import frappe
from frappe import _
from frappe.query_builder.functions import Sum
from pypika import Field
from frappe.utils import cint, date_diff, flt, getdate, today

from sf_trading.query import fetch_in_map

VIEW_ENTRIES = "Advance Entries"
VIEW_ORDERS = "Order Summary"
VIEW_PARTY = "Party Summary"

ST_UNAPPLIED = "Unapplied"
ST_PART = "Part Adjusted"
ST_ADJUSTED = "Adjusted"
ST_NO_VOUCHER = "No Supporting Voucher"

KIND_ORDER = "Order Advance"
KIND_ACCOUNT = "Payment on Account"
KIND_JOURNAL = "Journal Advance"
KIND_FIELD = "Order Field Only"

ORDERS = {
	"Purchase Order": {
		"party_type": "Supplier", "party_field": "supplier", "name_field": "supplier_name",
		"progress_field": "per_received", "invoice": "Purchase Invoice", "invoice_item": "Purchase Invoice Item",
		"invoice_order_field": "purchase_order", "invoice_detail_field": "po_detail", "order_item": "Purchase Order Item",
	},
	"Sales Order": {
		"party_type": "Customer", "party_field": "customer", "name_field": "customer_name",
		"progress_field": "per_delivered", "invoice": "Sales Invoice", "invoice_item": "Sales Invoice Item",
		"invoice_order_field": "sales_order", "invoice_detail_field": "so_detail", "order_item": "Sales Order Item",
	},
}
# the direction a payment has to run to be an advance of that side
PAYMENT_TYPE = {"Supplier": "Pay", "Customer": "Receive"}

NO_BRANCH = "(No Branch)"
TOLERANCE = 0.005

PAYMENT_FIELDS = [
	"name", "party_type", "party", "party_name", "posting_date", "mode_of_payment", "branch", "reference_no",
	"payment_type", "docstatus", "unallocated_amount", "paid_amount", "received_amount", "base_paid_amount",
	"base_received_amount", "remarks",
]


def execute(filters=None):
	filters = frappe._dict(filters or {})
	frappe.has_permission("Payment Entry", "read", throw=True)
	if not filters.company:
		frappe.throw(_("Company is required"))

	currency = frappe.get_cached_value("Company", filters.company, "default_currency")
	entries = collect_entries(filters, currency)

	scope = get_permitted_scope()
	entries = [e for e in entries if keep(e, filters, scope)]
	for entry in entries:
		finish(entry)
	entries = [e for e in entries if wanted_status(e, filters) and old_enough(e, filters)]

	summary = get_summary(entries, currency)
	view = filters.view or VIEW_ENTRIES
	if view == VIEW_ORDERS:
		return order_columns(), order_rows(entries, currency), None, None, summary
	if view == VIEW_PARTY:
		return party_columns(), party_rows(entries, currency), None, party_chart(entries), summary
	return entry_columns(), entry_rows(entries, currency), None, None, summary


# ── collection ──────────────────────────────────────────────────────────────────────────────────


def collect_entries(filters, currency) -> list:
	entries = []
	entries += order_advances(filters, currency)
	entries += orders_with_no_payment(filters)
	entries += payments_on_account(filters, currency)
	entries += journal_advances(filters)
	return entries


ADVANCE_LEDGER_SQL = """
	select aple.voucher_no as payment, aple.against_voucher_type as order_type,
		aple.against_voucher_no as order_no, aple.currency as party_currency, sum(aple.amount) as net
	from `tabAdvance Payment Ledger Entry` aple
	where aple.company = %(company)s and aple.delinked = 0 and aple.voucher_type = 'Payment Entry'
		and aple.against_voucher_type in ('Purchase Order', 'Sales Order')
	group by aple.voucher_no, aple.against_voucher_type, aple.against_voucher_no, aple.currency
	having abs(sum(aple.amount)) >= %(tolerance)s
"""


def to_company(amount, payment, party_type, party_currency, currency) -> float:
	"""A party-currency amount of this Payment Entry in the company currency, at the payment's own rate."""
	if not party_currency or party_currency == currency:
		return flt(amount, 3)
	if party_type == "Supplier":
		paid, base = flt(payment.received_amount), flt(payment.base_received_amount)
	else:
		paid, base = flt(payment.paid_amount), flt(payment.base_paid_amount)
	return flt(amount * (base / paid), 3) if paid else flt(amount, 3)


def order_advances(filters, currency) -> list:
	"""One entry per (payment, order): an advance a Payment Entry took against an order."""
	rows = frappe.db.sql(ADVANCE_LEDGER_SQL, {"company": filters.company, "tolerance": TOLERANCE}, as_dict=True)
	if not rows:
		return []

	payments = fetch_in_map("Payment Entry", [r.payment for r in rows], PAYMENT_FIELDS)
	orders = {}
	for order_type, meta in ORDERS.items():
		names = [r.order_no for r in rows if r.order_type == order_type]
		if not names:
			continue
		fields = ["name", meta["party_field"], meta["name_field"], "transaction_date", "status", "base_grand_total",
		          "per_billed", meta["progress_field"], "branch"]
		for name, order in fetch_in_map(order_type, names, fields).items():
			orders[(order_type, name)] = order

	adjusted, adjusting = adjusted_amounts(rows)
	entries = []
	for r in rows:
		payment = payments.get(r.payment)
		order = orders.get((r.order_type, r.order_no))
		if not payment or payment.docstatus != 1 or not order:
			continue
		meta = ORDERS[r.order_type]
		party_type = meta["party_type"]
		key = (r.payment, r.order_no)
		advance = to_company(abs(flt(r.net)), payment, party_type, r.party_currency, currency)
		applied = min(advance, to_company(adjusted.get(key, 0), payment, party_type, r.party_currency, currency))
		entries.append(
			frappe._dict(
				kind=KIND_ORDER, party_type=party_type, party=order.get(meta["party_field"]),
				party_name=order.get(meta["name_field"]), voucher_type="Payment Entry", voucher=payment.name,
				date=payment.posting_date, mode_of_payment=payment.mode_of_payment, reference_no=payment.reference_no,
				branch=payment.branch or order.branch or NO_BRANCH, party_currency=r.party_currency,
				order_type=r.order_type, order_no=r.order_no, order_date=order.transaction_date,
				order_status=order.status, order_amount=flt(order.base_grand_total, 3),
				per_billed=flt(order.per_billed), progress=flt(order.get(meta["progress_field"])),
				advance=advance, adjusted=flt(applied, 3), invoices=adjusting.get(key, ""), why="",
			)
		)
	return entries


def order_invoices(order_type, names) -> dict:
	"""{order: invoices that bill it}, an invoice reaching its order through the item row's order link
	or through the order's own row (the mapper sometimes carries only one of the two)."""
	meta = ORDERS[order_type]
	item = frappe.qb.DocType(meta["invoice_item"])
	invoice = frappe.qb.DocType(meta["invoice"])
	order_item = frappe.qb.DocType(meta["order_item"])
	order_link = Field(meta["invoice_order_field"], table=item)

	direct = (
		frappe.qb.from_(item).join(invoice).on(invoice.name == item.parent)
		.select(order_link.as_("order_no"), item.parent.as_("invoice"))
		.where((invoice.docstatus == 1) & order_link.isin(names)).distinct().run(as_dict=True)
	)
	by_row = (
		frappe.qb.from_(item).join(invoice).on(invoice.name == item.parent)
		.join(order_item).on(order_item.name == Field(meta["invoice_detail_field"], table=item))
		.select(order_item.parent.as_("order_no"), item.parent.as_("invoice"))
		.where((invoice.docstatus == 1) & order_item.parent.isin(names)).distinct().run(as_dict=True)
	)
	found = defaultdict(set)
	for r in direct + by_row:
		found[r.order_no].add(r.invoice)
	return found


def adjusted_amounts(rows):
	"""({(payment, order): amount of the payment already allocated to invoices that bill that order},
	{(payment, order): the invoices}).

	The Payment Entry's allocation to an invoice is what has taken the advance: ERPNext moves it from the
	order reference to the invoice reference when the invoice adjusts it.
	"""
	linked = {}
	for order_type in ORDERS:
		names = sorted({r.order_no for r in rows if r.order_type == order_type})
		for chunk_start in range(0, len(names), 200):
			for order_no, invoices in order_invoices(order_type, names[chunk_start : chunk_start + 200]).items():
				linked[(order_type, order_no)] = invoices

	invoices = sorted({inv for group in linked.values() for inv in group})
	if not invoices:
		return {}, {}
	reference = frappe.qb.DocType("Payment Entry Reference")
	allocations = defaultdict(float)
	payments = sorted({r.payment for r in rows})
	for start in range(0, len(payments), 200):
		for chunk in (invoices[i : i + 200] for i in range(0, len(invoices), 200)):
			for r in (
				frappe.qb.from_(reference)
				.select(reference.parent.as_("payment"), reference.reference_name.as_("invoice"),
				        Sum(reference.allocated_amount).as_("allocated"))
				.where(
					reference.parent.isin(payments[start : start + 200])
					& reference.reference_doctype.isin(["Purchase Invoice", "Sales Invoice"])
					& reference.reference_name.isin(chunk)
				)
				.groupby(reference.parent, reference.reference_name)
				.run(as_dict=True)
			):
				allocations[(r.payment, r.invoice)] += flt(r.allocated)

	amounts, names = {}, {}
	for r in rows:
		total, used = 0.0, []
		for invoice in sorted(linked.get((r.order_type, r.order_no), ())):
			amount = allocations.get((r.payment, invoice), 0)
			if amount:
				total += amount
				used.append(invoice)
		if total:
			amounts[(r.payment, r.order_no)] = total
			names[(r.payment, r.order_no)] = ", ".join(used)
	return amounts, names


def orders_with_no_payment(filters) -> list:
	"""Orders that show an advance but have no payment behind it in the advance ledger."""
	entries = []
	for order_type, meta in ORDERS.items():
		backed = {
			r[0]
			for r in frappe.db.sql(
				"""select against_voucher_no from `tabAdvance Payment Ledger Entry`
				where company = %s and delinked = 0 and against_voucher_type = %s
				group by against_voucher_no having abs(sum(amount)) >= %s""",
				(filters.company, order_type, TOLERANCE),
			)
		}
		orders = frappe.get_all(
			order_type,
			filters={"company": filters.company, "docstatus": 1, "advance_paid": [">", TOLERANCE]},
			fields=["name", meta["party_field"], meta["name_field"], "transaction_date", "status", "base_grand_total",
			        "per_billed", meta["progress_field"], "branch", "advance_paid"],
			limit_page_length=0,
		)
		for o in orders:
			if o.name in backed:
				continue
			entries.append(
				frappe._dict(
					kind=KIND_FIELD, party_type=meta["party_type"], party=o.get(meta["party_field"]),
					party_name=o.get(meta["name_field"]), voucher_type=None, voucher=None, date=None,
					mode_of_payment=None, reference_no=None, branch=o.branch or NO_BRANCH, party_currency=None,
					order_type=order_type, order_no=o.name, order_date=o.transaction_date, order_status=o.status,
					order_amount=flt(o.base_grand_total, 3), per_billed=flt(o.per_billed),
					progress=flt(o.get(meta["progress_field"])), advance=flt(o.advance_paid, 3), adjusted=0.0,
					invoices="", why="",
				)
			)
	return entries


def payments_on_account(filters, currency) -> list:
	"""Payment Entries with money left unallocated: an advance on the party's account, no voucher behind it."""
	entries = []
	for party_type, payment_type in PAYMENT_TYPE.items():
		payments = frappe.get_all(
			"Payment Entry",
			filters={
				"company": filters.company, "docstatus": 1, "party_type": party_type, "payment_type": payment_type,
				"unallocated_amount": [">", TOLERANCE],
			},
			fields=PAYMENT_FIELDS + ["paid_from_account_currency", "paid_to_account_currency"],
			limit_page_length=0,
		)
		if not payments:
			continue
		referenced = set()
		names = [p.name for p in payments]
		for start in range(0, len(names), 200):
			referenced.update(
				r[0]
				for r in frappe.db.sql(
					"select distinct parent from `tabPayment Entry Reference` where parent in %s",
					(tuple(names[start : start + 200]),),
				)
			)
		for p in payments:
			party_currency = p.paid_to_account_currency if party_type == "Supplier" else p.paid_from_account_currency
			whole = p.name not in referenced
			entries.append(
				frappe._dict(
					kind=KIND_ACCOUNT, party_type=party_type, party=p.party, party_name=p.party_name,
					voucher_type="Payment Entry", voucher=p.name, date=p.posting_date,
					mode_of_payment=p.mode_of_payment, reference_no=p.reference_no, branch=p.branch or NO_BRANCH,
					party_currency=party_currency, order_type=None, order_no=None, order_date=None, order_status=None,
					order_amount=None, per_billed=None, progress=None,
					advance=to_company(flt(p.unallocated_amount), p, party_type, party_currency, currency),
					adjusted=0.0, invoices="",
					why=_("Names no invoice, order or journal") if whole else _("Part of the payment is allocated to no voucher"),
				)
			)
	return entries


JOURNAL_ADVANCE_SQL = """
	select je.name as journal, je.posting_date, jea.party_type, jea.party, jea.debit, jea.credit,
		jea.branch as row_branch
	from `tabJournal Entry Account` jea join `tabJournal Entry` je on je.name = jea.parent
	where je.docstatus = 1 and je.company = %(company)s and jea.party_type in ('Supplier', 'Customer')
		and jea.is_advance = 'Yes' and ifnull(jea.reference_type, '') = ''
"""


def journal_advances(filters) -> list:
	"""Journal rows ticked Is Advance on a customer or supplier that name no reference."""
	rows = frappe.db.sql(JOURNAL_ADVANCE_SQL, {"company": filters.company}, as_dict=True)
	entries = []
	for r in rows:
		amount = flt(r.debit) - flt(r.credit) if r.party_type == "Supplier" else flt(r.credit) - flt(r.debit)
		if amount <= TOLERANCE:
			continue
		name_field = "supplier_name" if r.party_type == "Supplier" else "customer_name"
		entries.append(
			frappe._dict(
				kind=KIND_JOURNAL, party_type=r.party_type, party=r.party,
				party_name=frappe.db.get_value(r.party_type, r.party, name_field),
				voucher_type="Journal Entry", voucher=r.journal, date=r.posting_date, mode_of_payment=None,
				reference_no=None, branch=r.row_branch or NO_BRANCH, party_currency=None, order_type=None,
				order_no=None, order_date=None, order_status=None, order_amount=None, per_billed=None, progress=None,
				advance=flt(amount, 3), adjusted=0.0, invoices="",
				why=_("Journal row marked Is Advance names no reference"),
			)
		)
	return entries


# ── filtering ───────────────────────────────────────────────────────────────────────────────────


def get_permitted_scope(user=None):
	"""doctype -> values the user may see. Raw SQL applies no permission filtering."""
	from frappe.permissions import get_user_permissions

	permissions = get_user_permissions(user or frappe.session.user) or {}
	scope = {}
	for doctype in ("Supplier", "Customer", "Branch"):
		allowed = {row.get("doc") for row in (permissions.get(doctype) or []) if row.get("doc")}
		if allowed:
			scope[doctype] = allowed
	return scope


def keep(entry, filters, scope) -> bool:
	if filters.party_type and filters.party_type != "All" and entry.party_type != filters.party_type:
		return False
	party_filter = filters.supplier if entry.party_type == "Supplier" else filters.customer
	if party_filter and entry.party != party_filter:
		return False
	allowed = scope.get(entry.party_type)
	if allowed and entry.party and entry.party not in allowed:
		return False
	branches = set(filters.get("branch") or [])
	if branches and entry.branch not in branches:
		return False
	allowed_branch = scope.get("Branch")
	if allowed_branch and entry.branch and entry.branch != NO_BRANCH and entry.branch not in allowed_branch:
		return False
	when = entry.date or entry.order_date
	if filters.from_date and when and getdate(when) < getdate(filters.from_date):
		return False
	if filters.to_date and when and getdate(when) > getdate(filters.to_date):
		return False
	if entry.order_date:
		if filters.order_from_date and getdate(entry.order_date) < getdate(filters.order_from_date):
			return False
		if filters.order_to_date and getdate(entry.order_date) > getdate(filters.order_to_date):
			return False
	if filters.mode_of_payment and entry.mode_of_payment != filters.mode_of_payment:
		return False
	if filters.order_no and filters.order_no.strip().lower() not in (entry.order_no or "").lower():
		return False
	return True


def finish(entry):
	"""Status, age, the supporting voucher and the reason a row is flagged."""
	entry.flagged = entry.kind != KIND_ORDER
	if entry.flagged:
		entry.status = ST_NO_VOUCHER
		entry.adjusted = 0.0
		entry.unapplied = entry.advance
		entry.supporting = _("NONE")
		if entry.kind == KIND_FIELD:
			entry.why = _("The order shows this advance but no payment is behind it in the advance ledger")
	else:
		entry.unapplied = flt(max(entry.advance - entry.adjusted, 0), 3)
		entry.supporting = entry.order_no
		if entry.adjusted >= entry.advance - TOLERANCE:
			entry.status = ST_ADJUSTED
		elif entry.adjusted > TOLERANCE:
			entry.status = ST_PART
		else:
			entry.status = ST_UNAPPLIED
		entry.why = order_remark(entry)
	reference = entry.date or entry.order_date
	entry.age = date_diff(today(), reference) if reference else None


def order_remark(entry) -> str:
	notes = []
	if entry.unapplied > TOLERANCE and entry.per_billed >= 100:
		notes.append(_("Order is fully billed but the advance was not taken by the invoice"))
	if entry.unapplied > TOLERANCE and entry.order_status == "Closed":
		notes.append(_("Order is closed with this advance still unapplied"))
	if entry.order_amount and entry.advance > entry.order_amount + TOLERANCE:
		notes.append(_("Advance is more than the order"))
	return "; ".join(notes)


def wanted_status(entry, filters) -> bool:
	status = filters.status or "All"
	return status == "All" or entry.status == status


def old_enough(entry, filters) -> bool:
	days = cint(filters.older_than_days)
	if not days:
		return True
	return entry.unapplied > TOLERANCE and (entry.age or 0) >= days


# ── rows ────────────────────────────────────────────────────────────────────────────────────────


def entry_rows(entries, currency) -> list:
	rows = []
	ordered = sorted(entries, key=lambda e: (e.party_type, (e.party_name or e.party or "").lower(), str(e.date or e.order_date or "")))
	for e in ordered:
		rows.append(
			{
				"party_type": e.party_type, "party": e.party, "party_name": e.party_name, "kind": e.kind,
				"voucher_type": e.voucher_type, "voucher": e.voucher, "date": e.date,
				"mode_of_payment": e.mode_of_payment, "branch": e.branch, "supporting": e.supporting,
				"order_type": e.order_type, "order_date": e.order_date, "order_status": e.order_status,
				"order_amount": e.order_amount, "advance": e.advance, "adjusted": e.adjusted,
				"unapplied": e.unapplied, "status": e.status, "age": e.age, "invoices": e.invoices,
				"reference_no": e.reference_no, "flagged": cint(e.flagged), "remarks": e.why or "",
				"currency": currency,
			}
		)
	return rows


def group_orders(entries) -> list:
	groups = {}
	for e in entries:
		if not e.order_no:
			continue
		g = groups.setdefault(
			(e.order_type, e.order_no),
			frappe._dict(
				party_type=e.party_type, party=e.party, party_name=e.party_name, order_type=e.order_type,
				order_no=e.order_no, order_date=e.order_date, order_status=e.order_status, branch=e.branch,
				order_amount=e.order_amount, per_billed=e.per_billed, progress=e.progress, advance=0.0, adjusted=0.0,
				unapplied=0.0, payments=0, last_payment=None, flagged=False, notes=set(),
			),
		)
		g.advance += e.advance
		g.adjusted += e.adjusted
		g.unapplied += e.unapplied
		if e.voucher:
			g.payments += 1
			if not g.last_payment or getdate(e.date) > getdate(g.last_payment):
				g.last_payment = e.date
		if e.flagged:
			g.flagged = True
		if e.why:
			g.notes.add(e.why)
		if e.branch and e.branch != NO_BRANCH:
			g.branch = e.branch
	return list(groups.values())


def order_rows(entries, currency) -> list:
	rows = []
	ordered = sorted(group_orders(entries), key=lambda g: (g.party_type, (g.party_name or g.party or "").lower(), str(g.order_date or "")))
	for g in ordered:
		if g.flagged:
			status = ST_NO_VOUCHER
		elif g.adjusted >= g.advance - TOLERANCE:
			status = ST_ADJUSTED
		elif g.adjusted > TOLERANCE:
			status = ST_PART
		else:
			status = ST_UNAPPLIED
		rows.append(
			{
				"party_type": g.party_type, "party": g.party, "party_name": g.party_name, "order_type": g.order_type,
				"order_no": g.order_no, "order_date": g.order_date, "branch": g.branch, "order_status": g.order_status,
				"order_amount": g.order_amount, "advance": flt(g.advance, 3), "adjusted": flt(g.adjusted, 3),
				"unapplied": flt(g.unapplied, 3), "balance": flt((g.order_amount or 0) - g.advance, 3),
				"advance_pct": flt(g.advance / g.order_amount * 100, 1) if g.order_amount else None,
				"per_billed": g.per_billed, "progress": g.progress, "payments": g.payments,
				"last_payment": g.last_payment, "status": status, "flagged": cint(g.flagged),
				"remarks": "; ".join(sorted(g.notes)), "currency": currency,
			}
		)
	return rows


def party_groups(entries) -> list:
	groups = {}
	for e in entries:
		g = groups.setdefault(
			(e.party_type, e.party),
			frappe._dict(
				party_type=e.party_type, party=e.party, party_name=e.party_name, orders=set(), advance=0.0,
				adjusted=0.0, unapplied=0.0, no_voucher=0.0, no_voucher_entries=0, oldest=None,
			),
		)
		g.advance += e.advance
		g.adjusted += e.adjusted
		g.unapplied += e.unapplied
		if e.order_no:
			g.orders.add(e.order_no)
		if e.flagged:
			g.no_voucher += e.advance
			g.no_voucher_entries += 1
		if e.unapplied > TOLERANCE:
			when = e.date or e.order_date
			if when and (not g.oldest or getdate(when) < getdate(g.oldest)):
				g.oldest = when
	return list(groups.values())


def party_rows(entries, currency) -> list:
	rows = []
	for g in sorted(party_groups(entries), key=lambda g: (g.party_type, -g.unapplied)):
		rows.append(
			{
				"party_type": g.party_type, "party": g.party, "party_name": g.party_name, "orders": len(g.orders),
				"advance": flt(g.advance, 3), "adjusted": flt(g.adjusted, 3), "unapplied": flt(g.unapplied, 3),
				"no_voucher": flt(g.no_voucher, 3), "no_voucher_entries": g.no_voucher_entries, "oldest": g.oldest,
				"flagged": cint(g.no_voucher_entries > 0), "currency": currency,
			}
		)
	return rows


# ── columns ─────────────────────────────────────────────────────────────────────────────────────


def money(fieldname, label, width=115):
	return {"fieldname": fieldname, "label": label, "fieldtype": "Currency", "options": "currency", "width": width}


CURRENCY_COLUMN = {"fieldname": "currency", "label": _("Currency"), "fieldtype": "Link", "options": "Currency", "hidden": 1}
FLAG_COLUMN = {"fieldname": "flagged", "label": _("Flagged"), "fieldtype": "Check", "hidden": 1}


def entry_columns():
	return [
		{"fieldname": "party_type", "label": _("Side"), "fieldtype": "Data", "width": 80},
		{"fieldname": "party", "label": _("Party"), "fieldtype": "Dynamic Link", "options": "party_type", "width": 130},
		{"fieldname": "party_name", "label": _("Party Name"), "fieldtype": "Data", "width": 190},
		{"fieldname": "kind", "label": _("Advance Type"), "fieldtype": "Data", "width": 140},
		{"fieldname": "voucher", "label": _("Advance Voucher"), "fieldtype": "Dynamic Link", "options": "voucher_type", "width": 150},
		{"fieldname": "voucher_type", "label": _("Voucher Type"), "fieldtype": "Data", "hidden": 1},
		{"fieldname": "date", "label": _("Advance Date"), "fieldtype": "Date", "width": 95},
		{"fieldname": "mode_of_payment", "label": _("Mode of Payment"), "fieldtype": "Link", "options": "Mode of Payment", "width": 120},
		{"fieldname": "branch", "label": _("Branch"), "fieldtype": "Data", "width": 80},
		{"fieldname": "supporting", "label": _("Supporting Voucher"), "fieldtype": "Dynamic Link", "options": "order_type", "width": 150},
		{"fieldname": "order_type", "label": _("Order Type"), "fieldtype": "Data", "hidden": 1},
		{"fieldname": "order_date", "label": _("Order Date"), "fieldtype": "Date", "width": 95},
		{"fieldname": "order_status", "label": _("Order Status"), "fieldtype": "Data", "width": 110},
		money("order_amount", _("Order Amount")),
		money("advance", _("Advance")),
		money("adjusted", _("Adjusted to Invoice")),
		money("unapplied", _("Unapplied")),
		{"fieldname": "status", "label": _("Status"), "fieldtype": "Data", "width": 150},
		{"fieldname": "age", "label": _("Age (Days)"), "fieldtype": "Int", "width": 85},
		{"fieldname": "invoices", "label": _("Adjusted By"), "fieldtype": "Data", "width": 160},
		{"fieldname": "reference_no", "label": _("Reference No"), "fieldtype": "Data", "width": 120},
		{"fieldname": "remarks", "label": _("Why / Remarks"), "fieldtype": "Data", "width": 300},
		FLAG_COLUMN,
		CURRENCY_COLUMN,
	]


def order_columns():
	return [
		{"fieldname": "party_type", "label": _("Side"), "fieldtype": "Data", "width": 80},
		{"fieldname": "party", "label": _("Party"), "fieldtype": "Dynamic Link", "options": "party_type", "width": 130},
		{"fieldname": "party_name", "label": _("Party Name"), "fieldtype": "Data", "width": 190},
		{"fieldname": "order_no", "label": _("Order"), "fieldtype": "Dynamic Link", "options": "order_type", "width": 160},
		{"fieldname": "order_type", "label": _("Order Type"), "fieldtype": "Data", "hidden": 1},
		{"fieldname": "order_date", "label": _("Order Date"), "fieldtype": "Date", "width": 95},
		{"fieldname": "branch", "label": _("Branch"), "fieldtype": "Data", "width": 80},
		{"fieldname": "order_status", "label": _("Order Status"), "fieldtype": "Data", "width": 110},
		money("order_amount", _("Order Amount")),
		money("advance", _("Advance")),
		{"fieldname": "advance_pct", "label": _("Advance %"), "fieldtype": "Percent", "width": 85, "disable_total": 1},
		money("adjusted", _("Adjusted to Invoice")),
		money("unapplied", _("Unapplied")),
		money("balance", _("Balance")),
		{"fieldname": "per_billed", "label": _("Billed %"), "fieldtype": "Percent", "width": 80, "disable_total": 1},
		{"fieldname": "progress", "label": _("Received / Delivered %"), "fieldtype": "Percent", "width": 110, "disable_total": 1},
		{"fieldname": "payments", "label": _("Payments"), "fieldtype": "Int", "width": 80},
		{"fieldname": "last_payment", "label": _("Last Advance"), "fieldtype": "Date", "width": 95},
		{"fieldname": "status", "label": _("Status"), "fieldtype": "Data", "width": 150},
		{"fieldname": "remarks", "label": _("Remarks"), "fieldtype": "Data", "width": 300},
		FLAG_COLUMN,
		CURRENCY_COLUMN,
	]


def party_columns():
	return [
		{"fieldname": "party_type", "label": _("Side"), "fieldtype": "Data", "width": 80},
		{"fieldname": "party", "label": _("Party"), "fieldtype": "Dynamic Link", "options": "party_type", "width": 130},
		{"fieldname": "party_name", "label": _("Party Name"), "fieldtype": "Data", "width": 220},
		{"fieldname": "orders", "label": _("Orders"), "fieldtype": "Int", "width": 70},
		money("advance", _("Advance")),
		money("adjusted", _("Adjusted to Invoice")),
		money("unapplied", _("Unapplied")),
		money("no_voucher", _("No Supporting Voucher")),
		{"fieldname": "no_voucher_entries", "label": _("Entries Without Voucher"), "fieldtype": "Int", "width": 100},
		{"fieldname": "oldest", "label": _("Oldest Unapplied"), "fieldtype": "Date", "width": 105},
		FLAG_COLUMN,
		CURRENCY_COLUMN,
	]


# ── cards / chart ───────────────────────────────────────────────────────────────────────────────


def get_summary(entries, currency):
	def card(label, value, indicator):
		return {"label": label, "value": flt(value, 3), "datatype": "Currency", "currency": currency, "indicator": indicator}

	def total(party_type, field):
		return sum(e[field] for e in entries if e.party_type == party_type)

	no_voucher = [e for e in entries if e.flagged]
	return [
		card(_("Supplier Advances"), total("Supplier", "advance"), "Blue"),
		card(_("Supplier Unapplied"), total("Supplier", "unapplied"), "Orange"),
		card(_("Customer Advances"), total("Customer", "advance"), "Blue"),
		card(_("Customer Unapplied"), total("Customer", "unapplied"), "Orange"),
		card(_("No Supporting Voucher"), sum(e.advance for e in no_voucher), "Red" if no_voucher else "Green"),
		{"label": _("Entries Without Voucher"), "value": len(no_voucher), "datatype": "Int", "indicator": "Red" if no_voucher else "Green"},
	]


def party_chart(entries):
	groups = sorted(party_groups(entries), key=lambda g: -g.unapplied)[:12]
	if not groups:
		return None
	return {
		"data": {
			"labels": [(g.party_name or g.party or "")[:22] for g in groups],
			"datasets": [
				{"name": _("Unapplied"), "values": [flt(g.unapplied, 3) for g in groups]},
				{"name": _("No Supporting Voucher"), "values": [flt(g.no_voucher, 3) for g in groups]},
			],
		},
		"type": "bar",
		"fieldtype": "Currency",
	}
