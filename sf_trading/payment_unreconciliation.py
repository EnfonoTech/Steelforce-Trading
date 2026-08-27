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
from frappe.utils import add_days, cint, flt, getdate, nowdate

PAYMENT_TYPES = ("Payment Entry", "Journal Entry")


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
                    dimensions=None, limit=500):
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

	having = ["allocated_amount > 0.005"]
	if minimum_amount:
		having.append("allocated_amount >= %(minimum_amount)s")
		values["minimum_amount"] = flt(minimum_amount)
	if maximum_amount:
		having.append("allocated_amount <= %(maximum_amount)s")
		values["maximum_amount"] = flt(maximum_amount)
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
                       against_voucher_no=None, limit=500, dimensions=None):
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
		"payment_types": list(PAYMENT_TYPES),
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

	having = ["allocated_amount > 0.005"]
	if minimum_amount:
		having.append("allocated_amount >= %(minimum_amount)s")
		values["minimum_amount"] = flt(minimum_amount)
	if maximum_amount:
		having.append("allocated_amount <= %(maximum_amount)s")
		values["maximum_amount"] = flt(maximum_amount)

	values["limit"] = min(int(limit or 500), 2000)

	rows = frappe.db.sql(
		f"""
		select
			ple.company, ple.voucher_type, ple.voucher_no,
			ple.against_voucher_type, ple.against_voucher_no,
			ple.account, ple.party_type, ple.party, ple.account_currency as currency,
			max(ple.posting_date) as posting_date,
			abs(sum(ple.amount)) as allocated_amount,
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

RISK = "risk"
FRESH = "fresh"
PLAIN = "plain"


def _leg_counts(company, party_type, party):
	"""How many invoices each payment is spread across.

	This is the difference between one accounting event and eleven rows that look like eleven
	events. Two set-based queries for the whole page, whatever its size.
	"""
	values = {"company": company, "party_type": party_type, "party": party}
	counts = {}
	for r in frappe.db.sql("""
		select voucher_no, count(distinct against_voucher_no) as legs
		from `tabPayment Ledger Entry`
		where delinked = 0 and company = %(company)s and party_type = %(party_type)s
		  and party = %(party)s and voucher_no != against_voucher_no
		group by voucher_no
	""", values, as_dict=True):
		counts[r.voucher_no] = cint(r.legs)

	for r in frappe.db.sql("""
		select adv.voucher_no, count(distinct adv.against_voucher_no) as legs
		from `tabAdvance Payment Ledger Entry` adv
		join `tabPayment Entry` pe on pe.name = adv.voucher_no
		where adv.delinked = 0 and adv.event = 'Submit' and adv.company = %(company)s
		  and pe.party_type = %(party_type)s and pe.party = %(party)s
		group by adv.voucher_no
	""", values, as_dict=True):
		counts[r.voucher_no] = counts.get(r.voucher_no, 0) + cint(r.legs)
	return counts


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
		for doc in frappe.get_all(doctype, filters={"name": ("in", list(names))},
		                          fields=fields, limit_page_length=0):
			context[(doctype, doc["name"])] = doc
	return context


def _applied_totals(rows):
	"""Everything currently applied to each target, from every payment and party.

	Needed to say "more is applied to this invoice than the invoice is worth", which is the one
	arithmetic fact a clerk cannot work out by eye from a list of legs.
	"""
	targets = list({row["against_voucher_no"] for row in rows})
	if not targets:
		return {}
	totals = {}
	for chunk_start in range(0, len(targets), 900):
		chunk = targets[chunk_start:chunk_start + 900]
		for r in frappe.db.sql("""
			select against_voucher_no, abs(sum(amount)) as applied
			from `tabPayment Ledger Entry`
			where delinked = 0 and voucher_no != against_voucher_no
			  and against_voucher_no in %(targets)s
			group by against_voucher_no
		""", {"targets": chunk}, as_dict=True):
			totals[r.against_voucher_no] = flt(r.applied)
	return totals


def annotate(rows, company, party_type, party):
	"""Fill in provenance, the target's own figures, and the observation flags; then order the
	page so the rows worth a second look are the ones the eye lands on first."""
	if not rows:
		return rows

	advance_doctypes = set(frappe.get_hooks("advance_payment_doctypes") or [])
	closed = closed_period_date(company)
	legs = _leg_counts(company, party_type, party)
	context = _target_context(rows)
	applied = _applied_totals(rows)
	fresh_after = add_days(getdate(nowdate()), -RECENT_DAYS)

	for row in rows:
		row["entry_type"] = (ORDER_ADVANCE if row["against_voucher_type"] in advance_doctypes
		                     else INVOICE_ALLOCATION)
		target = context.get((row["against_voucher_type"], row["against_voucher_no"])) or {}

		row["leg_count"] = cint(legs.get(row["voucher_no"])) or 1
		row["target_date"] = target.get("target_date")
		row["target_total"] = flt(target.get("target_total"))
		row["target_status"] = target.get("target_status")
		row["target_branch"] = target.get("target_branch")
		row["applied_total"] = flt(applied.get(row["against_voucher_no"]))
		row["in_closed_period"] = bool(closed and getdate(row["posting_date"]) <= getdate(closed))

		# an order has no outstanding; what matters there is how much advance it is carrying
		if row["entry_type"] == ORDER_ADVANCE:
			row["outstanding_amount"] = flt(target.get("target_advance_paid"))
		else:
			row["outstanding_amount"] = flt(target.get("target_outstanding"))

		notes, worth_a_look = [], False

		if row["in_closed_period"]:
			notes.append(_("Closed period"))
			worth_a_look = True
		if cint(target.get("docstatus")) == 2:
			notes.append(_("Applied to a cancelled document"))
			worth_a_look = True
		if row.get("payment_branch") and row["target_branch"] \
				and row["payment_branch"] != row["target_branch"]:
			notes.append(_("Different branch"))
			worth_a_look = True
		if (row["entry_type"] == INVOICE_ALLOCATION and row["target_date"]
				and getdate(row["posting_date"]) < getdate(row["target_date"])):
			notes.append(_("Paid before the invoice date"))
			worth_a_look = True
		if row["target_total"] and row["applied_total"] > row["target_total"] + 0.005:
			notes.append(_("More applied than the document is worth"))
			worth_a_look = True
		if row["allocated_amount"] < DUST_AMOUNT:
			notes.append(_("Rounding-size"))

		# provenance: the single most useful thing on the row. "Somebody did this on Tuesday" is
		# what separates a mistake from four months of settled history.
		recent = bool(row.get("allocated_on") and getdate(row["allocated_on"]) >= fresh_after)
		if recent:
			notes.append(_("New"))
		if row["leg_count"] >= BULK_LEGS:
			notes.append(_("1 of {0} in this voucher").format(row["leg_count"]))
		if row["entry_type"] == ORDER_ADVANCE:
			notes.append(_("Order advance"))

		row["severity"] = RISK if worth_a_look else (FRESH if recent else PLAIN)
		row["insight"] = " · ".join(notes)
		row["allocated_by"] = _short_user(row.get("allocated_by"))

	order = {RISK: 0, FRESH: 1, PLAIN: 2}
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
		return _("Only {0} can be unreconciled").format(" and ".join(PAYMENT_TYPES))

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
	for name in HOSTS:
		if not frappe.db.exists("Workspace", name):
			continue
		try:
			ws = frappe.get_doc("Workspace", name)
			if any(s.link_to == TOOL for s in ws.shortcuts):
				continue
			ws.append("shortcuts", {"label": "Payment Unreconciliation", "type": "DocType",
			                        "link_to": TOOL})
			ws.save(ignore_permissions=True)
		except Exception:
			frappe.log_error(frappe.get_traceback(),
			                 f"sf_trading: could not add the unreconciliation link to {name}")
