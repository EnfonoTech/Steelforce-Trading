# Copyright (c) 2025, sf_trading and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.utils import add_days, flt, getdate


def execute(filters=None):
	columns = get_columns()
	data = get_data(filters)
	return columns, data


def _show_margin():
	"""Show Gross Margin column only to System Manager."""
	return "System Manager" in frappe.get_roles()


def enforce_user_cost_center(cost_center):
	"""A user restricted to exactly one Cost Center via User Permission always
	sees only that cost center's data, even with no filter selected."""
	if cost_center:
		return cost_center
	from frappe.core.doctype.user_permission.user_permission import get_user_permissions
	perms = (get_user_permissions() or {}).get("Cost Center") or []
	allowed = list({p.get("doc") for p in perms if p.get("doc")})
	if len(allowed) == 1:
		return allowed[0]
	return cost_center


def get_columns():
	cols = [
		_("Particulars") + ":Data:300",
		_("Income") + ":Currency:120",
		_("Expense") + ":Currency:120",
		_("Total Discount/Adj.") + ":Currency:150",
	]
	if _show_margin():
		cols.append(_("Gross Margin") + ":Currency:120")
	return cols


def get_report_link(label, report_type, from_date_str, to_date_str, company, cost_center):
	"""Create a clickable link to DCR Detail report with the given type and filters."""
	from frappe.utils import get_url
	from urllib.parse import urlencode, quote
	query_params = {
		"report_type": report_type,
		"from_date": from_date_str or "",
		"to_date": to_date_str or "",
		"company": company or "",
		"cost_center": cost_center or "",
	}
	query_string = urlencode({k: v for k, v in query_params.items() if v})
	report_path = quote("DCR Detail", safe="")
	url = get_url(uri=f"/app/query-report/{report_path}?{query_string}")
	return f'<a href="{url}">{label}</a>'


# ═══════════════════════════════════════════════════════════════════════════════
# Settlement-based classification (shared with DCR Detail)
#
# Sales lines follow the money actually received ON/BEFORE the invoice date:
#   Cash-type mode          → CASH SALES
#   Cheque mode (for_pdc)   → CHEQUE SALES (even though MoP type is Bank)
#   any other mode          → BANK SALES
# The unsettled remainder → CREDIT SALES, or Home Credit when a driver is set.
# Split payments land proportionally in each line.
# ═══════════════════════════════════════════════════════════════════════════════

def _mode_is_cheque(alias):
	"""SQL condition: the mode of payment on `alias` is a cheque/PDC mode.
	Cheque modes are flagged for_pdc in Branch Configuration; name match is a
	fallback for unconfigured modes."""
	return (
		"(EXISTS (SELECT 1 FROM `tabBranch Configuration Mode of Payment` {a}_pdc"
		" WHERE {a}_pdc.mode_of_payment = {a}.mode_of_payment AND {a}_pdc.for_pdc = 1)"
		" OR LOWER(COALESCE({a}.mode_of_payment, '')) LIKE '%%cheque%%'"
		" OR LOWER(COALESCE({a}.mode_of_payment, '')) LIKE '%%chq%%')"
	).format(a=alias)


def _settled_alloc_subquery(prefix, kind):
	"""Amount settled against the invoice on/before its posting date via the given
	kind of mode ('cash', 'bank' or 'cheque'), from Payment Entries plus the
	invoice's own POS payment rows."""
	p = prefix
	pe_cheque = _mode_is_cheque(f"{p}_pe")
	sip_cheque = _mode_is_cheque(f"{p}_sip")
	if kind == "cash":
		pe_mode = f"({p}_mop.type = 'Cash' AND NOT {pe_cheque})"
		sip_mode = f"({p}_sipm.type = 'Cash' AND NOT {sip_cheque})"
	elif kind == "bank":
		pe_mode = f"(COALESCE({p}_mop.type, '') != 'Cash' AND NOT {pe_cheque})"
		sip_mode = f"(COALESCE({p}_sipm.type, '') != 'Cash' AND NOT {sip_cheque})"
	else:
		pe_mode = pe_cheque
		sip_mode = sip_cheque
	return f"""((
		SELECT COALESCE(SUM({p}_per.allocated_amount), 0)
		FROM `tabPayment Entry Reference` {p}_per
		INNER JOIN `tabPayment Entry` {p}_pe ON {p}_pe.name = {p}_per.parent AND {p}_pe.docstatus = 1 AND {p}_pe.payment_type = 'Receive'
		LEFT JOIN `tabMode of Payment` {p}_mop ON {p}_mop.name = {p}_pe.mode_of_payment
		WHERE {p}_per.reference_doctype = 'Sales Invoice' AND {p}_per.reference_name = si.name
			AND {p}_pe.posting_date <= si.posting_date
			AND {pe_mode}
	) + (
		SELECT COALESCE(SUM({p}_sip.base_amount), 0)
		FROM `tabSales Invoice Payment` {p}_sip
		LEFT JOIN `tabMode of Payment` {p}_sipm ON {p}_sipm.name = {p}_sip.mode_of_payment
		WHERE {p}_sip.parent = si.name AND {p}_sip.parenttype = 'Sales Invoice'
			AND {sip_mode}
	))"""


SETTLED_CASH_ALLOC_SUBQUERY = _settled_alloc_subquery("sc", "cash")
SETTLED_BANK_ALLOC_SUBQUERY = _settled_alloc_subquery("sb", "bank")
SETTLED_CHEQUE_ALLOC_SUBQUERY = _settled_alloc_subquery("sq", "cheque")

# Return that was actually refunded (money out) on/before the return's posting
# date: a Pay-type Payment Entry against the return, or POS refund rows on it.
# Follows the money on the return itself — the original invoice's settlement is
# irrelevant (a refunded return is a cash return even if the sale was on credit).
RETURN_REFUNDED_CONDITION = """(
	EXISTS (
		SELECT 1 FROM `tabPayment Entry Reference` ret_per
		INNER JOIN `tabPayment Entry` ret_pe ON ret_pe.name = ret_per.parent AND ret_pe.docstatus = 1 AND ret_pe.payment_type = 'Pay'
		WHERE ret_per.reference_doctype = 'Sales Invoice' AND ret_per.reference_name = si.name
			AND ret_pe.posting_date <= si.posting_date
	)
	OR EXISTS (
		SELECT 1 FROM `tabSales Invoice Payment` ret_sip
		WHERE ret_sip.parent = si.name AND ret_sip.parenttype = 'Sales Invoice'
			AND ret_sip.base_amount != 0
	)
)"""


def get_invoices_with_settlement(from_date, to_date, company, cost_center):
	"""All sales invoices in range with the amounts settled on/before the invoice
	date per mode kind (cash/bank/cheque). Shared by the summary and DCR Detail."""
	conditions = "si.docstatus = 1 AND si.is_return = 0"
	if from_date:
		conditions += " AND si.posting_date >= %(from_date)s"
	if to_date:
		conditions += " AND si.posting_date <= %(to_date)s"
	if company:
		conditions += " AND si.company = %(company)s"
	cost_center_condition = " AND sii.cost_center = %(cost_center)s" if cost_center else ""
	return frappe.db.sql("""
		SELECT
			si.name,
			si.posting_date,
			si.customer,
			si.customer_name,
			si.custom_driver,
			si.base_net_total,
			si.total_taxes_and_charges as vat_amount,
			si.base_grand_total,
			si.grand_total,
			si.discount_amount as discount,
			SUM(COALESCE(sii.incoming_rate, 0) * sii.stock_qty) as cost,
			{cash_alloc} as cash_alloc,
			{bank_alloc} as bank_alloc,
			{cheque_alloc} as cheque_alloc
		FROM `tabSales Invoice` si
		LEFT JOIN `tabSales Invoice Item` sii ON sii.parent = si.name
		WHERE {conditions}
			{cost_center_condition}
		GROUP BY si.name, si.posting_date, si.customer, si.customer_name, si.custom_driver,
			si.base_net_total, si.total_taxes_and_charges, si.base_grand_total, si.grand_total,
			si.discount_amount
		ORDER BY si.posting_date, si.name
	""".format(cash_alloc=SETTLED_CASH_ALLOC_SUBQUERY, bank_alloc=SETTLED_BANK_ALLOC_SUBQUERY,
		cheque_alloc=SETTLED_CHEQUE_ALLOC_SUBQUERY, conditions=conditions,
		cost_center_condition=cost_center_condition), {
		"from_date": from_date,
		"to_date": to_date,
		"company": company,
		"cost_center": cost_center,
	}, as_dict=True)


def split_invoice_settlement(r):
	"""Split one invoice's grand total into settled cash/bank/cheque portions and
	the unsettled remainder. Over-allocation (e.g. write-off riding on the last
	Payment Entry) is scaled back so portions never exceed the invoice total."""
	total = flt(r.base_grand_total)
	if total <= 0:
		return {"cash": 0, "bank": 0, "cheque": 0, "remainder": 0, "total": total}
	cash, bank, cheque = flt(r.cash_alloc), flt(r.bank_alloc), flt(r.cheque_alloc)
	settled = cash + bank + cheque
	if settled > total:
		factor = total / settled
		cash, bank, cheque = cash * factor, bank * factor, cheque * factor
		settled = total
	remainder = total - settled
	if remainder < 0.005:
		remainder = 0
	return {"cash": cash, "bank": bank, "cheque": cheque, "remainder": remainder, "total": total}


def get_sales_buckets(from_date, to_date, company, cost_center):
	"""Aggregate the per-invoice settlement splits into the report's sales rows."""
	buckets = {
		key: {"net_total": 0, "vat_amount": 0, "discount": 0, "cost": 0}
		for key in ("cash", "bank", "cheque", "credit", "home_credit")
	}
	for r in get_invoices_with_settlement(from_date, to_date, company, cost_center):
		s = split_invoice_settlement(r)
		if s["total"] <= 0:
			continue
		remainder_bucket = "home_credit" if (r.custom_driver or "").strip() else "credit"
		shares = {
			"cash": s["cash"] / s["total"],
			"bank": s["bank"] / s["total"],
			"cheque": s["cheque"] / s["total"],
			remainder_bucket: s["remainder"] / s["total"],
		}
		for bucket_key, share in shares.items():
			if share <= 0:
				continue
			b = buckets[bucket_key]
			b["net_total"] += flt(r.base_net_total) * share
			b["vat_amount"] += flt(r.vat_amount) * share
			b["discount"] += flt(r.discount) * share
			b["cost"] += flt(r.cost) * share
	return buckets


def get_data(filters):
	data = []

	if not filters.get("from_date") or not filters.get("to_date"):
		frappe.throw(_("Please select From Date and To Date"))

	from_date = getdate(filters.get("from_date"))
	to_date = getdate(filters.get("to_date"))
	company = filters.get("company")
	cost_center = enforce_user_cost_center(filters.get("cost_center"))

	from_date_str = from_date.strftime("%Y-%m-%d")
	to_date_str = to_date.strftime("%Y-%m-%d")

	# Opening/Closing straight from the General Ledger cash accounts so both
	# balances always tie to the branch petty cash ledger (Journal Entries,
	# POS payments and Internal Transfers are included automatically)
	opening_balance = get_gl_cash_balance(add_days(from_date, -1), company, cost_center)
	closing_balance = get_gl_cash_balance(to_date, company, cost_center)

	buckets = get_sales_buckets(from_date, to_date, company, cost_center)

	def bucket_income(b):
		return b["net_total"] + b["vat_amount"]

	def bucket_margin(b):
		return b["net_total"] - b["cost"]

	cash_b = buckets["cash"]
	bank_b = buckets["bank"]
	cheque_b = buckets["cheque"]
	credit_b = buckets["credit"]
	home_b = buckets["home_credit"]

	total_sales_income = sum(bucket_income(b) for b in buckets.values())
	total_sales_margin = sum(bucket_margin(b) for b in buckets.values())
	total_sales_discount = sum(b["discount"] for b in buckets.values())

	sales_return_data = get_sales_returns(from_date, to_date, company, cost_center, refunded=True)
	sales_return_cash = sales_return_data["net_total"] + sales_return_data["vat_amount"]

	credit_return_data = get_sales_returns(from_date, to_date, company, cost_center, refunded=False)
	credit_return_total = credit_return_data["net_total"] + credit_return_data["vat_amount"]

	credit_purchase = get_credit_purchases(from_date, to_date, company, cost_center).get("total_with_vat", 0)

	cash_received_credit_sales = get_cash_received_credit_sales(from_date, to_date, company, cost_center)
	cash_receipts_pos = (
		get_cash_receipts_from_pos(from_date, to_date, company, cost_center)
		+ get_pos_cash_receipts(from_date, to_date, company, cost_center)
	)

	petty_cash_approved = get_petty_cash_payments(from_date, to_date, company, cost_center, docstatus=1)
	petty_cash_unapproved = get_petty_cash_payments(from_date, to_date, company, cost_center, docstatus=0)

	write_off_total = get_write_off_total(from_date, to_date, company, cost_center)

	def _row(particulars, income, expense, discount_adj, margin=0):
		row = [particulars, income, expense, discount_adj]
		if _show_margin():
			row.append(margin)
		return row

	def _link(label, report_type):
		return get_report_link(label, report_type, from_date_str, to_date_str, company, cost_center)

	data.append(_row("<b>" + _link(_("Opening Cash Balance"), "Opening Cash Balance") + "</b>", opening_balance, 0, 0, 0))
	data.append(_row("<b>" + _link(_("Total Sales"), "Total Sales") + "</b>", total_sales_income, 0, -total_sales_discount, total_sales_margin))
	data.append(_row(_link("CASH SALES", "Cash Sales"), bucket_income(cash_b), 0, -cash_b["discount"], bucket_margin(cash_b)))
	data.append(_row(_link("BANK SALES", "Bank Sales"), bucket_income(bank_b), 0, -bank_b["discount"], bucket_margin(bank_b)))
	data.append(_row(_link("CHEQUE SALES", "Cheque Sales"), bucket_income(cheque_b), 0, -cheque_b["discount"], bucket_margin(cheque_b)))
	data.append(_row(_link("CREDIT SALES", "Credit Sales"), bucket_income(credit_b), credit_return_total, -credit_b["discount"], bucket_margin(credit_b)))
	data.append(_row(_link("Home Credit (Delivery)", "Home Credit (Delivery)"), bucket_income(home_b), 0, -home_b["discount"], bucket_margin(home_b)))
	data.append(_row(_link("Sales Return - Cash", "Sales Return - Cash"), 0, sales_return_cash, 0, 0))
	data.append(_row(_link("VAT Collected on Cash Sales", "VAT Collected on Cash Sales"), cash_b["vat_amount"], 0, 0, 0))
	data.append(_row(_link("VAT Collected on Bank Sales", "VAT Collected on Bank Sales"), bank_b["vat_amount"], 0, 0, 0))
	data.append(_row(_link("VAT Collected on Cheque Sales", "VAT Collected on Cheque Sales"), cheque_b["vat_amount"], 0, 0, 0))
	data.append(_row(_link("VAT Applied on Credit Sales", "VAT Applied on Credit Sales"), credit_b["vat_amount"], credit_return_data["vat_amount"], 0, 0))
	data.append(_row(_link("VAT Applied on Home Credit", "VAT Applied on Home Credit"), home_b["vat_amount"], 0, 0, 0))
	data.append(_row(_link("VAT Refund on Sales Return", "VAT Refund on Sales Return"), 0, sales_return_data["vat_amount"], 0, 0))
	data.append(_row(_link("Loyalty / Write Off", "Loyalty / Write Off"), 0, write_off_total, 0, 0))
	data.append(_row(_link("Credit Purchase - DIRECT PURCHASE", "Credit Purchase - DIRECT PURCHASE"), 0, credit_purchase, 0, 0))
	data.append(_row(_link("Cash Received : Credit Sales", "Cash Received : Credit Sales"), cash_received_credit_sales, 0, 0, 0))
	data.append(_row(_link("Payments-Petty Cash (Approved)", "Payments-Petty Cash (Approved)"), 0, petty_cash_approved, 0, 0))
	data.append(_row(_link("Payments-Petty Cash (UnApproved)", "Payments-Petty Cash (UnApproved)"), 0, petty_cash_unapproved, 0, 0))
	data.append(_row("<b>" + _link(_("Total Receipt-Petty Cash"), "Total Receipt-Petty Cash") + "</b>", cash_receipts_pos + cash_received_credit_sales, 0, 0, 0))
	data.append(_row("<b>" + _link(_("Closing Cash Balance"), "Closing Cash Balance") + "</b>", closing_balance, 0, 0, 0))

	return data


def get_branch_petty_cash_accounts(company, cost_center):
	"""Petty cash accounts of the branch(es) mapped to this cost center, derived
	from Branch Configuration: the default account (per company) of the branch's
	Cash-type Modes of Payment. Branches always have their own cash MoP, and its
	default account is exactly where the payment popup posts the branch's cash."""
	if not cost_center:
		return []
	conditions = "bcc.cost_center = %(cost_center)s AND COALESCE(mopa.default_account, '') != ''"
	if company:
		conditions += " AND mopa.company = %(company)s AND (bc.company = %(company)s OR COALESCE(bc.company, '') = '')"
	else:
		conditions += " AND (mopa.company = bc.company OR COALESCE(bc.company, '') = '')"
	rows = frappe.db.sql("""
		SELECT DISTINCT mopa.default_account
		FROM `tabBranch Configuration` bc
		INNER JOIN `tabBranch Configuration Cost Center` bcc
			ON bcc.parent = bc.name AND bcc.parenttype = 'Branch Configuration'
		INNER JOIN `tabBranch Configuration Mode of Payment` bcm
			ON bcm.parent = bc.name AND bcm.parenttype = 'Branch Configuration'
		INNER JOIN `tabMode of Payment` mop ON mop.name = bcm.mode_of_payment AND mop.type = 'Cash'
		INNER JOIN `tabMode of Payment Account` mopa ON mopa.parent = mop.name
		WHERE {conditions}
	""".format(conditions=conditions), {"company": company, "cost_center": cost_center})
	return [r[0] for r in rows]


def get_gl_cash_balance(as_of_date, company, cost_center):
	"""Opening/Closing Cash Balance from the General Ledger.

	When the cost center maps to a branch with a Petty Cash Account configured
	(Branch Configuration), the balance is that account's full GL balance with
	NO cost center condition — the account is branch-specific by definition, so
	journal entries with a wrong or missing cost center still count, and other
	cash accounts that merely share the cost center are excluded.

	Fallback (no Petty Cash Account configured, or no cost center filter):
	all Cash-type accounts, filtered by cost center when given."""
	petty_cash_accounts = get_branch_petty_cash_accounts(company, cost_center)

	conditions = "gle.is_cancelled = 0 AND gle.posting_date <= %(as_of_date)s"
	if company:
		conditions += " AND gle.company = %(company)s"

	params = {
		"as_of_date": as_of_date,
		"company": company,
		"cost_center": cost_center,
		"accounts": tuple(petty_cash_accounts) or ("",),
	}

	if petty_cash_accounts:
		result = frappe.db.sql("""
			SELECT COALESCE(SUM(gle.debit - gle.credit), 0) as balance
			FROM `tabGL Entry` gle
			WHERE {conditions}
				AND gle.account IN %(accounts)s
		""".format(conditions=conditions), params, as_dict=True)
	else:
		cost_center_condition = " AND gle.cost_center = %(cost_center)s" if cost_center else ""
		result = frappe.db.sql("""
			SELECT COALESCE(SUM(gle.debit - gle.credit), 0) as balance
			FROM `tabGL Entry` gle
			INNER JOIN `tabAccount` acc ON acc.name = gle.account
			WHERE {conditions}
				AND acc.account_type = 'Cash'
				{cost_center_condition}
		""".format(conditions=conditions, cost_center_condition=cost_center_condition), params, as_dict=True)
	return flt(result[0].balance) if result else 0


def get_sales_returns(from_date, to_date, company, cost_center, refunded=True):
	"""Sales returns split by what happened on the return itself: refunded
	returns (money out by the return date) are cash returns (Sales Return - Cash
	row); unrefunded returns only offset the customer's receivable and show as
	expense on the CREDIT SALES row. Together the two cover every return."""
	conditions = "si.docstatus = 1 AND si.is_return = 1"
	if from_date:
		conditions += " AND si.posting_date >= %(from_date)s"
	if to_date:
		conditions += " AND si.posting_date <= %(to_date)s"
	if company:
		conditions += " AND si.company = %(company)s"
	cost_center_condition = " AND EXISTS (SELECT 1 FROM `tabSales Invoice Item` sii WHERE sii.parent = si.name AND sii.cost_center = %(cost_center)s)" if cost_center else ""
	return_condition = RETURN_REFUNDED_CONDITION if refunded else "NOT " + RETURN_REFUNDED_CONDITION
	result = frappe.db.sql("""
		SELECT SUM(ABS(si.base_net_total)) as net_total, SUM(ABS(si.total_taxes_and_charges)) as vat_amount
		FROM `tabSales Invoice` si
		WHERE {conditions}
			AND {return_condition}
			{cost_center_condition}
	""".format(conditions=conditions, return_condition=return_condition,
		cost_center_condition=cost_center_condition), {
		"from_date": from_date,
		"to_date": to_date,
		"company": company,
		"cost_center": cost_center,
	}, as_dict=True)
	if result and (result[0].net_total or result[0].vat_amount):
		return {
			"net_total": flt(result[0].net_total),
			"vat_amount": flt(result[0].vat_amount),
		}
	return {"net_total": 0, "vat_amount": 0}


def get_credit_purchases(from_date, to_date, company, cost_center):
	"""Get credit purchases (Purchase Invoices - all invoices regardless of payment status)
	Returns total including VAT"""
	conditions = "pi.docstatus = 1"
	if from_date:
		conditions += " AND pi.posting_date >= %(from_date)s"
	if to_date:
		conditions += " AND pi.posting_date <= %(to_date)s"
	if company:
		conditions += " AND pi.company = %(company)s"

	cost_center_condition = ""
	if cost_center:
		cost_center_condition = " AND pii.cost_center = %(cost_center)s"

	result = frappe.db.sql("""
		SELECT
			SUM(pi.net_total) as net_total,
			SUM(pi.total_taxes_and_charges) as vat_amount
		FROM `tabPurchase Invoice` pi
		LEFT JOIN `tabPurchase Invoice Item` pii ON pii.parent = pi.name
		WHERE {conditions}
			{cost_center_condition}
		GROUP BY pi.name
	""".format(conditions=conditions, cost_center_condition=cost_center_condition), {
		"from_date": from_date,
		"to_date": to_date,
		"company": company,
		"cost_center": cost_center
	}, as_dict=True)

	if result:
		total_net = sum([flt(r.net_total) for r in result if r.net_total])
		total_vat = sum([flt(r.vat_amount) for r in result if r.vat_amount])
		total_with_vat = total_net + total_vat
		return {
			"net_total": total_net,
			"vat_amount": total_vat,
			"total_with_vat": total_with_vat
		}
	return {"net_total": 0, "vat_amount": 0, "total_with_vat": 0}


def get_cash_receipts_from_pos(from_date, to_date, company, cost_center):
	"""Cash received (Payment Entry, Cash mode) on/before the invoice date —
	the till receipts of same-day (cash) sales."""
	conditions = "pe.docstatus = 1 AND pe.payment_type = 'Receive'"
	if from_date:
		conditions += " AND pe.posting_date >= %(from_date)s"
	if to_date:
		conditions += " AND pe.posting_date <= %(to_date)s"
	if company:
		conditions += " AND pe.company = %(company)s"
	cost_center_condition = " AND pe.cost_center = %(cost_center)s" if cost_center else ""
	result = frappe.db.sql("""
		SELECT SUM(per.allocated_amount) as amount
		FROM `tabPayment Entry` pe
		INNER JOIN `tabPayment Entry Reference` per ON per.parent = pe.name
		INNER JOIN `tabMode of Payment` mop ON mop.name = pe.mode_of_payment
		INNER JOIN `tabSales Invoice` si ON si.name = per.reference_name AND per.reference_doctype = 'Sales Invoice'
		WHERE {conditions}
			AND per.reference_doctype = 'Sales Invoice'
			AND mop.type = 'Cash'
			AND pe.posting_date <= si.posting_date
			{cost_center_condition}
	""".format(conditions=conditions, cost_center_condition=cost_center_condition), {
		"from_date": from_date,
		"to_date": to_date,
		"company": company,
		"cost_center": cost_center,
	}, as_dict=True)
	if result and result[0].amount:
		return flt(result[0].amount)
	return 0


def get_pos_cash_receipts(from_date, to_date, company, cost_center):
	"""Cash collected directly on POS-style invoices (rows in the invoice's own
	payments table, Cash mode). These have no Payment Entry, so they are counted
	here to keep the till receipts complete."""
	conditions = "si.docstatus = 1 AND si.is_return = 0"
	if from_date:
		conditions += " AND si.posting_date >= %(from_date)s"
	if to_date:
		conditions += " AND si.posting_date <= %(to_date)s"
	if company:
		conditions += " AND si.company = %(company)s"
	cost_center_condition = " AND EXISTS (SELECT 1 FROM `tabSales Invoice Item` sii WHERE sii.parent = si.name AND sii.cost_center = %(cost_center)s)" if cost_center else ""
	result = frappe.db.sql("""
		SELECT SUM(sip.base_amount) as amount
		FROM `tabSales Invoice` si
		INNER JOIN `tabSales Invoice Payment` sip ON sip.parent = si.name
		INNER JOIN `tabMode of Payment` mop ON mop.name = sip.mode_of_payment
		WHERE {conditions}
			AND mop.type = 'Cash'
			{cost_center_condition}
	""".format(conditions=conditions, cost_center_condition=cost_center_condition), {
		"from_date": from_date,
		"to_date": to_date,
		"company": company,
		"cost_center": cost_center,
	}, as_dict=True)
	if result and result[0].amount:
		return flt(result[0].amount)
	return 0


def get_cash_received_credit_sales(from_date, to_date, company, cost_center):
	"""Cash received (Payment Entry, Cash mode) after the invoice date —
	collections against credit and home-credit sales."""
	conditions = "pe.docstatus = 1 AND pe.payment_type = 'Receive'"
	if from_date:
		conditions += " AND pe.posting_date >= %(from_date)s"
	if to_date:
		conditions += " AND pe.posting_date <= %(to_date)s"
	if company:
		conditions += " AND pe.company = %(company)s"
	cost_center_condition = " AND pe.cost_center = %(cost_center)s" if cost_center else ""
	result = frappe.db.sql("""
		SELECT SUM(per.allocated_amount) as amount
		FROM `tabPayment Entry` pe
		INNER JOIN `tabPayment Entry Reference` per ON per.parent = pe.name
		INNER JOIN `tabMode of Payment` mop ON mop.name = pe.mode_of_payment
		INNER JOIN `tabSales Invoice` si ON si.name = per.reference_name AND per.reference_doctype = 'Sales Invoice'
		WHERE {conditions}
			AND per.reference_doctype = 'Sales Invoice'
			AND mop.type = 'Cash'
			AND si.posting_date < pe.posting_date
			{cost_center_condition}
	""".format(conditions=conditions, cost_center_condition=cost_center_condition), {
		"from_date": from_date,
		"to_date": to_date,
		"company": company,
		"cost_center": cost_center,
	}, as_dict=True)
	if result and result[0].amount:
		return flt(result[0].amount)
	return 0


def get_petty_cash_payments(from_date, to_date, company, cost_center, docstatus=1):
	"""Petty cash payments: Payment Entry (Pay, Cash mode, party type Supplier —
	with or without invoice references), paid Purchase Invoices (is_paid, Cash
	mode), and Journal Entries crediting the branch petty cash account.
	docstatus 1 = approved (submitted), 0 = unapproved (draft/pending)."""
	conditions = "pe.docstatus = %(docstatus)s"
	if from_date:
		conditions += " AND pe.posting_date >= %(from_date)s"
	if to_date:
		conditions += " AND pe.posting_date <= %(to_date)s"
	if company:
		conditions += " AND pe.company = %(company)s"
	cost_center_condition = " AND pe.cost_center = %(cost_center)s" if cost_center else ""

	params = {
		"from_date": from_date,
		"to_date": to_date,
		"company": company,
		"cost_center": cost_center,
		"docstatus": docstatus,
	}

	payments_pe_result = frappe.db.sql("""
		SELECT SUM(pe.paid_amount) as amount
		FROM `tabPayment Entry` pe
		INNER JOIN `tabMode of Payment` mop ON mop.name = pe.mode_of_payment
		WHERE {conditions}
			AND pe.payment_type = 'Pay'
			AND pe.party_type = 'Supplier'
			AND mop.type = 'Cash'
			{cost_center_condition}
	""".format(conditions=conditions, cost_center_condition=cost_center_condition), params, as_dict=True)

	pi_conditions = "pi.docstatus = %(docstatus)s AND pi.is_paid = 1"
	if from_date:
		pi_conditions += " AND pi.posting_date >= %(from_date)s"
	if to_date:
		pi_conditions += " AND pi.posting_date <= %(to_date)s"
	if company:
		pi_conditions += " AND pi.company = %(company)s"
	pi_cost_center_condition = " AND pi.cost_center = %(cost_center)s" if cost_center else ""

	payments_pi_result = frappe.db.sql("""
		SELECT SUM(pi.base_paid_amount) as amount
		FROM `tabPurchase Invoice` pi
		INNER JOIN `tabMode of Payment` mop ON mop.name = pi.mode_of_payment
		WHERE {conditions}
			AND mop.type = 'Cash'
			{cost_center_condition}
	""".format(conditions=pi_conditions, cost_center_condition=pi_cost_center_condition), params, as_dict=True)

	# Journal Entries paying out of the branch petty cash account (credit side).
	# Same account resolution as the balances: the branch's derived petty cash
	# accounts without a cost center condition, else Cash-type accounts.
	accounts = get_branch_petty_cash_accounts(company, cost_center)
	params["accounts"] = tuple(accounts) or ("",)
	je_conditions = "je.docstatus = %(docstatus)s"
	if from_date:
		je_conditions += " AND je.posting_date >= %(from_date)s"
	if to_date:
		je_conditions += " AND je.posting_date <= %(to_date)s"
	if company:
		je_conditions += " AND je.company = %(company)s"
	if accounts:
		je_join = ""
		je_account_condition = "jea.account IN %(accounts)s"
	else:
		je_join = "INNER JOIN `tabAccount` acc ON acc.name = jea.account"
		je_account_condition = "acc.account_type = 'Cash'"
		if cost_center:
			je_account_condition += " AND jea.cost_center = %(cost_center)s"
	payments_je_result = frappe.db.sql("""
		SELECT SUM(jea.credit) as amount
		FROM `tabJournal Entry Account` jea
		INNER JOIN `tabJournal Entry` je ON je.name = jea.parent
		{je_join}
		WHERE {je_conditions}
			AND jea.credit > 0
			AND {je_account_condition}
	""".format(je_join=je_join, je_conditions=je_conditions, je_account_condition=je_account_condition), params, as_dict=True)

	# Internal Transfer Payment Entries taking cash out of the petty cash account
	# (e.g. depositing till cash to the bank)
	if accounts:
		it_join = ""
		it_account_condition = "pe_it.paid_from IN %(accounts)s"
	else:
		it_join = "INNER JOIN `tabAccount` acc_it ON acc_it.name = pe_it.paid_from"
		it_account_condition = "acc_it.account_type = 'Cash'"
		if cost_center:
			it_account_condition += " AND pe_it.cost_center = %(cost_center)s"
	it_conditions = "pe_it.docstatus = %(docstatus)s AND pe_it.payment_type = 'Internal Transfer'"
	if from_date:
		it_conditions += " AND pe_it.posting_date >= %(from_date)s"
	if to_date:
		it_conditions += " AND pe_it.posting_date <= %(to_date)s"
	if company:
		it_conditions += " AND pe_it.company = %(company)s"
	payments_it_result = frappe.db.sql("""
		SELECT SUM(pe_it.paid_amount) as amount
		FROM `tabPayment Entry` pe_it
		{it_join}
		WHERE {it_conditions}
			AND {it_account_condition}
	""".format(it_join=it_join, it_conditions=it_conditions, it_account_condition=it_account_condition), params, as_dict=True)

	payments_pe = flt(payments_pe_result[0].amount) if payments_pe_result and payments_pe_result[0].amount else 0
	payments_pi = flt(payments_pi_result[0].amount) if payments_pi_result and payments_pi_result[0].amount else 0
	payments_je = flt(payments_je_result[0].amount) if payments_je_result and payments_je_result[0].amount else 0
	payments_it = flt(payments_it_result[0].amount) if payments_it_result and payments_it_result[0].amount else 0
	return payments_pe + payments_pi + payments_je + payments_it


def get_write_off_total(from_date, to_date, company, cost_center):
	"""Loyalty / small-balance write-off: Payment Entry deduction rows booked to
	the company's Write Off Account (the payment popup and the PE form's
	'Write Off Difference Amount' button both create these)."""
	conditions = "pe.docstatus = 1 AND pe.payment_type = 'Receive'"
	if from_date:
		conditions += " AND pe.posting_date >= %(from_date)s"
	if to_date:
		conditions += " AND pe.posting_date <= %(to_date)s"
	if company:
		conditions += " AND pe.company = %(company)s"
	cost_center_condition = " AND COALESCE(ded.cost_center, pe.cost_center) = %(cost_center)s" if cost_center else ""
	result = frappe.db.sql("""
		SELECT COALESCE(SUM(ded.amount), 0) as amount
		FROM `tabPayment Entry Deduction` ded
		INNER JOIN `tabPayment Entry` pe ON pe.name = ded.parent
		INNER JOIN `tabCompany` c ON c.name = pe.company
		WHERE {conditions}
			AND ded.account = c.write_off_account
			{cost_center_condition}
	""".format(conditions=conditions, cost_center_condition=cost_center_condition), {
		"from_date": from_date,
		"to_date": to_date,
		"company": company,
		"cost_center": cost_center,
	}, as_dict=True)
	return flt(result[0].amount) if result else 0
