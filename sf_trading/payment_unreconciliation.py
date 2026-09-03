# sf_trading/payment_unreconciliation.py
"""Undo a payment allocation, party by party — the mirror image of Payment Reconciliation.

WHY THIS EXISTS
---------------
ERPNext v15 can already unreconcile, but only one document at a time: open the Payment Entry,
press Unreconcile, tick its rows. When a party's ledger has been mis-applied across a dozen
invoices — which is exactly what happened here in August, where returns were netted against the
wrong invoices — that is a dozen trips through a form. Payment Reconciliation lets you work a
whole party in one screen; this gives the reverse the same shape.

WHAT IT DOES NOT DO
-------------------
It does not touch the ledger itself. Finding the allocations is this module's job; undoing one is
handed to erpnext's own `create_unreconcile_doc_for_selection`, which builds an Unreconcile
Payment document per pair and runs the tested path:

    unlink_ref_doc_from_payment_entries → cancel_exchange_gain_loss_journal → update_voucher_outstanding

Reimplementing that is how you end up with an invoice whose outstanding no longer matches its
Payment Ledger Entries. Every row that unreconciles here leaves the same audit trail as one done
from the Payment Entry — a submitted Unreconcile Payment naming the pair.

THE ONE RULE WORTH SPELLING OUT
-------------------------------
Unreconciling re-opens an invoice and may cancel an exchange gain/loss journal, so it writes to
the past. Rows dated on or before a submitted Period Closing Voucher are refused: re-opening a
closed period silently is how a trial balance stops tying, and this site has already lived
through one repost that moved money it should not have.
"""

import json

import frappe
from frappe import _
from frappe.utils import add_days, cint, cstr, flt, getdate, nowdate

from sf_trading.query import fetch_in

# What erpnext's Unreconcile Payment will actually accept -- its validate() hard-codes
# supported_types = ["Payment Entry", "Journal Entry"] and throws on anything else.
PAYMENT_TYPES = ("Payment Entry", "Journal Entry")
# What the screen LISTS. A credit note netted straight onto its own invoice writes a Payment
# Ledger Entry whose voucher_type is the invoice doctype; 16 such allocations worth 492.767 BHD
# exist on this site and the screen used to claim they did not exist. A tool whose premise is
# "here is everything live against this party" must not silently omit rows -- so they are listed,
# marked, and locked instead.
LISTED_TYPES = PAYMENT_TYPES + ("Sales Invoice", "Purchase Invoice")


_HAS_OUTSTANDING = {}


def _has_outstanding(doctype: str) -> bool:
	"""Whether this doctype even carries outstanding_amount.

	An allocation does not have to point at an invoice. This site has 2,619 against Sales
	Invoice and 593 against Purchase Invoice — both of which have the column — but also 20
	against Journal Entry and one against another Payment Entry, and neither of those has it.
	Reading it blindly failed with "Unknown column 'outstanding_amount'" and took the whole
	screen down for a party that happened to own one such row.
	"""
	if doctype not in _HAS_OUTSTANDING:
		_HAS_OUTSTANDING[doctype] = bool(frappe.db.has_column(doctype, "outstanding_amount"))
	return _HAS_OUTSTANDING[doctype]


def closed_period_date(company: str):
	"""The latest date closed by a submitted Period Closing Voucher, or None.

	The field is `period_end_date` -- this doctype has no posting_date at all, and asking for one
	fails with "Unknown column 'posting_date'" rather than quietly returning nothing.
	"""
	row = frappe.db.sql(
		"""select max(period_end_date) from `tabPeriod Closing Voucher`
		   where company = %s and docstatus = 1""",
		(company,),
	)
	return row[0][0] if row and row[0] else None


DIMENSION_FIELDS = ("cost_center", "project", "branch", "finance_book")
INVOICE_ALLOCATION = "Invoice Allocation"
ORDER_ADVANCE = "Order Advance"


def advance_entries(company, party_type, party, account=None, from_date=None, to_date=None,
                    minimum_amount=None, maximum_amount=None, against_voucher_no=None,
                    dimensions=None, limit=500, payment_no=None, reconciled_within=None):
	"""Advances a payment holds against an ORDER, from the Advance Payment Ledger Entry.

	An order advance never touches the Payment Ledger Entry -- it lives only here -- so a
	party-wise screen that reads PLE alone cannot see it. erpnext's own per-voucher dialog unions
	the two (get_linked_advances), and so does this.

	Three things the ledger does not carry and one that must be filtered:
	  * no party and no posting date -- both come from the Payment Entry it belongs to
	  * no account either; the party account is paid_from on a receipt and paid_to on a payment
	  * event must be "Submit". An "Adjustment" row is the advance being consumed as the order
	    gets billed, not an allocation somebody chose, and unreconciling one means nothing.
	"""
	conditions = [
		"adv.delinked = 0",
		"adv.company = %(company)s",
		"adv.event = 'Submit'",
		"adv.voucher_type = 'Payment Entry'",
		"pe.docstatus = 1",
		"pe.party_type = %(party_type)s",
		"pe.party = %(party)s",
	]
	values = {"company": company, "party_type": party_type, "party": party}

	if from_date:
		conditions.append("pe.posting_date >= %(from_date)s")
		values["from_date"] = getdate(from_date)
	if to_date:
		conditions.append("pe.posting_date <= %(to_date)s")
		values["to_date"] = getdate(to_date)
	if against_voucher_no:
		conditions.append("adv.against_voucher_no = %(against_voucher_no)s")
		values["against_voucher_no"] = against_voucher_no
	if payment_no:
		conditions.append("adv.voucher_no = %(payment_no)s")
		values["payment_no"] = payment_no
	if account:
		conditions.append(
			"(case when pe.payment_type = 'Receive' then pe.paid_from else pe.paid_to end)"
			" = %(account)s")
		values["account"] = account

	if isinstance(dimensions, str):
		dimensions = json.loads(dimensions or "{}")
	for field, value in (dimensions or {}).items():
		# the advance's dimensions are the payment's; finance_book is not on Payment Entry
		if not value or field not in DIMENSION_FIELDS:
			continue
		if not frappe.db.has_column("Payment Entry", field):
			continue
		conditions.append(f"pe.{field} = %({field})s")
		values[field] = value

	# >= not >: 32 legs sit at exactly 0.005 and were invisible, which is not the same thing as
	# being immaterial
	having = ["allocated_amount >= 0.005"]
	if minimum_amount:
		having.append("allocated_amount >= %(minimum_amount)s")
		values["minimum_amount"] = flt(minimum_amount)
	if maximum_amount:
		having.append("allocated_amount <= %(maximum_amount)s")
		values["maximum_amount"] = flt(maximum_amount)
	within = RECONCILED_WINDOWS.get(cstr(reconciled_within))
	if within:
		having.append("min(adv.creation) >= %(reconciled_since)s")
		values["reconciled_since"] = add_days(getdate(nowdate()), -within)
	values["limit"] = min(int(limit or 500), 2000)

	return frappe.db.sql(
		f"""
		select
			adv.company, adv.voucher_type, adv.voucher_no,
			adv.against_voucher_type, adv.against_voucher_no,
			adv.currency, pe.posting_date, pe.party_type, pe.party,
			(case when pe.payment_type = 'Receive' then pe.paid_from else pe.paid_to end) as account,
			abs(sum(adv.amount)) as allocated_amount,
			min(adv.creation) as allocated_on,
			max(adv.owner) as allocated_by,
			max(pe.branch) as payment_branch,
			max(pe.cost_center) as payment_cost_center
		from `tabAdvance Payment Ledger Entry` adv
		join `tabPayment Entry` pe on pe.name = adv.voucher_no
		where {" and ".join(conditions)}
		group by adv.voucher_no, adv.against_voucher_type, adv.against_voucher_no, adv.currency
		having {" and ".join(having)}
		order by pe.posting_date desc, adv.voucher_no
		limit %(limit)s
		""",
		values,
		as_dict=True,
	)


@frappe.whitelist()
def reconciled_entries(company, party_type, party, account=None, voucher_type=None,
                       from_date=None, to_date=None, minimum_amount=None, maximum_amount=None,
                       against_voucher_no=None, limit=500, dimensions=None, payment_no=None,
                       reconciled_within=None):
	"""Every live allocation of this party's payments against its invoices.

	Read from the Payment Ledger Entry rather than from Payment Entry Reference rows, because the
	ledger is what `outstanding_amount` is derived from and it covers Journal Entries in the same
	shape. A row where voucher_no equals against_voucher_no is the invoice's own entry, not an
	allocation, so it is excluded.
	"""
	if not (company and party_type and party):
		frappe.throw(_("Company, Party Type and Party are all needed"))
	frappe.has_permission("Payment Ledger Entry", "read", throw=True)

	conditions = [
		"ple.delinked = 0",
		"ple.company = %(company)s",
		"ple.party_type = %(party_type)s",
		"ple.party = %(party)s",
		"ple.voucher_no != ple.against_voucher_no",
		"ple.voucher_type in %(payment_types)s",
	]
	values = {
		"company": company, "party_type": party_type, "party": party,
		"payment_types": list(LISTED_TYPES),
	}
	if voucher_type:
		conditions.append("ple.voucher_type = %(voucher_type)s")
		values["voucher_type"] = voucher_type
	if account:
		conditions.append("ple.account = %(account)s")
		values["account"] = account
	if from_date:
		conditions.append("ple.posting_date >= %(from_date)s")
		values["from_date"] = getdate(from_date)
	if to_date:
		conditions.append("ple.posting_date <= %(to_date)s")
		values["to_date"] = getdate(to_date)
	if against_voucher_no:
		conditions.append("ple.against_voucher_no = %(against_voucher_no)s")
		values["against_voucher_no"] = against_voucher_no
	if payment_no:
		conditions.append("ple.voucher_no = %(payment_no)s")
		values["payment_no"] = payment_no

	# The Payment Ledger Entry carries the dimensions itself (cost_center, project, branch,
	# finance_book are all real columns and populated on this site), so a dimension filter needs
	# no join -- and it filters the ALLOCATION, which is what the user is choosing between.
	if isinstance(dimensions, str):
		dimensions = json.loads(dimensions or "{}")
	for field, value in (dimensions or {}).items():
		if not value or field not in DIMENSION_FIELDS:
			continue
		if not frappe.db.has_column("Payment Ledger Entry", field):
			continue
		conditions.append(f"ple.{field} = %({field})s")
		values[field] = value

	having = ["allocated_amount >= 0.005"]
	if minimum_amount:
		having.append("allocated_amount >= %(minimum_amount)s")
		values["minimum_amount"] = flt(minimum_amount)
	if maximum_amount:
		having.append("allocated_amount <= %(maximum_amount)s")
		values["maximum_amount"] = flt(maximum_amount)
	# when the allocation was MADE, which is a different question from when the payment was
	# posted -- and the only one that answers "somebody reconciled something yesterday". It has
	# to be a HAVING: creation is aggregated, not grouped on.
	within = RECONCILED_WINDOWS.get(cstr(reconciled_within))
	if within:
		having.append("min(ple.creation) >= %(reconciled_since)s")
		values["reconciled_since"] = add_days(getdate(nowdate()), -within)

	values["limit"] = min(int(limit or 500), 2000)

	rows = frappe.db.sql(
		f"""
		select
			ple.company, ple.voucher_type, ple.voucher_no,
			ple.against_voucher_type, ple.against_voucher_no,
			ple.account, ple.party_type, ple.party, ple.account_currency as currency,
			max(ple.posting_date) as posting_date,
			abs(sum(ple.amount)) as allocated_amount,
			count(*) as leg_rows,
			max(ple.remarks) as remarks,
			min(ple.creation) as allocated_on,
			max(ple.owner) as allocated_by,
			max(ple.branch) as payment_branch,
			max(ple.cost_center) as payment_cost_center
		from `tabPayment Ledger Entry` ple
		where {" and ".join(conditions)}
		group by ple.voucher_type, ple.voucher_no, ple.against_voucher_type, ple.against_voucher_no,
		         ple.account, ple.account_currency
		having {" and ".join(having)}
		order by max(ple.posting_date) desc, ple.voucher_no
		limit %(limit)s
		""",
		values,
		as_dict=True,
	)

	# order advances live in a different ledger; the screen shows both, marked
	if not voucher_type or voucher_type == "Payment Entry":
		rows += advance_entries(
			company, party_type, party, account=account, from_date=from_date, to_date=to_date,
			minimum_amount=minimum_amount, maximum_amount=maximum_amount,
			against_voucher_no=against_voucher_no, dimensions=dimensions, limit=limit,
			payment_no=payment_no, reconciled_within=reconciled_within,
		)

	annotate(rows, company, party_type, party)
	return rows


# ---------------------------------------------------------------------------------------------
# The insight layer
#
# A flat list of live allocations is unreadable: one bulk journal shows up as eleven rows that
# look like eleven separate events, and nothing on screen says which row a human touched
# yesterday versus which arrived with a data load. Everything below exists to answer, at a
# glance, "which of these did somebody get wrong".
#
# Two rules govern the flags. They are OBSERVATIONS, never accusations -- acting on this screen
# breaks a real ledger link, so a flag that shouts at a legitimate allocation is worse than no
# flag at all. And they must stay cheap: every one is computed set-based for the whole page, so
# the cost does not grow with the number of rows.
# ---------------------------------------------------------------------------------------------

BULK_LEGS = 5            # a voucher spread over this many invoices reads as one bulk event
RECENT_DAYS = 7          # "somebody did this lately" window
DUST_AMOUNT = 0.05       # rounding-size allocation, in the account currency
LATE_DAYS = 180          # a settlement this far after the invoice is usually a clean-up sweep
RECONCILED_WINDOWS = {"Today": 0, "Last 7 days": 7, "Last 30 days": 30}

RISK = "risk"
LEAD = "lead"          # not a fault -- a row that is probably why the user came
FRESH = "fresh"
PLAIN = "plain"


def _leg_counts(rows):
	"""How many documents each payment on this page is spread across, and whether it reaches
	beyond the party on screen.

	Keyed on the page's own vouchers, not on the company. The company-wide version of this query
	was measured at 241ms of annotate's 299ms on UAT -- an index scan of 59,431 ledger rows that
	grows with the ledger, so on a production ledger it would be seconds. Keyed on the page it is
	a range scan of about a dozen values.

	Not party-scoped on purpose: when a voucher's legs outnumber the legs listed, that voucher
	also allocates to somebody else, and undoing what is visible leaves the rest of it standing.
	"""
	vouchers = list({row["voucher_no"] for row in rows})
	if not vouchers:
		return {}

	counts = {}
	for chunk_start in range(0, len(vouchers), 900):
		chunk = vouchers[chunk_start:chunk_start + 900]
		for r in frappe.db.sql("""
			select voucher_no, count(distinct against_voucher_no) as legs,
			       count(distinct party) as parties
			from `tabPayment Ledger Entry`
			where delinked = 0 and voucher_no != against_voucher_no
			  and voucher_no in %(vouchers)s
			group by voucher_no
		""", {"vouchers": chunk}, as_dict=True):
			counts[r.voucher_no] = {"legs": cint(r.legs), "parties": cint(r.parties)}

		for r in frappe.db.sql("""
			select voucher_no, count(distinct against_voucher_no) as legs
			from `tabAdvance Payment Ledger Entry`
			where delinked = 0 and event = 'Submit' and voucher_no in %(vouchers)s
			group by voucher_no
		""", {"vouchers": chunk}, as_dict=True):
			seen = counts.setdefault(r.voucher_no, {"legs": 0, "parties": 1})
			seen["legs"] += cint(r.legs)
	return counts


def _payment_context(rows):
	"""The paying side's own facts: is it an amendment, is it a receipt or a refund, and is there
	money on it that was never applied. One read per payment doctype."""
	by_doctype = {}
	for row in rows:
		by_doctype.setdefault(row["voucher_type"], set()).add(row["voucher_no"])

	optional = (("payment_type", "payment_type"), ("unallocated_amount", "unallocated_amount"),
	            ("amended_from", "amended_from"), ("mode_of_payment", "mode_of_payment"))
	context = {}
	for doctype, names in by_doctype.items():
		fields = ["name"] + [f"{f} as {alias}" for f, alias in optional
		                     if frappe.db.has_column(doctype, f)]
		for doc in fetch_in(doctype, list(names), fields=fields):
			context[(doctype, doc["name"])] = doc
	return context


def _return_intent(rows):
	"""What the returns on this page say they reverse, and whether that invoice is still open.

	`return_against` is the only field in this whole picture that records INTENT: the return
	itself names the invoice it reverses. So a return netted somewhere else is a contradiction in
	the data rather than a correlation with how the business happens to work -- the one
	evidence-grade test available here. It is also nearly blind: only 59 of this site's 1,893
	returns record an origin at all, so a clean screen is not proof of a clean ledger.
	"""
	names = {}
	for row in rows:
		for doctype, name in ((row["voucher_type"], row["voucher_no"]),
		                      (row["against_voucher_type"], row["against_voucher_no"])):
			if doctype in ("Sales Invoice", "Purchase Invoice"):
				names.setdefault(doctype, set()).add(name)

	intent, origins = {}, {}
	for doctype, group in names.items():
		returns = fetch_in(doctype, list(group), filters={"is_return": 1},
		                   fields=["name", "return_against"])
		wanted = [r.return_against for r in returns if r.return_against]
		if wanted:
			# one read for every origin invoice on the page, not one per return
			for doc in fetch_in(doctype, wanted, fields=["name", "outstanding_amount"]):
				origins[doc.name] = flt(doc.outstanding_amount)
		for r in returns:
			if not r.return_against:
				continue
			r["origin_outstanding"] = origins.get(r.return_against, 0.0)
			intent[r.name] = r
	return intent


def _floating_credit_notes(rows):
	"""Live credit notes still pointing at the documents on this page.

	This is the client's actual pathology, seen from the other end: an invoice relieved once by
	cash and once by a return that was never netted against it. If a row's invoice has a return
	sitting unapplied, that row is the one to look at. One read per invoice doctype.
	"""
	by_doctype = {}
	for row in rows:
		if row["against_voucher_type"] in ("Sales Invoice", "Purchase Invoice"):
			by_doctype.setdefault(row["against_voucher_type"], set()).add(row["against_voucher_no"])

	floating = {}
	for doctype, names in by_doctype.items():
		for r in frappe.db.sql(f"""
			select return_against, count(*) as notes, abs(sum(outstanding_amount)) as amount,
			       min(name) as example
			from `tab{doctype}`
			where docstatus = 1 and is_return = 1 and return_against in %(names)s
			  and abs(outstanding_amount) > 0.005
			group by return_against
		""", {"names": list(names)}, as_dict=True):
			floating[r.return_against] = r
	return floating


@frappe.whitelist()
def party_alerts(company, party_type, party):
	"""What is true about this party before a single row is read.

	Somebody opens this screen because something is off. Twice out of three the thing that is off
	is an unapplied credit note -- so say so up front, instead of leaving them to infer it from a
	list of allocations. Also warns about the allocations ERPNext cannot undo at all: a credit
	note netted straight onto its invoice writes a Payment Ledger Entry whose voucher_type is
	Sales Invoice, and core's Unreconcile Payment only supports Payment Entry and Journal Entry.
	"""
	frappe.has_permission("Unreconcile Payment", "read", throw=True)
	alerts = []
	doctype = "Sales Invoice" if party_type == "Customer" else "Purchase Invoice"
	party_field = "customer" if party_type == "Customer" else "supplier"

	if frappe.db.has_column(doctype, "is_return"):
		open_notes = frappe.db.sql(f"""
			select name, posting_date, return_against, abs(outstanding_amount) as amount
			from `tab{doctype}`
			where docstatus = 1 and is_return = 1 and company = %(company)s
			  and `{party_field}` = %(party)s and abs(outstanding_amount) > 0.005
			order by abs(outstanding_amount) desc
		""", {"company": company, "party": party}, as_dict=True)
		if open_notes:
			biggest = open_notes[0]
			alerts.append({
				"kind": "credit_note",
				"message": _("{0} unapplied credit note(s) worth {1}. The largest is {2}{3}.").format(
					len(open_notes),
					frappe.utils.fmt_money(sum(flt(n.amount) for n in open_notes),
					                       currency=frappe.get_cached_value("Company", company,
					                                                        "default_currency")),
					biggest.name,
					_(" (issued against {0})").format(biggest.return_against)
					if biggest.return_against else "",
				),
				"reference": biggest.name,
			})

	# voucher_no != against_voucher_no is not optional here: every invoice writes its OWN row to
	# this ledger with voucher_type = the invoice doctype, so without the guard this counts the
	# party's invoices. It read 443 for Steel Art WLL, where the true answer is 0.
	netted = frappe.db.sql("""
		select count(*) from `tabPayment Ledger Entry`
		where delinked = 0 and company = %(company)s and party_type = %(party_type)s
		  and party = %(party)s and voucher_no != against_voucher_no
		  and voucher_type in ('Sales Invoice', 'Purchase Invoice')
	""", {"company": company, "party_type": party_type, "party": party})[0][0]
	if netted:
		alerts.append({
			"kind": "not_undoable",
			"message": _("{0} ledger entries for this party come from a credit note netted straight "
			             "onto an invoice. ERPNext cannot unreconcile those -- cancel or amend the "
			             "credit note instead.").format(netted),
		})
	return alerts


def _target_context(rows):
	"""The invoice/order side of every row on the page: one read per target doctype.

	This also replaces what used to be a get_value per row -- the old loop cost one query per
	line, which a 500-row page could not afford.
	"""
	by_doctype = {}
	for row in rows:
		by_doctype.setdefault(row["against_voucher_type"], set()).add(row["against_voucher_no"])

	optional = (
		("grand_total", "target_total"),
		("outstanding_amount", "target_outstanding"),
		("advance_paid", "target_advance_paid"),
		("branch", "target_branch"),
		("cost_center", "target_cost_center"),
		("status", "target_status"),
		("is_return", "target_is_return"),
		("return_against", "target_return_against"),
	)

	context = {}
	for doctype, names in by_doctype.items():
		fields = ["name", "docstatus"]
		for field in ("posting_date", "transaction_date"):
			if frappe.db.has_column(doctype, field):
				fields.append(f"{field} as target_date")
				break
		fields += [f"{f} as {alias}" for f, alias in optional
		           if frappe.db.has_column(doctype, f)]
		# get_all deliberately: the rows are already scoped to a party the user filtered on, and
		# a permission-filtered read here would silently blank the context of some rows
		for doc in fetch_in(doctype, list(names), fields=fields):
			context[(doctype, doc["name"])] = doc
	return context


def _applied_totals(rows):
	"""Everything applied to each target, from every payment and party, live and undone.

	Three facts a clerk cannot work out by eye from a list of legs: whether more is applied to a
	document than it is worth, how many other payments are also settling it (so undoing this row
	will not fully re-open it), and whether an allocation here was already stripped once --
	usually because the paying voucher was cancelled and re-amended, which is exactly when the
	wrong invoice gets picked.
	"""
	targets = list({row["against_voucher_no"] for row in rows})
	if not targets:
		return {}
	totals = {}
	for chunk_start in range(0, len(targets), 900):
		chunk = targets[chunk_start:chunk_start + 900]
		for r in frappe.db.sql("""
			select against_voucher_no,
			       abs(sum(case when delinked = 0 then amount else 0 end)) as applied,
			       count(distinct case when delinked = 0 then voucher_no end) as payers,
			       max(case when delinked = 1 then 1 else 0 end) as had_undo
			from `tabPayment Ledger Entry`
			where voucher_no != against_voucher_no and against_voucher_no in %(targets)s
			group by against_voucher_no
		""", {"targets": chunk}, as_dict=True):
			totals[r.against_voucher_no] = r
	return totals


def _bulk_day(rows):
	"""The day a data load wrote most of this page, if there was one.

	Derived from the page rather than hardcoded, and not from the owner alone: 13 of the 15 most
	recent allocations on this site are Administrator-owned too, so "owner is Administrator" would
	dismiss this week's live work as machine noise. A single day that carries most of the page and
	is entirely Administrator-written is a load; anything else is somebody's work.
	"""
	by_day = {}
	for row in rows:
		if not row.get("allocated_on"):
			continue
		day = getdate(row["allocated_on"])
		seen = by_day.setdefault(day, {"count": 0, "system": 0})
		seen["count"] += 1
		if row.get("allocated_by") == "Administrator":
			seen["system"] += 1

	for day, seen in by_day.items():
		if seen["count"] >= max(3, 0.3 * len(rows)) and seen["count"] == seen["system"]:
			return day
	return None


def annotate(rows, company, party_type, party):
	"""Fill in provenance, the target's own figures, and the observation flags; then order the
	page so the rows worth a second look are the ones the eye lands on first."""
	if not rows:
		return rows

	advance_doctypes = set(frappe.get_hooks("advance_payment_doctypes") or [])
	closed = closed_period_date(company)
	legs = _leg_counts(rows)
	context = _target_context(rows)
	paid_by = _payment_context(rows)
	floating = _floating_credit_notes(rows)
	intent = _return_intent(rows)
	applied = _applied_totals(rows)
	fresh_after = add_days(getdate(nowdate()), -RECENT_DAYS)
	bulk_day = _bulk_day(rows)

	for row in rows:
		row["entry_type"] = (ORDER_ADVANCE if row["against_voucher_type"] in advance_doctypes
		                     else INVOICE_ALLOCATION)
		target = context.get((row["against_voucher_type"], row["against_voucher_no"])) or {}
		payment = paid_by.get((row["voucher_type"], row["voucher_no"])) or {}

		spread = legs.get(row["voucher_no"]) or {}
		row["leg_count"] = cint(spread.get("legs")) or 1
		row["other_parties"] = max(cint(spread.get("parties")) - 1, 0)
		row["target_date"] = target.get("target_date")
		row["target_total"] = flt(target.get("target_total"))
		row["target_status"] = target.get("target_status")
		row["target_branch"] = target.get("target_branch")
		applied_here = applied.get(row["against_voucher_no"]) or {}
		row["applied_total"] = flt(applied_here.get("applied"))
		row["payers"] = cint(applied_here.get("payers")) or 1
		row["unallocated_amount"] = flt(payment.get("unallocated_amount"))
		row["is_amendment"] = bool(payment.get("amended_from"))
		row["imported"] = bool(bulk_day and row.get("allocated_on")
		                       and getdate(row["allocated_on"]) == bulk_day
		                       and row.get("allocated_by") == "Administrator")
		row["days_gap"] = (
			(getdate(row["posting_date"]) - getdate(row["target_date"])).days
			if row["target_date"] else 0
		)
		row["in_closed_period"] = bool(closed and getdate(row["posting_date"]) <= getdate(closed))

		# an order has no outstanding; what matters there is how much advance it is carrying
		if row["entry_type"] == ORDER_ADVANCE:
			row["outstanding_amount"] = flt(target.get("target_advance_paid"))
		else:
			row["outstanding_amount"] = flt(target.get("target_outstanding"))

		notes, worth_a_look, lead = [], False, False

		# listed for completeness, but core cannot undo it: erpnext's Unreconcile Payment accepts
		# only a Payment Entry or a Journal Entry
		row["undoable"] = row["voucher_type"] in PAYMENT_TYPES
		if not row["undoable"]:
			notes.append(_("Netted credit note — undo this on the credit note, not here"))

		# A closed period does not make a row suspicious, it makes it un-undoable. Mixing "you may
		# not do this" into "this might be wrong" devalues both.
		if row["in_closed_period"]:
			row["undoable"] = False
			notes.append(_("Inside a closed accounting period"))
		if cint(target.get("docstatus")) == 2:
			notes.append(_("Applied to a cancelled document"))
			worth_a_look = True
		# Both branches must be filled in before a difference means anything. On this data a
		# naive comparison "finds" 800 mismatches, every one of them a blank payment branch
		# against a populated invoice branch; requiring both leaves the 5 that are real.
		if row.get("payment_branch") and row["target_branch"] \
				and row["payment_branch"] != row["target_branch"]:
			notes.append(_("Different branch"))
			worth_a_look = True

		# Not a fault, and usually the reason somebody opened this screen: the invoice was
		# settled in cash and the party still holds an open credit note against it. Netting that
		# note is only possible once this allocation is undone -- so the row is a lead, and it
		# sorts high, but it is never accused of anything. (Checked: on this site all four such
		# invoices have zero outstanding, so nothing is double-relieved.)
		note = floating.get(row["against_voucher_no"])
		if note:
			notes.append(_("Its credit note {0} for {1} is unapplied").format(
				note.example, frappe.utils.fmt_money(flt(note.amount), currency=row["currency"])))
			lead = True

		if cint(target.get("target_is_return")):
			# A label, not a suspicion. 40 rows point at a return while claiming to be an
			# "Invoice Allocation", and the outstanding figure they show has the opposite sign to
			# what a reader assumes. No direction test: a receipt that nets a credit note against
			# the same party's invoices is the most ordinary use of a credit note there is -- all
			# five such receipts here balance to the fils against their own paid amount.
			origin = (intent.get(row["against_voucher_no"]) or {}).get("return_against")
			notes.append(_("Applied to credit note {0}{1}").format(
				row["against_voucher_no"],
				_(" (a return of {0})").format(origin) if origin else ""))

		# The evidence-grade one: the return names the invoice it reverses, and this is not it.
		mine = intent.get(row["voucher_no"])
		if mine and mine.return_against != row["against_voucher_no"]:
			notes.append(_("This return says it reverses {0}, but it was netted against {1}").format(
				mine.return_against, row["against_voucher_no"]))
			worth_a_look = True
		# and the harm-gated variant: a return refunded in cash while its own invoice still owes
		if (mine and row["voucher_type"] in ("Sales Invoice", "Purchase Invoice")
				and abs(flt(mine.get("origin_outstanding"))) > 0.05):
			notes.append(_("{0} is still open for {1}").format(
				mine.return_against,
				frappe.utils.fmt_money(flt(mine["origin_outstanding"]), currency=row["currency"])))
			worth_a_look = True
		# Not an alarm: a payment older than the invoice it settles is usually an advance being
		# consumed, and one such receipt lights up sixteen rows at once. Say it, do not shout it.
		if (row["entry_type"] == INVOICE_ALLOCATION and row["target_date"]
				and getdate(row["posting_date"]) < getdate(row["target_date"])):
			notes.append(_("Paid before the invoice date"))
		# abs(): a credit note's grand_total is NEGATIVE, and a plain > comparison called every
		# one of the 40 credit-note targets on this site over-applied
		if row["target_total"] and row["applied_total"] > abs(row["target_total"]) + 0.005:
			notes.append(_("More applied than the document is worth"))
			worth_a_look = True
		if row["days_gap"] > LATE_DAYS:
			notes.append(_("Settled {0} days later").format(row["days_gap"]))
		# 85 of 3,177 allocations site-wide, 14 of Steel Art's 384 -- a 27x narrowing, and the
		# literal sentence a customer says. A lead, not an alarm: part-settlement is ordinary.
		if (row["entry_type"] == INVOICE_ALLOCATION
				and abs(flt(row["outstanding_amount"])) > 0.005
				and not cint(target.get("target_is_return"))):
			notes.append(_("Still open for {0}").format(frappe.utils.fmt_money(
				flt(row["outstanding_amount"]), currency=row["currency"])))
			lead = True
		if cint(row.get("leg_rows")) > 1:
			# the grouped total hides them: three legs of -35.255, -35.255 and -21.670 read as one
			# tidy 92.180 against the invoice
			notes.append(_("{0} ledger legs for this one pair").format(cint(row["leg_rows"])))
			worth_a_look = True
		if row["payers"] > 1:
			notes.append(_("Also settled by {0} other payment(s)").format(row["payers"] - 1))
		if row.get("other_parties"):
			# undoing what is on screen would leave the rest of this voucher standing
			notes.append(_("This voucher also allocates to {0} other part(y/ies)").format(
				row["other_parties"]))
		if row["allocated_amount"] < DUST_AMOUNT:
			notes.append(_("Rounding-size"))

		# provenance: the single most useful thing on the row. "Somebody did this on Tuesday" is
		# what separates a mistake from four months of settled history.
		recent = bool(row.get("allocated_on") and getdate(row["allocated_on"]) >= fresh_after)
		if recent:
			notes.append(_("New"))
		if row["entry_type"] == ORDER_ADVANCE:
			notes.append(_("Order advance"))

		row["severity"] = (RISK if worth_a_look else LEAD if lead
		                   else FRESH if recent else PLAIN)
		# the glyph lives in the text on purpose: a CSS tint only exists for the rows the grid has
		# actually rendered, so on page 2 of a big party the text is all that is left
		row["insight"] = ("\u26a0 " if worth_a_look else "") + " · ".join(notes)
		row["allocated_by"] = _short_user(row.get("allocated_by"))

	order = {RISK: 0, LEAD: 1, FRESH: 2, PLAIN: 3}
	rows.sort(key=lambda r: (order.get(r["severity"], 3),
	                         -(getdate(r["allocated_on"]).toordinal() if r.get("allocated_on") else 0),
	                         -flt(r["allocated_amount"])))
	return rows


def _short_user(user):
	"""'rahul@steelforceco.com' reads as 'Rahul' in a grid cell one inch wide.

	frappe's own document cache, not lru_cache: a worker lives for days and a renamed user must
	not stay wrong until the next restart.
	"""
	if not user:
		return ""
	if user == "Administrator":
		return _("System")
	return frappe.get_cached_value("User", user, "full_name") or user.split("@")[0]


def _validate_row(row, closed):
	"""Everything that must be true before erpnext is asked to unlink a pair."""
	for field in ("company", "voucher_type", "voucher_no", "against_voucher_type",
	              "against_voucher_no"):
		if not row.get(field):
			return _("{0} is missing").format(field)

	if row["voucher_type"] not in PAYMENT_TYPES:
		# the server-side backstop for the rows the screen deliberately lists but locks
		return _("{0} is a {1}; ERPNext can only unreconcile a {2}. Cancel or amend the credit "
		         "note instead.").format(row["voucher_no"], _(row["voucher_type"]),
		                                 _(" or a ").join(PAYMENT_TYPES))

	if frappe.db.get_value(row["voucher_type"], row["voucher_no"], "docstatus") != 1:
		return _("{0} is not submitted").format(row["voucher_no"])

	# an order advance never wrote a Payment Ledger Entry, so its liveness must be read
	# from the ledger it actually lives in, or every advance row is refused below
	if row.get("entry_type") == ORDER_ADVANCE:
		live = frappe.db.count("Advance Payment Ledger Entry", {
			"delinked": 0,
			"event": "Submit",
			"company": row["company"],
			"voucher_no": row["voucher_no"],
			"against_voucher_no": row["against_voucher_no"],
		})
	else:
		live = frappe.db.count("Payment Ledger Entry", {
			"delinked": 0,
			"company": row["company"],
			"voucher_no": row["voucher_no"],
			"against_voucher_no": row["against_voucher_no"],
		})
	if not live:
		# somebody else got there first, or it was never allocated to begin with
		return _("{0} is no longer allocated to {1}").format(
			row["voucher_no"], row["against_voucher_no"])

	posting_date = frappe.db.get_value(row["voucher_type"], row["voucher_no"], "posting_date")
	if closed and posting_date and getdate(posting_date) <= getdate(closed):
		return _("{0} is dated {1}, on or before the period closed on {2}").format(
			row["voucher_no"], frappe.utils.formatdate(posting_date),
			frappe.utils.formatdate(closed))
	return None


@frappe.whitelist()
def unreconcile(rows):
	"""Unlink each selected pair, one savepoint at a time.

	erpnext's own helper loops over the whole selection inside one transaction, so a single bad
	row takes the entire batch down with it. Here each pair gets its own savepoint: what can be
	undone is undone, and what cannot is reported with the reason.
	"""
	from erpnext.accounts.doctype.unreconcile_payment.unreconcile_payment import (
		create_unreconcile_doc_for_selection,
	)

	if isinstance(rows, str):
		rows = json.loads(rows)
	if not rows:
		frappe.throw(_("Nothing selected"))

	frappe.has_permission("Unreconcile Payment", "create", throw=True)

	done, failed = [], []
	closed_cache = {}
	for row in rows:
		company = row.get("company")
		if company not in closed_cache:
			closed_cache[company] = closed_period_date(company)

		problem = _validate_row(row, closed_cache[company])
		if problem:
			failed.append({"voucher_no": row.get("voucher_no"),
			               "against_voucher_no": row.get("against_voucher_no"),
			               "error": problem})
			continue

		savepoint = "sf_unreconcile"
		try:
			frappe.db.savepoint(savepoint)
			create_unreconcile_doc_for_selection(json.dumps([{
				"company": company,
				"voucher_type": row["voucher_type"],
				"voucher_no": row["voucher_no"],
				"against_voucher_type": row["against_voucher_type"],
				"against_voucher_no": row["against_voucher_no"],
			}]))
			# hand back the audit record so the user can open what was created, not just be
			# told something happened
			audit = frappe.db.get_value(
				"Unreconcile Payment",
				{"voucher_no": row["voucher_no"], "docstatus": 1},
				"name",
				order_by="creation desc",
			)
			done.append({"voucher_no": row["voucher_no"],
			             "against_voucher_no": row["against_voucher_no"],
			             "allocated_amount": flt(row.get("allocated_amount")),
			             "audit": audit})
		except Exception as e:
			frappe.db.rollback(save_point=savepoint)
			failed.append({"voucher_no": row.get("voucher_no"),
			               "against_voucher_no": row.get("against_voucher_no"),
			               "error": str(e)[:200]})
			frappe.log_error(frappe.get_traceback(),
			                 f"SF unreconcile failed: {row.get('voucher_no')}")

	return {"done": done, "failed": failed}


# ─── Discoverability ──────────────────────────────────────────────────────────

TOOL = "SF Payment Unreconciliation"
HOSTS = ("Supplier Payments", "Receivables", "Payables")


def ensure_workspace_links():
	"""Put the tool where an accountant already looks: beside Payment Reconciliation.

	Added to this app's own Supplier Payments workspace and to erpnext's Receivables and
	Payables, because that is where the forward tool lives. Run from after_migrate rather than
	shipped as a fixture: erpnext re-syncs its own workspaces on migrate and would drop a
	hand-added link, so this re-adds it every time and skips whatever is already there.
	"""
	label = "Payment Unreconciliation"
	for name in HOSTS:
		if not frappe.db.exists("Workspace", name):
			continue
		try:
			ws = frappe.get_doc("Workspace", name)
			changed = False
			if not any(s.link_to == TOOL for s in ws.shortcuts):
				ws.append("shortcuts", {"label": label, "type": "DocType", "link_to": TOOL})
				changed = True

			# A shortcut row alone renders NOTHING. The desk lays a workspace out from `content`,
			# a JSON list of blocks, and a shortcut only appears if a block names it -- matched on
			# the child row's LABEL, not its link. Three workspaces carried the row for a week and
			# showed no shortcut, so the tool was reachable only by typing its URL.
			blocks = json.loads(ws.content or "[]")
			if not any(b.get("type") == "shortcut"
			           and (b.get("data") or {}).get("shortcut_name") == label for b in blocks):
				last = max((i for i, b in enumerate(blocks) if b.get("type") == "shortcut"),
				           default=-1)
				blocks.insert(last + 1, {"id": frappe.generate_hash(length=10), "type": "shortcut",
				                         "data": {"shortcut_name": label, "col": 3}})
				ws.content = json.dumps(blocks)
				changed = True

			if changed:
				ws.save(ignore_permissions=True)
		except Exception:
			frappe.log_error(frappe.get_traceback(),
			                 f"sf_trading: could not add the unreconciliation link to {name}")
