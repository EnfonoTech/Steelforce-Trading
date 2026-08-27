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
from frappe.utils import flt, getdate

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
			abs(sum(adv.amount)) as allocated_amount
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
			abs(sum(ple.amount)) as allocated_amount
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

	advance_doctypes = set(frappe.get_hooks("advance_payment_doctypes") or [])
	closed = closed_period_date(company)
	for row in rows:
		row["entry_type"] = (ORDER_ADVANCE if row["against_voucher_type"] in advance_doctypes
		                     else INVOICE_ALLOCATION)
		# an order has no outstanding; what matters there is how much advance it is carrying
		if row["entry_type"] == ORDER_ADVANCE:
			row["outstanding_amount"] = flt(frappe.db.get_value(
				row["against_voucher_type"], row["against_voucher_no"], "advance_paid"))
		else:
			row["outstanding_amount"] = (
				flt(frappe.db.get_value(row["against_voucher_type"], row["against_voucher_no"],
				                        "outstanding_amount"))
				if _has_outstanding(row["against_voucher_type"]) else 0.0
			)
		row["in_closed_period"] = bool(closed and getdate(row["posting_date"]) <= getdate(closed))
	return rows


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
