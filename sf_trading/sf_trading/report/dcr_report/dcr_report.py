# Copyright (c) 2025, sf_trading and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.utils import flt, getdate, add_days, slug
from frappe.utils.data import quoted


def execute(filters=None):
	columns = get_columns()
	data = get_data(filters)
	return columns, data


def _show_margin():
	"""Show Gross Margin column only to System Manager."""
	return "System Manager" in frappe.get_roles()


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


def get_list_view_link(doctype, label, filters_dict):
	"""Create a clickable link to list view with filters"""
	from frappe.utils import get_url, getdate
	import json
	from urllib.parse import urlencode
	
	# Build route - Frappe list view format
	doctype_slug = slug(doctype)
	route = f"{doctype_slug}/view/list"
	
	# Build filters as query parameters
	# Frappe list views expect date ranges in format: ["between", [date1, date2]]
	query_params = {}
	for key, value in filters_dict.items():
		if value is not None and value != "":
			if isinstance(value, list) and len(value) == 2:
				# Date range: validate dates and format for Frappe
				valid_dates = []
				for date_val in value:
					if date_val:
						# Check string representation first to avoid getdate() error
						date_str_check = str(date_val)
						if date_str_check and not date_str_check.startswith("0000-") and date_str_check != "0000-01-01":
							try:
								# Validate date by trying to parse it
								parsed_date = getdate(date_val)
								# Check year is valid (not 0 or negative)
								if parsed_date and parsed_date.year > 0:
									date_str = parsed_date.strftime("%Y-%m-%d")
									# Final check - ensure it's not invalid
									if date_str and not date_str.startswith("0000-") and date_str != "0000-01-01" and date_str != "0000-01-01":
										valid_dates.append(date_str)
							except:
								# Skip invalid dates
								pass
				# Only add date filter if we have exactly 2 valid dates
				# Frappe expects: ["between", [date1, date2]] (lowercase "between")
				if len(valid_dates) == 2:
					# Final validation - ensure both dates are valid
					date1, date2 = valid_dates[0], valid_dates[1]
					if (date1 and date2 and 
						not date1.startswith("0000-") and not date2.startswith("0000-") and
						date1 != "0000-01-01" and date2 != "0000-01-01"):
						query_params[key] = json.dumps(["between", valid_dates])
			else:
				# Single value: validate and pass as string
				str_value = str(value)
				# Check for invalid date strings BEFORE calling getdate()
				if str_value and not str_value.startswith("0000-") and str_value != "0000-01-01":
					# For date fields, try to validate
					if key.endswith("_date") or key == "posting_date":
						try:
							parsed_date = getdate(str_value)
							# Check year is valid (not 0 or negative)
							if parsed_date and parsed_date.year > 0:
								date_str = parsed_date.strftime("%Y-%m-%d")
								if date_str and not date_str.startswith("0000-") and date_str != "0000-01-01":
									query_params[key] = date_str
						except:
							# Skip invalid dates
							pass
					else:
						query_params[key] = str_value
	
	# Build URL
	if query_params:
		# URL encode the query parameters properly
		query_string = urlencode(query_params)
		url = get_url(uri=f"/app/{route}?{query_string}")
	else:
		url = get_url(uri=f"/app/{route}")
	
	return f'<a href="{url}">{label}</a>'


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


def get_data(filters):
	data = []
	
	if not filters.get("from_date") or not filters.get("to_date"):
		frappe.throw(_("Please select From Date and To Date"))
	
	from_date = getdate(filters.get("from_date"))
	to_date = getdate(filters.get("to_date"))
	company = filters.get("company")
	cost_center = filters.get("cost_center")
	
	# Get opening balance from previous day
	opening_balance = get_opening_cash_balance(from_date, company, cost_center)
	
	# Initialize totals
	cash_sales = 0
	credit_sales = 0
	vat_collected_cash = 0
	vat_applied_credit = 0
	sales_return_cash = 0
	vat_refund_sales_return = 0
	credit_purchase = 0
	cash_received_credit_sales = 0
	payments_petty_cash = 0
	receipts_petty_cash = 0
	total_discount_adj = 0
	
	# Get settled sales (paid on/before invoice date) split between Cash and Cheque
	# by payment allocation, so split-payment invoices land in both rows proportionally
	settled_split = get_settled_sales_split(from_date, to_date, company, cost_center)

	cash_sales_data = settled_split["cash"]
	cash_sales_net = cash_sales_data.get("net_total", 0)
	vat_collected_cash = cash_sales_data.get("vat_amount", 0)
	# Include VAT in cash sales
	cash_sales = cash_sales_net + vat_collected_cash
	total_discount_adj += cash_sales_data.get("discount", 0)

	cheque_sales_data = settled_split["cheque"]
	cheque_sales_net = cheque_sales_data.get("net_total", 0)
	vat_collected_cheque = cheque_sales_data.get("vat_amount", 0)
	cheque_sales = cheque_sales_net + vat_collected_cheque
	cheque_discount = cheque_sales_data.get("discount", 0)

	# Get Credit Sales (Sales Invoices without immediate payment or with credit terms)
	credit_sales_data = get_credit_sales(from_date, to_date, company, cost_center)
	credit_sales_net = credit_sales_data.get("net_total", 0)
	vat_applied_credit = credit_sales_data.get("vat_amount", 0)
	# Include VAT in credit sales
	credit_sales = credit_sales_net + vat_applied_credit

	# Get Home Credit (Delivery Person set, no payment entry received yet)
	home_credit_data = get_home_credit_sales(from_date, to_date, company, cost_center)
	home_credit_net = home_credit_data.get("net_total", 0)
	vat_applied_home_credit = home_credit_data.get("vat_amount", 0)
	home_credit_sales = home_credit_net + vat_applied_home_credit
	
	# Get Sales Return - Cash
	sales_return_data = get_sales_returns_cash(from_date, to_date, company, cost_center)
	sales_return_cash_net = sales_return_data.get("net_total", 0)
	vat_refund_sales_return = sales_return_data.get("vat_amount", 0)
	# Include VAT in sales return
	sales_return_cash = sales_return_cash_net + vat_refund_sales_return
	
	# Get Credit Purchase (including VAT)
	credit_purchase_data = get_credit_purchases(from_date, to_date, company, cost_center)
	credit_purchase = credit_purchase_data.get("total_with_vat", 0)
	
	# Get Cash Received from Credit Sales (Payment Entries for Sales Invoices)
	cash_received_credit_sales = get_cash_received_credit_sales(from_date, to_date, company, cost_center)
	
	# Get Cash Receipts from POS (only cash mode payments)
	cash_receipts_pos = get_cash_receipts_from_pos(from_date, to_date, company, cost_center)
	
	# Get Petty Cash Payments and Receipts (based on Payment Entries)
	petty_cash_data = get_petty_cash_transactions(from_date, to_date, company, cost_center)
	payments_petty_cash = petty_cash_data.get("payments", 0)
	receipts_petty_cash = petty_cash_data.get("receipts", 0)
	
	# Get Internal Transfer transactions affecting cash accounts
	internal_transfer_data = get_internal_transfer_cash_transactions(from_date, to_date, company, cost_center)
	cash_out_internal_transfer = internal_transfer_data.get("cash_out", 0)  # Cash transferred to bank
	cash_in_internal_transfer = internal_transfer_data.get("cash_in", 0)  # Cash received from bank
	
	# Calculate Gross Margin for Cash Sales (use net amount, VAT is not part of margin)
	gross_margin_cash = cash_sales_net - cash_sales_data.get("cost", 0)

	# Calculate Gross Margin for Cheque Sales (use net amount, VAT is not part of margin)
	gross_margin_cheque = cheque_sales_net - cheque_sales_data.get("cost", 0)

	# Calculate Gross Margin for Credit Sales (use net amount, VAT is not part of margin)
	gross_margin_credit = credit_sales_net - credit_sales_data.get("cost", 0)

	# Calculate Gross Margin for Home Credit (use net amount, VAT is not part of margin)
	gross_margin_home_credit = home_credit_net - home_credit_data.get("cost", 0)
	
	# Build report data with clickable links (margin column only for System Manager)
	def _row(particulars, income, expense, discount_adj, margin=0):
		row = [particulars, income, expense, discount_adj]
		if _show_margin():
			row.append(margin)
		return row

	# Opening Cash Balance - no link (row 1 - bold)
	data.append(_row("<b>Opening Cash Balance</b>", opening_balance, 0, 0, 0))
	
	# CASH SALES - link to Sales Invoice list filtered by cash payments
	# Format dates safely - validate using Frappe's getdate
	from_date_str = None
	to_date_str = None
	if from_date:
		try:
			# Check string representation first
			from_date_check = str(from_date)
			if from_date_check and not from_date_check.startswith("0000-") and from_date_check != "0000-01-01":
				# Use Frappe's getdate to validate
				valid_from = getdate(from_date)
				# Check year is valid (not 0 or negative)
				if valid_from and valid_from.year > 0:
					from_date_str = valid_from.strftime("%Y-%m-%d")
					# Final check
					if from_date_str.startswith("0000-") or from_date_str == "0000-01-01":
						from_date_str = None
		except:
			from_date_str = None
	if to_date:
		try:
			# Check string representation first
			to_date_check = str(to_date)
			if to_date_check and not to_date_check.startswith("0000-") and to_date_check != "0000-01-01":
				# Use Frappe's getdate to validate
				valid_to = getdate(to_date)
				# Check year is valid (not 0 or negative)
				if valid_to and valid_to.year > 0:
					to_date_str = valid_to.strftime("%Y-%m-%d")
					# Final check
					if to_date_str.startswith("0000-") or to_date_str == "0000-01-01":
						to_date_str = None
		except:
			to_date_str = None
	
	data.append(_row(get_report_link("CASH SALES", "Cash Sales", from_date_str, to_date_str, company, cost_center), cash_sales, 0, -total_discount_adj, gross_margin_cash))

	# CHEQUE SALES - link to DCR Detail report
	data.append(_row(get_report_link("CHEQUE SALES", "Cheque Sales", from_date_str, to_date_str, company, cost_center), cheque_sales, 0, -cheque_discount, gross_margin_cheque))

	# CREDIT SALES - link to DCR Detail report
	data.append(_row(get_report_link("CREDIT SALES", "Credit Sales", from_date_str, to_date_str, company, cost_center), credit_sales, 0, 0, gross_margin_credit))

	# Home Credit (Delivery Person set, payment not yet received) - link to DCR Detail report
	data.append(_row(get_report_link("Home Credit (Delivery)", "Home Credit (Delivery)", from_date_str, to_date_str, company, cost_center), home_credit_sales, 0, 0, gross_margin_home_credit))

	# Sales Return - Cash - link to DCR Detail report
	data.append(_row(get_report_link("Sales Return - Cash", "Sales Return - Cash", from_date_str, to_date_str, company, cost_center), 0, sales_return_cash, 0, 0))

	# VAT Collected on Cash Sales - link to DCR Detail report (same invoices as Cash Sales)
	data.append(_row(get_report_link("VAT Collected on Cash Sales", "VAT Collected on Cash Sales", from_date_str, to_date_str, company, cost_center), vat_collected_cash, 0, 0, 0))

	# VAT Collected on Cheque Sales - link to DCR Detail report (same invoices as Cheque Sales)
	data.append(_row(get_report_link("VAT Collected on Cheque Sales", "VAT Collected on Cheque Sales", from_date_str, to_date_str, company, cost_center), vat_collected_cheque, 0, 0, 0))

	# VAT Applied on Credit Sales - link to DCR Detail report
	data.append(_row(get_report_link("VAT Applied on Credit Sales", "VAT Applied on Credit Sales", from_date_str, to_date_str, company, cost_center), vat_applied_credit, 0, 0, 0))

	# VAT Applied on Home Credit - link to DCR Detail report (same invoices as Home Credit)
	data.append(_row(get_report_link("VAT Applied on Home Credit", "VAT Applied on Home Credit", from_date_str, to_date_str, company, cost_center), vat_applied_home_credit, 0, 0, 0))

	# VAT Refund on Sales Return - link to DCR Detail report
	data.append(_row(get_report_link("VAT Refund on Sales Return", "VAT Refund on Sales Return", from_date_str, to_date_str, company, cost_center), 0, vat_refund_sales_return, 0, 0))

	# Credit Purchase - link to DCR Detail report
	data.append(_row(get_report_link("Credit Purchase - DIRECT PURCHASE", "Credit Purchase - DIRECT PURCHASE", from_date_str, to_date_str, company, cost_center), 0, credit_purchase, 0, 0))

	# Cash Received : Credit Sales - link to DCR Detail report
	data.append(_row(get_report_link("Cash Received : Credit Sales", "Cash Received : Credit Sales", from_date_str, to_date_str, company, cost_center), cash_received_credit_sales, 0, 0, 0))

	# Payments-Petty Cash - link to DCR Detail report
	data.append(_row(get_report_link("Payments-Petty Cash (Total Payments)", "Payments-Petty Cash (Total Payments)", from_date_str, to_date_str, company, cost_center), 0, payments_petty_cash, 0, 0))

	# Total receipt petty cash (row 11 - bold)
	total_receipt_petty_cash = cash_receipts_pos + cash_received_credit_sales
	data.append(_row("<b>" + _("Total Receipt-Petty Cash") + "</b>", total_receipt_petty_cash, 0, 0, 0))

	# Bank Sales (row 12 - bold) - link to DCR Detail (Bank Sales Receipts; user can change type for payments)
	non_cash_data = get_non_cash_transactions(from_date, to_date, company, cost_center)
	data.append(_row(
		"<b>" + get_report_link(_("Bank Sales"), "Bank Sales Receipts", from_date_str, to_date_str, company, cost_center) + "</b>",
		non_cash_data.get("receipts", 0),
		non_cash_data.get("payments", 0),
		0,
		0
	))
	
	# Calculate Cash Balance (only cash mode payments)
	# Cash Balance = Opening Cash + Cash Receipts (cash mode only) - Cash Payments - Expenses - Internal Transfers (Cash Out) + Internal Transfers (Cash In)
	# Note: Only cash mode payments are included in cash balance calculation
	cash_balance = (
		opening_balance
		+ cash_receipts_pos  # Cash receipts from POS (cash mode only)
		+ cash_received_credit_sales  # Cash received from credit sales (cash mode only)
		- sales_return_cash  # Sales returns including VAT (expense)
		- payments_petty_cash  # Petty cash payments (expense)
		- cash_out_internal_transfer  # Cash transferred to bank (Internal Transfer)
		+ cash_in_internal_transfer  # Cash received from bank (Internal Transfer)
	)
	
	# Cash Balance (row 13 - bold)
	data.append(_row("<b>Cash Balance</b>", cash_balance, 0, 0, 0))

	return data


def get_opening_cash_balance(from_date, company, cost_center):
	"""Get opening cash balance from previous day's closing balance"""
	prev_date = add_days(from_date, -1)
	
	# Get all cash transactions up to previous day
	conditions = "si.posting_date <= %(prev_date)s AND si.docstatus = 1"
	if company:
		conditions += " AND si.company = %(company)s"
	
	# Get Cash Receipts from POS for previous period (only cash mode payments)
	cash_receipts_pos_prev = get_cash_receipts_from_pos(None, prev_date, company, cost_center)
	
	# Get sales returns
	sales_return_data = get_sales_returns_cash(None, prev_date, company, cost_center)
	sales_return_cash_net = sales_return_data.get("net_total", 0)
	vat_refund_sales_return = sales_return_data.get("vat_amount", 0)
	# Include VAT in sales return
	sales_return_cash = sales_return_cash_net + vat_refund_sales_return
	
	# Get cash received from credit sales
	cash_received_credit_sales = get_cash_received_credit_sales(None, prev_date, company, cost_center)
	
	# Get petty cash transactions
	petty_cash_data = get_petty_cash_transactions(None, prev_date, company, cost_center)
	payments_petty_cash = petty_cash_data.get("payments", 0)
	receipts_petty_cash = petty_cash_data.get("receipts", 0)
	
	# Get Internal Transfer transactions affecting cash accounts (for opening balance)
	internal_transfer_prev = get_internal_transfer_cash_transactions(None, prev_date, company, cost_center)
	cash_out_internal_transfer_prev = internal_transfer_prev.get("cash_out", 0)
	cash_in_internal_transfer_prev = internal_transfer_prev.get("cash_in", 0)
	
	# Calculate opening balance (only cash mode payments)
	# Opening balance = Cash Receipts (cash mode only) - Cash Payments - Expenses - Internal Transfers (Cash Out) + Internal Transfers (Cash In)
	# Note: Only cash mode payments are included in cash balance calculation
	opening = (
		cash_receipts_pos_prev  # Cash receipts from POS (cash mode only)
		+ cash_received_credit_sales  # Cash received from credit sales (cash mode only)
		- sales_return_cash  # Sales returns including VAT (expense)
		- payments_petty_cash  # Petty cash payments (expense)
		- cash_out_internal_transfer_prev  # Cash transferred to bank (Internal Transfer)
		+ cash_in_internal_transfer_prev  # Cash received from bank (Internal Transfer)
	)
	
	return flt(opening)


# Invoice has a qualifying (on/before invoice date) Receive Payment Entry (any mode) = settled sale
SETTLED_ON_TIME_CONDITION = """EXISTS (
	SELECT 1 FROM `tabPayment Entry Reference` st_per
	INNER JOIN `tabPayment Entry` st_pe ON st_pe.name = st_per.parent AND st_pe.docstatus = 1 AND st_pe.payment_type = 'Receive'
	WHERE st_per.reference_doctype = 'Sales Invoice' AND st_per.reference_name = si.name
		AND st_pe.posting_date <= si.posting_date
)"""

# Amount allocated to the invoice by qualifying (on/before invoice date) Cheque-mode Payment Entries
CHEQUE_ALLOC_SUBQUERY = """(
	SELECT COALESCE(SUM(chq_per.allocated_amount), 0)
	FROM `tabPayment Entry Reference` chq_per
	INNER JOIN `tabPayment Entry` chq_pe ON chq_pe.name = chq_per.parent AND chq_pe.docstatus = 1 AND chq_pe.payment_type = 'Receive'
	INNER JOIN `tabMode of Payment` chq_mop ON chq_mop.name = chq_pe.mode_of_payment
	WHERE chq_per.reference_doctype = 'Sales Invoice' AND chq_per.reference_name = si.name
		AND chq_pe.posting_date <= si.posting_date
		AND LOWER(chq_mop.name) LIKE '%%cheque%%'
)"""

# Amount allocated to the invoice by qualifying (on/before invoice date) non-Cheque Payment Entries
OTHER_ALLOC_SUBQUERY = """(
	SELECT COALESCE(SUM(oth_per.allocated_amount), 0)
	FROM `tabPayment Entry Reference` oth_per
	INNER JOIN `tabPayment Entry` oth_pe ON oth_pe.name = oth_per.parent AND oth_pe.docstatus = 1 AND oth_pe.payment_type = 'Receive'
	LEFT JOIN `tabMode of Payment` oth_mop ON oth_mop.name = oth_pe.mode_of_payment
	WHERE oth_per.reference_doctype = 'Sales Invoice' AND oth_per.reference_name = si.name
		AND oth_pe.posting_date <= si.posting_date
		AND (oth_mop.name IS NULL OR LOWER(oth_mop.name) NOT LIKE '%%cheque%%')
)"""

# Invoice has any submitted Receive Payment Entry (regardless of date)
HAS_ANY_RECEIVE_PE_CONDITION = """EXISTS (
	SELECT 1 FROM `tabPayment Entry Reference` any_per
	INNER JOIN `tabPayment Entry` any_pe ON any_pe.name = any_per.parent AND any_pe.docstatus = 1 AND any_pe.payment_type = 'Receive'
	WHERE any_per.reference_doctype = 'Sales Invoice' AND any_per.reference_name = si.name
)"""

# Home credit: Delivery Person selected on the invoice and no payment received yet
HOME_CREDIT_CONDITION = "COALESCE(si.custom_driver, '') != '' AND NOT " + HAS_ANY_RECEIVE_PE_CONDITION


def get_settled_sales_split(from_date, to_date, company, cost_center):
	"""Get settled sales (Sales Invoices paid on/before invoice date) split between
	Cash Sales and Cheque Sales by the payment allocation of each mode.
	A split-payment invoice (e.g. 10 by Cheque + 1.5 by Cash) contributes its amounts
	proportionally to both rows instead of being classified into a single one."""
	conditions = "si.docstatus = 1 AND si.is_return = 0"
	if from_date:
		conditions += " AND si.posting_date >= %(from_date)s"
	if to_date:
		conditions += " AND si.posting_date <= %(to_date)s"
	if company:
		conditions += " AND si.company = %(company)s"
	cost_center_condition = " AND sii.cost_center = %(cost_center)s" if cost_center else ""
	result = frappe.db.sql("""
		SELECT
			si.name,
			si.base_net_total,
			si.total_taxes_and_charges as vat_amount,
			si.discount_amount as discount,
			SUM(COALESCE(sii.incoming_rate, 0) * sii.stock_qty) as cost,
			{cheque_alloc} as cheque_alloc,
			{other_alloc} as other_alloc
		FROM `tabSales Invoice` si
		LEFT JOIN `tabSales Invoice Item` sii ON sii.parent = si.name
		WHERE {conditions}
			AND {settled_condition}
			{cost_center_condition}
		GROUP BY si.name, si.base_net_total, si.total_taxes_and_charges, si.discount_amount
	""".format(conditions=conditions, settled_condition=SETTLED_ON_TIME_CONDITION,
		cheque_alloc=CHEQUE_ALLOC_SUBQUERY, other_alloc=OTHER_ALLOC_SUBQUERY,
		cost_center_condition=cost_center_condition), {
		"from_date": from_date,
		"to_date": to_date,
		"company": company,
		"cost_center": cost_center,
	}, as_dict=True)
	cash = {"net_total": 0, "vat_amount": 0, "discount": 0, "cost": 0}
	cheque = {"net_total": 0, "vat_amount": 0, "discount": 0, "cost": 0}
	for r in result:
		total_alloc = flt(r.cheque_alloc) + flt(r.other_alloc)
		cheque_share = flt(r.cheque_alloc) / total_alloc if total_alloc else 0
		for key, value in (
			("net_total", r.base_net_total),
			("vat_amount", r.vat_amount),
			("discount", r.discount),
			("cost", r.cost),
		):
			cheque[key] += flt(value) * cheque_share
			cash[key] += flt(value) * (1 - cheque_share)
	return {"cash": cash, "cheque": cheque}


def get_home_credit_sales(from_date, to_date, company, cost_center):
	"""Get home credit sales: Sales Invoices with a Delivery Person (driver) selected
	and NO Payment Entry received yet. Once a payment is received the invoice moves
	to its respective row (Cash/Cheque/Credit Sales)."""
	conditions = "si.docstatus = 1 AND si.is_return = 0"
	if from_date:
		conditions += " AND si.posting_date >= %(from_date)s"
	if to_date:
		conditions += " AND si.posting_date <= %(to_date)s"
	if company:
		conditions += " AND si.company = %(company)s"
	cost_center_condition = " AND sii.cost_center = %(cost_center)s" if cost_center else ""
	result = frappe.db.sql("""
		SELECT
			si.name,
			si.base_net_total,
			si.total_taxes_and_charges as vat_amount,
			SUM(COALESCE(sii.incoming_rate, 0) * sii.stock_qty) as cost
		FROM `tabSales Invoice` si
		LEFT JOIN `tabSales Invoice Item` sii ON sii.parent = si.name
		WHERE {conditions}
			AND {home_credit_condition}
			{cost_center_condition}
		GROUP BY si.name, si.base_net_total, si.total_taxes_and_charges
	""".format(conditions=conditions, home_credit_condition=HOME_CREDIT_CONDITION, cost_center_condition=cost_center_condition), {
		"from_date": from_date,
		"to_date": to_date,
		"company": company,
		"cost_center": cost_center,
	}, as_dict=True)
	if result:
		return {
			"net_total": sum(flt(r.base_net_total) for r in result),
			"vat_amount": sum(flt(r.vat_amount or 0) for r in result),
			"cost": sum(flt(r.cost or 0) for r in result),
		}
	return {"net_total": 0, "vat_amount": 0, "cost": 0}


def get_credit_sales(from_date, to_date, company, cost_center):
	"""Get credit sales: Sales Invoices that do NOT have a Payment Entry on the same day or before invoice date.
	Credit sale = invoice not paid on/before invoice date (payment later or not yet).
	Home Credit invoices (Delivery Person set, no payment yet) are excluded (shown separately)."""
	conditions = "si.docstatus = 1 AND si.is_return = 0"
	if from_date:
		conditions += " AND si.posting_date >= %(from_date)s"
	if to_date:
		conditions += " AND si.posting_date <= %(to_date)s"
	if company:
		conditions += " AND si.company = %(company)s"
	cost_center_condition = " AND sii.cost_center = %(cost_center)s" if cost_center else ""
	# Exclude invoices that have any PE (Receive) with posting_date <= si.posting_date (those are cash sales)
	result = frappe.db.sql("""
		SELECT
			si.name,
			si.base_net_total,
			si.total_taxes_and_charges as vat_amount,
			SUM(COALESCE(sii.incoming_rate, 0) * sii.stock_qty) as cost
		FROM `tabSales Invoice` si
		LEFT JOIN `tabSales Invoice Item` sii ON sii.parent = si.name
		WHERE {conditions}
			AND NOT EXISTS (
				SELECT 1 FROM `tabPayment Entry Reference` per
				INNER JOIN `tabPayment Entry` pe ON pe.name = per.parent AND pe.docstatus = 1 AND pe.payment_type = 'Receive'
				WHERE per.reference_doctype = 'Sales Invoice' AND per.reference_name = si.name
				  AND pe.posting_date <= si.posting_date
			)
			AND NOT ({home_credit_condition})
			{cost_center_condition}
		GROUP BY si.name, si.base_net_total, si.total_taxes_and_charges
	""".format(conditions=conditions, home_credit_condition=HOME_CREDIT_CONDITION, cost_center_condition=cost_center_condition), {
		"from_date": from_date,
		"to_date": to_date,
		"company": company,
		"cost_center": cost_center,
	}, as_dict=True)
	if result:
		total_net = sum(flt(r.base_net_total) for r in result)
		total_vat = sum(flt(r.vat_amount or 0) for r in result)
		total_cost = sum(flt(r.cost or 0) for r in result)
		return {
			"net_total": total_net,
			"vat_amount": total_vat,
			"cost": total_cost,
		}
	return {"net_total": 0, "vat_amount": 0, "cost": 0}


def get_sales_returns_cash(from_date, to_date, company, cost_center):
	"""Get sales returns for cash sales (return against invoice that had payment on same day or before)."""
	conditions = "si.docstatus = 1 AND si.is_return = 1"
	if from_date:
		conditions += " AND si.posting_date >= %(from_date)s"
	if to_date:
		conditions += " AND si.posting_date <= %(to_date)s"
	if company:
		conditions += " AND si.company = %(company)s"
	cost_center_condition = " AND EXISTS (SELECT 1 FROM `tabSales Invoice Item` sii WHERE sii.parent = si.name AND sii.cost_center = %(cost_center)s)" if cost_center else ""
	# Return is "cash" if the original invoice (return_against) has a PE with posting_date <= original's posting_date
	result = frappe.db.sql("""
		SELECT SUM(ABS(si.base_net_total)) as net_total, SUM(ABS(si.total_taxes_and_charges)) as vat_amount
		FROM `tabSales Invoice` si
		WHERE {conditions}
			AND EXISTS (
				SELECT 1 FROM `tabSales Invoice` orig
				INNER JOIN `tabPayment Entry Reference` per ON per.reference_doctype = 'Sales Invoice' AND per.reference_name = orig.name
				INNER JOIN `tabPayment Entry` pe ON pe.name = per.parent AND pe.docstatus = 1 AND pe.payment_type = 'Receive'
				WHERE orig.name = si.return_against AND orig.docstatus = 1 AND pe.posting_date <= orig.posting_date
			)
			{cost_center_condition}
	""".format(conditions=conditions, cost_center_condition=cost_center_condition), {
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
		# Return total including VAT
		total_with_vat = total_net + total_vat
		return {
			"net_total": total_net,
			"vat_amount": total_vat,
			"total_with_vat": total_with_vat
		}
	return {"net_total": 0, "vat_amount": 0, "total_with_vat": 0}


def get_cash_receipts_from_pos(from_date, to_date, company, cost_center):
	"""Get cash receipts from cash sales (Payment Entry Receive, Cash, where payment date <= invoice date).
	Same-day or before payment = cash sale; sum received_amount for those PEs in the date range."""
	conditions = "pe.docstatus = 1 AND pe.payment_type = 'Receive'"
	if from_date:
		conditions += " AND pe.posting_date >= %(from_date)s"
	if to_date:
		conditions += " AND pe.posting_date <= %(to_date)s"
	if company:
		conditions += " AND pe.company = %(company)s"
	cost_center_condition = " AND pe.cost_center = %(cost_center)s" if cost_center else ""
	result = frappe.db.sql("""
		SELECT SUM(pe.received_amount) as amount
		FROM `tabPayment Entry` pe
		INNER JOIN `tabPayment Entry Reference` per ON per.parent = pe.name
		INNER JOIN `tabMode of Payment` mop ON mop.name = pe.mode_of_payment
		INNER JOIN `tabSales Invoice` si ON si.name = per.reference_name AND per.reference_doctype = 'Sales Invoice'
		WHERE {conditions}
			AND per.reference_doctype = 'Sales Invoice'
			AND pe.posting_date <= si.posting_date
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
	"""Get cash received against credit sales only.
	Count Payment Entries (Receive, Cash) where payment date is AFTER invoice date (collecting on credit)."""
	conditions = "pe.docstatus = 1 AND pe.payment_type = 'Receive'"
	if from_date:
		conditions += " AND pe.posting_date >= %(from_date)s"
	if to_date:
		conditions += " AND pe.posting_date <= %(to_date)s"
	if company:
		conditions += " AND pe.company = %(company)s"
	cost_center_condition = " AND pe.cost_center = %(cost_center)s" if cost_center else ""
	# Only Sales Invoice refs where si.posting_date < pe.posting_date (payment after invoice = credit collection)
	result = frappe.db.sql("""
		SELECT SUM(per.allocated_amount) as amount
		FROM `tabPayment Entry` pe
		INNER JOIN `tabPayment Entry Reference` per ON per.parent = pe.name
		INNER JOIN `tabMode of Payment` mop ON mop.name = pe.mode_of_payment
		INNER JOIN `tabSales Invoice` si ON si.name = per.reference_name AND per.reference_doctype = 'Sales Invoice'
		WHERE {conditions}
			AND per.reference_doctype = 'Sales Invoice'
			AND si.posting_date < pe.posting_date
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


def get_petty_cash_transactions(from_date, to_date, company, cost_center):
	"""Get petty cash payments and receipts
	Payments: Payment Entry (Pay) + Purchase Invoice (is_paid=1) with Cash mode
	Receipts: Payment Entry (Receive) + Sales Invoice payments with Cash mode"""
	
	conditions = "pe.docstatus = 1"
	if from_date:
		conditions += " AND pe.posting_date >= %(from_date)s"
	if to_date:
		conditions += " AND pe.posting_date <= %(to_date)s"
	if company:
		conditions += " AND pe.company = %(company)s"
	
	cost_center_condition = ""
	if cost_center:
		cost_center_condition = " AND pe.cost_center = %(cost_center)s"
	
	# Get Payments from Payment Entry (Pay against Purchase Invoice/Purchase Order)
	payments_pe_result = frappe.db.sql("""
		SELECT 
			SUM(pe.paid_amount) as amount
		FROM `tabPayment Entry` pe
		INNER JOIN `tabPayment Entry Reference` per ON per.parent = pe.name
		INNER JOIN `tabMode of Payment` mop ON mop.name = pe.mode_of_payment
		WHERE {conditions}
			AND pe.payment_type = 'Pay'
			AND per.reference_doctype IN ('Purchase Invoice', 'Purchase Order')
			AND mop.type = 'Cash'
			{cost_center_condition}
	""".format(conditions=conditions, cost_center_condition=cost_center_condition), {
		"from_date": from_date,
		"to_date": to_date,
		"company": company,
		"cost_center": cost_center
	}, as_dict=True)
	
	# Get Payments from Purchase Invoice (is_paid=1 with Cash mode)
	pi_conditions = "pi.docstatus = 1 AND pi.is_paid = 1"
	if from_date:
		pi_conditions += " AND pi.posting_date >= %(from_date)s"
	if to_date:
		pi_conditions += " AND pi.posting_date <= %(to_date)s"
	if company:
		pi_conditions += " AND pi.company = %(company)s"
	
	pi_cost_center_condition = ""
	if cost_center:
		pi_cost_center_condition = " AND pi.cost_center = %(cost_center)s"
	
	payments_pi_result = frappe.db.sql("""
		SELECT 
			SUM(pi.base_paid_amount) as amount
		FROM `tabPurchase Invoice` pi
		INNER JOIN `tabMode of Payment` mop ON mop.name = pi.mode_of_payment
		WHERE {conditions}
			AND mop.type = 'Cash'
			{cost_center_condition}
	""".format(conditions=pi_conditions, cost_center_condition=pi_cost_center_condition), {
		"from_date": from_date,
		"to_date": to_date,
		"company": company,
		"cost_center": cost_center
	}, as_dict=True)
	
	# Get Receipts from Payment Entry (Receive against Sales Invoice/Sales Order)
	receipts_pe_result = frappe.db.sql("""
		SELECT 
			SUM(pe.received_amount) as amount
		FROM `tabPayment Entry` pe
		INNER JOIN `tabPayment Entry Reference` per ON per.parent = pe.name
		INNER JOIN `tabMode of Payment` mop ON mop.name = pe.mode_of_payment
		WHERE {conditions}
			AND pe.payment_type = 'Receive'
			AND per.reference_doctype IN ('Sales Invoice', 'Sales Order')
			AND mop.type = 'Cash'
			{cost_center_condition}
	""".format(conditions=conditions, cost_center_condition=cost_center_condition), {
		"from_date": from_date,
		"to_date": to_date,
		"company": company,
		"cost_center": cost_center
	}, as_dict=True)
	
	# Don't count Sales Invoice Payment in receipts because:
	# 1. Cash sales (with Sales Invoice Payment) are already counted as income in "CASH SALES"
	# 2. Credit sales paid later are counted via Payment Entry
	# So Sales Invoice Payment would cause double counting
	
	# Sum all payments
	payments_pe = flt(payments_pe_result[0].amount) if payments_pe_result and payments_pe_result[0].amount else 0
	payments_pi = flt(payments_pi_result[0].amount) if payments_pi_result and payments_pi_result[0].amount else 0
	payments = payments_pe + payments_pi
	
	# Sum all receipts (only Payment Entry, not Sales Invoice Payment)
	receipts_pe = flt(receipts_pe_result[0].amount) if receipts_pe_result and receipts_pe_result[0].amount else 0
	receipts = receipts_pe
	
	return {
		"payments": payments,
		"receipts": receipts,
		"unposted_payments": 0
	}


def get_internal_transfer_cash_transactions(from_date, to_date, company, cost_center):
	"""Get Internal Transfer Payment Entries affecting cash accounts
	Cash Out: Internal Transfer where paid_from is Cash account (cash transferred to bank)
	Cash In: Internal Transfer where paid_to is Cash account (cash received from bank)"""
	
	conditions = "pe.docstatus = 1 AND pe.payment_type = 'Internal Transfer'"
	if from_date:
		conditions += " AND pe.posting_date >= %(from_date)s"
	if to_date:
		conditions += " AND pe.posting_date <= %(to_date)s"
	if company:
		conditions += " AND pe.company = %(company)s"
	
	cost_center_condition = ""
	if cost_center:
		cost_center_condition = " AND pe.cost_center = %(cost_center)s"
	
	# Get Cash Out: Internal Transfer where paid_from is Cash account
	cash_out_result = frappe.db.sql("""
		SELECT 
			SUM(pe.paid_amount) as amount
		FROM `tabPayment Entry` pe
		INNER JOIN `tabAccount` acc_from ON acc_from.name = pe.paid_from
		WHERE {conditions}
			AND acc_from.account_type = 'Cash'
			{cost_center_condition}
	""".format(conditions=conditions, cost_center_condition=cost_center_condition), {
		"from_date": from_date,
		"to_date": to_date,
		"company": company,
		"cost_center": cost_center
	}, as_dict=True)
	
	# Get Cash In: Internal Transfer where paid_to is Cash account
	cash_in_result = frappe.db.sql("""
		SELECT 
			SUM(pe.received_amount) as amount
		FROM `tabPayment Entry` pe
		INNER JOIN `tabAccount` acc_to ON acc_to.name = pe.paid_to
		WHERE {conditions}
			AND acc_to.account_type = 'Cash'
			{cost_center_condition}
	""".format(conditions=conditions, cost_center_condition=cost_center_condition), {
		"from_date": from_date,
		"to_date": to_date,
		"company": company,
		"cost_center": cost_center
	}, as_dict=True)
	
	cash_out = flt(cash_out_result[0].amount) if cash_out_result and cash_out_result[0].amount else 0
	cash_in = flt(cash_in_result[0].amount) if cash_in_result and cash_in_result[0].amount else 0
	
	return {
		"cash_out": cash_out,
		"cash_in": cash_in
	}


def get_non_cash_transactions(from_date, to_date, company, cost_center):
	"""Get all transactions where Mode of Payment type is NOT 'Cash' (i.e. goes to/from bank).
	Returns receipts (income to bank) and payments (expense from bank)."""
	conditions_pe = "pe.docstatus = 1"
	if from_date:
		conditions_pe += " AND pe.posting_date >= %(from_date)s"
	if to_date:
		conditions_pe += " AND pe.posting_date <= %(to_date)s"
	if company:
		conditions_pe += " AND pe.company = %(company)s"
	cost_center_condition = " AND pe.cost_center = %(cost_center)s" if cost_center else ""

	params = {"from_date": from_date, "to_date": to_date, "company": company, "cost_center": cost_center}

	# Non-cash receipts: Payment Entry (Receive) where mop.type != 'Cash'
	receipts_pe = frappe.db.sql("""
		SELECT SUM(pe.received_amount) as amount
		FROM `tabPayment Entry` pe
		INNER JOIN `tabPayment Entry Reference` per ON per.parent = pe.name
		INNER JOIN `tabMode of Payment` mop ON mop.name = pe.mode_of_payment
		WHERE {conditions}
			AND pe.payment_type = 'Receive'
			AND per.reference_doctype IN ('Sales Invoice', 'Sales Order')
			AND (mop.type IS NULL OR mop.type != 'Cash')
			{cost_center_condition}
	""".format(conditions=conditions_pe, cost_center_condition=cost_center_condition), params, as_dict=True)

	# Non-cash receipts: Sales Invoice Payment where mop.type != 'Cash'
	conditions_si = "si.docstatus = 1 AND si.is_return = 0"
	if from_date:
		conditions_si += " AND si.posting_date >= %(from_date)s"
	if to_date:
		conditions_si += " AND si.posting_date <= %(to_date)s"
	if company:
		conditions_si += " AND si.company = %(company)s"
	si_cost_condition = ""
	if cost_center:
		si_cost_condition = " AND EXISTS (SELECT 1 FROM `tabSales Invoice Item` sii WHERE sii.parent = si.name AND sii.cost_center = %(cost_center)s)"

	receipts_si = frappe.db.sql("""
		SELECT SUM(sip.base_amount) as amount
		FROM `tabSales Invoice` si
		INNER JOIN `tabSales Invoice Payment` sip ON sip.parent = si.name
		INNER JOIN `tabMode of Payment` mop ON mop.name = sip.mode_of_payment
		WHERE {conditions}
			AND (mop.type IS NULL OR mop.type != 'Cash')
			{si_cost_condition}
	""".format(conditions=conditions_si, si_cost_condition=si_cost_condition), params, as_dict=True)

	# Non-cash payments: Payment Entry (Pay) where mop.type != 'Cash'
	payments_pe = frappe.db.sql("""
		SELECT SUM(pe.paid_amount) as amount
		FROM `tabPayment Entry` pe
		INNER JOIN `tabMode of Payment` mop ON mop.name = pe.mode_of_payment
		WHERE {conditions}
			AND pe.payment_type = 'Pay'
			AND (mop.type IS NULL OR mop.type != 'Cash')
			{cost_center_condition}
	""".format(conditions=conditions_pe, cost_center_condition=cost_center_condition), params, as_dict=True)

	# Non-cash payments: Purchase Invoice (is_paid) where mop.type != 'Cash'
	pi_conditions = "pi.docstatus = 1 AND pi.is_paid = 1"
	if from_date:
		pi_conditions += " AND pi.posting_date >= %(from_date)s"
	if to_date:
		pi_conditions += " AND pi.posting_date <= %(to_date)s"
	if company:
		pi_conditions += " AND pi.company = %(company)s"
	pi_cost_condition = " AND pi.cost_center = %(cost_center)s" if cost_center else ""
	payments_pi = frappe.db.sql("""
		SELECT SUM(pi.base_paid_amount) as amount
		FROM `tabPurchase Invoice` pi
		INNER JOIN `tabMode of Payment` mop ON mop.name = pi.mode_of_payment
		WHERE {conditions}
			AND (mop.type IS NULL OR mop.type != 'Cash')
			{pi_cost_condition}
	""".format(conditions=pi_conditions, pi_cost_condition=pi_cost_condition), params, as_dict=True)

	receipts = flt(receipts_pe[0].amount if receipts_pe and receipts_pe[0].amount else 0) + flt(receipts_si[0].amount if receipts_si and receipts_si[0].amount else 0)
	payments = flt(payments_pe[0].amount if payments_pe and payments_pe[0].amount else 0) + flt(payments_pi[0].amount if payments_pi and payments_pi[0].amount else 0)

	return {"receipts": receipts, "payments": payments}
