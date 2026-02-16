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
	
	# Get Cash Sales (Sales Invoices with Cash payment mode)
	cash_sales_data = get_cash_sales(from_date, to_date, company, cost_center)
	cash_sales_net = cash_sales_data.get("net_total", 0)
	vat_collected_cash = cash_sales_data.get("vat_amount", 0)
	# Include VAT in cash sales
	cash_sales = cash_sales_net + vat_collected_cash
	total_discount_adj += cash_sales_data.get("discount", 0)
	
	# Get Credit Sales (Sales Invoices without immediate payment or with credit terms)
	credit_sales_data = get_credit_sales(from_date, to_date, company, cost_center)
	credit_sales_net = credit_sales_data.get("net_total", 0)
	vat_applied_credit = credit_sales_data.get("vat_amount", 0)
	# Include VAT in credit sales
	credit_sales = credit_sales_net + vat_applied_credit
	
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
	
	# Get Petty Cash Payments and Receipts (based on Payment Entries)
	petty_cash_data = get_petty_cash_transactions(from_date, to_date, company, cost_center)
	payments_petty_cash = petty_cash_data.get("payments", 0)
	receipts_petty_cash = petty_cash_data.get("receipts", 0)
	
	# Calculate Gross Margin for Cash Sales (use net amount, VAT is not part of margin)
	gross_margin_cash = cash_sales_net - cash_sales_data.get("cost", 0)
	
	# Calculate Gross Margin for Credit Sales (use net amount, VAT is not part of margin)
	gross_margin_credit = credit_sales_net - credit_sales_data.get("cost", 0)
	
	# Build report data with clickable links (margin column only for System Manager)
	def _row(particulars, income, expense, discount_adj, margin=0):
		row = [particulars, income, expense, discount_adj]
		if _show_margin():
			row.append(margin)
		return row

	# Opening Cash Balance - no link
	data.append(_row("Opening Cash Balance", opening_balance, 0, 0, 0))
	
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
	
	cash_sales_filters = {
		"company": company,
		"docstatus": "1",
		"is_return": "0"
	}
	# Only add date filter if both dates are valid and not "0000-01-01"
	if from_date_str and to_date_str and from_date_str != "0000-01-01" and to_date_str != "0000-01-01" and not from_date_str.startswith("0000-") and not to_date_str.startswith("0000-"):
		cash_sales_filters["posting_date"] = [from_date_str, to_date_str]
	if cost_center:
		cash_sales_filters["cost_center"] = cost_center
	data.append(_row(get_list_view_link("Sales Invoice", "CASH SALES", cash_sales_filters), cash_sales, 0, -total_discount_adj, gross_margin_cash))

	# CREDIT SALES - link to Sales Invoice list filtered by credit sales
	credit_sales_filters = {
		"company": company,
		"docstatus": "1",
		"is_return": "0"
	}
	if from_date_str and to_date_str and from_date_str != "0000-01-01" and to_date_str != "0000-01-01" and not from_date_str.startswith("0000-") and not to_date_str.startswith("0000-"):
		credit_sales_filters["posting_date"] = [from_date_str, to_date_str]
	if cost_center:
		credit_sales_filters["cost_center"] = cost_center
	data.append(_row(get_list_view_link("Sales Invoice", "CREDIT SALES", credit_sales_filters), credit_sales, 0, 0, gross_margin_credit))

	# Sales Return - Cash - link to Sales Invoice list filtered by returns (moved to position 4)
	returns_filters = {
		"company": company,
		"docstatus": "1",
		"is_return": "1"
	}
	if from_date_str and to_date_str and from_date_str != "0000-01-01" and to_date_str != "0000-01-01" and not from_date_str.startswith("0000-") and not to_date_str.startswith("0000-"):
		returns_filters["posting_date"] = [from_date_str, to_date_str]
	if cost_center:
		returns_filters["cost_center"] = cost_center
	data.append(_row(get_list_view_link("Sales Invoice", "Sales Return - Cash", returns_filters), 0, sales_return_cash, 0, 0))

	# VAT Collected on Cash Sales - link to Sales Invoice list (same as cash sales) (moved to position 5)
	# VAT collected is income, so it should be positive
	data.append(_row(get_list_view_link("Sales Invoice", "VAT Collected on Cash Sales", cash_sales_filters), vat_collected_cash, 0, 0, 0))

	# VAT Applied on Credit Sales - link to Sales Invoice list (same as credit sales) (moved to position 6)
	# VAT applied is income, so it should be positive
	data.append(_row(get_list_view_link("Sales Invoice", "VAT Applied on Credit Sales", credit_sales_filters), vat_applied_credit, 0, 0, 0))

	# VAT Refund on Sales Return - link to Sales Invoice list (same as returns) (position 7)
	# VAT refund is expense, so it should be in expense column
	data.append(_row(get_list_view_link("Sales Invoice", "VAT Refund on Sales Return", returns_filters), 0, vat_refund_sales_return, 0, 0))

	# Credit Purchase - link to Purchase Invoice list
	purchase_filters = {
		"company": company,
		"docstatus": "1"
	}
	if from_date_str and to_date_str and from_date_str != "0000-01-01" and to_date_str != "0000-01-01" and not from_date_str.startswith("0000-") and not to_date_str.startswith("0000-"):
		purchase_filters["posting_date"] = [from_date_str, to_date_str]
	if cost_center:
		purchase_filters["cost_center"] = cost_center
	data.append(_row(get_list_view_link("Purchase Invoice", "Credit Purchase - DIRECT PURCHASE", purchase_filters), 0, credit_purchase, 0, 0))

	# Cash Received : Credit Sales - link to Payment Entry list filtered by Receive type
	payment_receive_filters = {
		"company": company,
		"docstatus": "1",
		"payment_type": "Receive"
	}
	if from_date_str and to_date_str and from_date_str != "0000-01-01" and to_date_str != "0000-01-01" and not from_date_str.startswith("0000-") and not to_date_str.startswith("0000-"):
		payment_receive_filters["posting_date"] = [from_date_str, to_date_str]
	if cost_center:
		payment_receive_filters["cost_center"] = cost_center
	data.append(_row(get_list_view_link("Payment Entry", "Cash Received : Credit Sales", payment_receive_filters), cash_received_credit_sales, 0, 0, 0))

	# Payments-Petty Cash - link to Payment Entry list filtered by Pay type
	payment_pay_filters = {
		"company": company,
		"docstatus": "1",
		"payment_type": "Pay"
	}
	if from_date_str and to_date_str and from_date_str != "0000-01-01" and to_date_str != "0000-01-01" and not from_date_str.startswith("0000-") and not to_date_str.startswith("0000-"):
		payment_pay_filters["posting_date"] = [from_date_str, to_date_str]
	if cost_center:
		payment_pay_filters["cost_center"] = cost_center
	data.append(_row(get_list_view_link("Payment Entry", "Payments-Petty Cash (Total Payments)", payment_pay_filters), 0, payments_petty_cash, 0, 0))

	# Total Receipts = Cash Sales + Cash Received from Credit Sales
	total_receipts = cash_sales + cash_received_credit_sales
	data.append(_row("Total Receipts", total_receipts, 0, 0, 0))
	
	# Calculate Cash Balance
	# Cash Balance = Opening Cash + Total Receipts - Total Payments - Expenses
	# Note: VAT is now included in cash_sales and sales_return_cash
	cash_balance = (
		opening_balance
		+ total_receipts  # Total receipts (Cash Sales + VAT + Cash Received from Credit Sales)
		- sales_return_cash  # Sales returns including VAT (expense)
		- payments_petty_cash  # Petty cash payments (expense)
	)
	
	# Cash Balance - no link
	data.append(_row("Cash Balance", cash_balance, 0, 0, 0))

	return data


def get_opening_cash_balance(from_date, company, cost_center):
	"""Get opening cash balance from previous day's closing balance"""
	prev_date = add_days(from_date, -1)
	
	# Get all cash transactions up to previous day
	conditions = "si.posting_date <= %(prev_date)s AND si.docstatus = 1"
	if company:
		conditions += " AND si.company = %(company)s"
	
	# Get cash sales up to previous day
	cash_sales_data = get_cash_sales(None, prev_date, company, cost_center)
	cash_sales_net = cash_sales_data.get("net_total", 0)
	vat_collected_cash = cash_sales_data.get("vat_amount", 0)
	# Include VAT in cash sales
	cash_sales = cash_sales_net + vat_collected_cash
	
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
	
	# Calculate opening balance
	# Opening balance = Total Receipts (Cash Sales + VAT + Cash Received) - Payments - Expenses
	# Note: VAT is now included in cash_sales and sales_return_cash
	total_receipts_prev = cash_sales + cash_received_credit_sales
	opening = (
		total_receipts_prev  # Total receipts (Cash Sales + VAT + Cash Received from Credit Sales)
		- sales_return_cash  # Sales returns including VAT (expense)
		- payments_petty_cash  # Petty cash payments (expense)
	)
	
	return flt(opening)


def get_cash_sales(from_date, to_date, company, cost_center):
	"""Get cash sales (Sales Invoices with is_pos=1 OR Mode of Payment type='Cash')
	For POS invoices: net_total - change_amount
	For regular cash invoices: net_total"""
	conditions = "si.docstatus = 1 AND si.is_return = 0"
	if from_date:
		conditions += " AND si.posting_date >= %(from_date)s"
	if to_date:
		conditions += " AND si.posting_date <= %(to_date)s"
	if company:
		conditions += " AND si.company = %(company)s"
	
	cost_center_condition = ""
	if cost_center:
		cost_center_condition = " AND sii.cost_center = %(cost_center)s"
	
	# Get cash sales: is_pos=1 OR mode_of_payment type='Cash'
	result = frappe.db.sql("""
		SELECT 
			si.name,
			si.net_total,
			si.base_net_total,
			COALESCE(si.change_amount, 0) as change_amount,
			COALESCE(si.base_change_amount, 0) as base_change_amount,
			si.is_pos,
			si.total_taxes_and_charges as vat_amount,
			si.discount_amount as discount,
			SUM(COALESCE(sii.incoming_rate, 0) * sii.stock_qty) as cost
		FROM `tabSales Invoice` si
		LEFT JOIN `tabSales Invoice Item` sii ON sii.parent = si.name
		WHERE {conditions}
			AND (
				si.is_pos = 1 
				OR EXISTS (
					SELECT 1 FROM `tabSales Invoice Payment` sip
					INNER JOIN `tabMode of Payment` mop ON mop.name = sip.mode_of_payment
					WHERE sip.parent = si.name AND mop.type = 'Cash'
				)
			)
			{cost_center_condition}
		GROUP BY si.name, si.net_total, si.base_net_total, si.change_amount, 
			si.base_change_amount, si.is_pos, si.total_taxes_and_charges, si.discount_amount
	""".format(conditions=conditions, cost_center_condition=cost_center_condition), {
		"from_date": from_date,
		"to_date": to_date,
		"company": company,
		"cost_center": cost_center
	}, as_dict=True)
	
	if result:
		total_net = 0
		total_vat = 0
		total_discount = 0
		total_cost = 0
		
		for r in result:
			# For POS invoices: subtract change_amount
			if r.is_pos:
				# Use base_net_total - base_change_amount for base currency
				net_amount = flt(r.base_net_total) - flt(r.base_change_amount)
			else:
				net_amount = flt(r.net_total)
			
			total_net += net_amount
			total_vat += flt(r.vat_amount) if r.vat_amount else 0
			total_discount += flt(r.discount) if r.discount else 0
			total_cost += flt(r.cost) if r.cost else 0
		
		return {
			"net_total": total_net,
			"vat_amount": total_vat,
			"discount": total_discount,
			"cost": total_cost
		}
	return {"net_total": 0, "vat_amount": 0, "discount": 0, "cost": 0}


def get_credit_sales(from_date, to_date, company, cost_center):
	"""Get credit sales (Sales Invoices that are NOT cash sales)
	Credit sales = invoices that are NOT (is_pos=1 OR mode_of_payment type='Cash')
	Even if they receive payment via Payment Entry later, they're still credit sales"""
	conditions = "si.docstatus = 1 AND si.is_return = 0"
	if from_date:
		conditions += " AND si.posting_date >= %(from_date)s"
	if to_date:
		conditions += " AND si.posting_date <= %(to_date)s"
	if company:
		conditions += " AND si.company = %(company)s"
	
	cost_center_condition = ""
	if cost_center:
		cost_center_condition = " AND sii.cost_center = %(cost_center)s"
	
	# Get invoices that are NOT cash sales
	# NOT (is_pos=1 OR has cash mode of payment)
	result = frappe.db.sql("""
		SELECT 
			SUM(DISTINCT si.net_total) as net_total,
			SUM(DISTINCT si.total_taxes_and_charges) as vat_amount,
			SUM(COALESCE(sii.incoming_rate, 0) * sii.stock_qty) as cost
		FROM `tabSales Invoice` si
		LEFT JOIN `tabSales Invoice Item` sii ON sii.parent = si.name
		WHERE {conditions}
			AND si.is_pos = 0
			AND NOT EXISTS (
				SELECT 1 FROM `tabSales Invoice Payment` sip
				INNER JOIN `tabMode of Payment` mop ON mop.name = sip.mode_of_payment
				WHERE sip.parent = si.name AND mop.type = 'Cash'
			)
			{cost_center_condition}
		GROUP BY si.name
	""".format(conditions=conditions, cost_center_condition=cost_center_condition), {
		"from_date": from_date,
		"to_date": to_date,
		"company": company,
		"cost_center": cost_center
	}, as_dict=True)
	
	if result:
		total_net = sum([flt(r.net_total) for r in result if r.net_total])
		total_vat = sum([flt(r.vat_amount) for r in result if r.vat_amount])
		total_cost = sum([flt(r.cost) for r in result if r.cost])
		
		return {
			"net_total": total_net,
			"vat_amount": total_vat,
			"cost": total_cost
		}
	return {"net_total": 0, "vat_amount": 0, "cost": 0}


def get_sales_returns_cash(from_date, to_date, company, cost_center):
	"""Get sales returns for cash invoices (is_pos=1 OR mode_of_payment type='Cash')"""
	conditions = "si.docstatus = 1 AND si.is_return = 1"
	if from_date:
		conditions += " AND si.posting_date >= %(from_date)s"
	if to_date:
		conditions += " AND si.posting_date <= %(to_date)s"
	if company:
		conditions += " AND si.company = %(company)s"
	
	cost_center_condition = ""
	if cost_center:
		cost_center_condition = " AND sii.cost_center = %(cost_center)s"
	
	result = frappe.db.sql("""
		SELECT 
			SUM(ABS(si.net_total)) as net_total,
			SUM(ABS(si.total_taxes_and_charges)) as vat_amount
		FROM `tabSales Invoice` si
		LEFT JOIN `tabSales Invoice Item` sii ON sii.parent = si.name
		WHERE {conditions}
			AND (
				si.is_pos = 1 
				OR EXISTS (
					SELECT 1 FROM `tabSales Invoice Payment` sip
					INNER JOIN `tabMode of Payment` mop ON mop.name = sip.mode_of_payment
					WHERE sip.parent = si.name AND mop.type = 'Cash'
				)
			)
			{cost_center_condition}
	""".format(conditions=conditions, cost_center_condition=cost_center_condition), {
		"from_date": from_date,
		"to_date": to_date,
		"company": company,
		"cost_center": cost_center
	}, as_dict=True)
	
	if result and result[0].net_total:
		return {
			"net_total": flt(result[0].net_total),
			"vat_amount": flt(result[0].vat_amount)
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


def get_cash_received_credit_sales(from_date, to_date, company, cost_center):
	"""Get cash received against credit sales (Payment Entries for Sales Invoices with Cash mode)"""
	conditions = "pe.docstatus = 1 AND pe.payment_type = 'Receive'"
	if from_date:
		conditions += " AND pe.posting_date >= %(from_date)s"
	if to_date:
		conditions += " AND pe.posting_date <= %(to_date)s"
	if company:
		conditions += " AND pe.company = %(company)s"
	
	cost_center_condition = ""
	if cost_center:
		cost_center_condition = " AND pe.cost_center = %(cost_center)s"
	
	result = frappe.db.sql("""
		SELECT 
			SUM(per.allocated_amount) as amount
		FROM `tabPayment Entry` pe
		INNER JOIN `tabPayment Entry Reference` per ON per.parent = pe.name
		INNER JOIN `tabMode of Payment` mop ON mop.name = pe.mode_of_payment
		WHERE {conditions}
			AND per.reference_doctype IN ('Sales Invoice', 'Sales Order')
			AND mop.type = 'Cash'
			{cost_center_condition}
	""".format(conditions=conditions, cost_center_condition=cost_center_condition), {
		"from_date": from_date,
		"to_date": to_date,
		"company": company,
		"cost_center": cost_center
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
