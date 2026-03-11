# Copyright (c) 2025, sf_trading and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.utils import flt, getdate


REPORT_TYPES = [
	"Cash Sales",
	"Credit Sales",
	"Sales Return - Cash",
	"VAT Collected on Cash Sales",
	"VAT Applied on Credit Sales",
	"VAT Refund on Sales Return",
	"Credit Purchase - DIRECT PURCHASE",
	"Cash Received : Credit Sales",
	"Payments-Petty Cash (Total Payments)",
	"Cash Receipts (Cash Sales)",
	"Bank Sales Receipts",
	"Bank Sales Payments",
	"Internal Transfer (Cash Out)",
	"Internal Transfer (Cash In)",
]


def execute(filters=None):
	if not filters:
		filters = {}
	if not filters.get("report_type"):
		return get_columns("Cash Sales"), []
	if not filters.get("from_date") or not filters.get("to_date"):
		frappe.throw(_("Please select From Date and To Date"))
	from_date = getdate(filters.get("from_date"))
	to_date = getdate(filters.get("to_date"))
	company = filters.get("company")
	cost_center = filters.get("cost_center")
	report_type = filters.get("report_type")
	columns = get_columns(report_type)
	data = get_data_for_type(report_type, from_date, to_date, company, cost_center)
	return columns, data


def get_columns(report_type):
	"""Columns vary by report type."""
	if report_type in ("Cash Sales", "Credit Sales", "VAT Collected on Cash Sales", "VAT Applied on Credit Sales"):
		return [
			_("Sales Invoice") + ":Link/Sales Invoice:120",
			_("Posting Date") + ":Date:100",
			_("Customer") + ":Link/Customer:150",
			_("Customer Name") + ":Data:180",
			_("Net Total") + ":Currency:120",
			_("VAT") + ":Currency:100",
			_("Grand Total") + ":Currency:120",
			_("Discount") + ":Currency:90",
		]
	if report_type in ("Sales Return - Cash", "VAT Refund on Sales Return"):
		return [
			_("Sales Invoice") + ":Link/Sales Invoice:120",
			_("Posting Date") + ":Date:100",
			_("Return Against") + ":Link/Sales Invoice:120",
			_("Customer") + ":Link/Customer:150",
			_("Net Total") + ":Currency:120",
			_("VAT") + ":Currency:100",
		]
	if report_type == "Credit Purchase - DIRECT PURCHASE":
		return [
			_("Purchase Invoice") + ":Link/Purchase Invoice:120",
			_("Posting Date") + ":Date:100",
			_("Supplier") + ":Link/Supplier:150",
			_("Supplier Name") + ":Data:180",
			_("Net Total") + ":Currency:120",
			_("VAT") + ":Currency:100",
			_("Grand Total") + ":Currency:120",
		]
	if report_type in ("Cash Received : Credit Sales", "Cash Receipts (Cash Sales)"):
		return [
			_("Payment Entry") + ":Link/Payment Entry:120",
			_("Posting Date") + ":Date:100",
			_("Party") + ":Data:150",
			_("Received Amount") + ":Currency:120",
			_("Mode of Payment") + ":Data:120",
			_("Reference") + ":Link/Sales Invoice:120",
		]
	if report_type == "Payments-Petty Cash (Total Payments)":
		return [
			_("Document Type") + ":Data:120",
			_("Document") + ":Data:120",
			_("Posting Date") + ":Date:100",
			_("Party") + ":Data:150",
			_("Amount") + ":Currency:120",
			_("Mode of Payment") + ":Data:120",
		]
	if report_type == "Bank Sales Receipts":
		return [
			_("Payment Entry") + ":Link/Payment Entry:120",
			_("Posting Date") + ":Date:100",
			_("Party") + ":Data:150",
			_("Received Amount") + ":Currency:120",
			_("Mode of Payment") + ":Data:120",
			_("Reference") + ":Data:120",
		]
	if report_type == "Bank Sales Payments":
		return [
			_("Document Type") + ":Data:120",
			_("Document") + ":Data:120",
			_("Posting Date") + ":Date:100",
			_("Party") + ":Data:150",
			_("Amount") + ":Currency:120",
			_("Mode of Payment") + ":Data:120",
		]
	if report_type in ("Internal Transfer (Cash Out)", "Internal Transfer (Cash In)"):
		return [
			_("Payment Entry") + ":Link/Payment Entry:120",
			_("Posting Date") + ":Date:100",
			_("From Account") + ":Link/Account:150",
			_("To Account") + ":Link/Account:150",
			_("Amount") + ":Currency:120",
		]
	return []


def _base_conditions(from_date, to_date, company, cost_center, date_field="si.posting_date", table_alias="si"):
	conditions = []
	if from_date:
		conditions.append(f" {date_field} >= %(from_date)s")
	if to_date:
		conditions.append(f" {date_field} <= %(to_date)s")
	if company:
		conditions.append(f" {table_alias}.company = %(company)s")
	return " AND ".join(conditions) if conditions else " 1=1 "


def get_data_for_type(report_type, from_date, to_date, company, cost_center):
	params = {"from_date": from_date, "to_date": to_date, "company": company, "cost_center": cost_center}
	if report_type in ("Cash Sales", "VAT Collected on Cash Sales"):
		return _detail_cash_sales(params, cost_center)
	if report_type in ("Credit Sales", "VAT Applied on Credit Sales"):
		return _detail_credit_sales(params, cost_center)
	if report_type in ("Sales Return - Cash", "VAT Refund on Sales Return"):
		return _detail_sales_return_cash(params, cost_center)
	if report_type == "Credit Purchase - DIRECT PURCHASE":
		return _detail_credit_purchase(params, cost_center)
	if report_type == "Cash Received : Credit Sales":
		return _detail_cash_received_credit_sales(params, cost_center)
	if report_type == "Payments-Petty Cash (Total Payments)":
		return _detail_payments_petty_cash(params, cost_center)
	if report_type == "Cash Receipts (Cash Sales)":
		return _detail_cash_receipts_cash_sales(params, cost_center)
	if report_type == "Bank Sales Receipts":
		return _detail_bank_sales_receipts(params, cost_center)
	if report_type == "Bank Sales Payments":
		return _detail_bank_sales_payments(params, cost_center)
	if report_type == "Internal Transfer (Cash Out)":
		return _detail_internal_transfer_out(params, cost_center)
	if report_type == "Internal Transfer (Cash In)":
		return _detail_internal_transfer_in(params, cost_center)
	return []


def _detail_cash_sales(params, cost_center):
	cond = _base_conditions(params["from_date"], params["to_date"], params["company"], cost_center, "si.posting_date")
	cc = " AND sii.cost_center = %(cost_center)s" if cost_center else ""
	sql = """
		SELECT si.name, si.posting_date, si.customer, si.customer_name,
			si.base_net_total, si.total_taxes_and_charges, si.grand_total, si.discount_amount
		FROM `tabSales Invoice` si
		LEFT JOIN `tabSales Invoice Item` sii ON sii.parent = si.name
		INNER JOIN `tabPayment Entry Reference` per ON per.reference_doctype = 'Sales Invoice' AND per.reference_name = si.name
		INNER JOIN `tabPayment Entry` pe ON pe.name = per.parent AND pe.docstatus = 1 AND pe.payment_type = 'Receive'
		WHERE si.docstatus = 1 AND si.is_return = 0 AND """ + cond + """
		AND pe.posting_date <= si.posting_date
		""" + cc + """
		GROUP BY si.name, si.posting_date, si.customer, si.customer_name, si.base_net_total,
			si.total_taxes_and_charges, si.grand_total, si.discount_amount
		ORDER BY si.posting_date, si.name
	"""
	rows = frappe.db.sql(sql, params, as_dict=True)
	return [_row_si(r) for r in rows]


def _row_si(r):
	return [
		r.name,
		r.posting_date,
		r.get("customer"),
		r.get("customer_name") or "",
		flt(r.get("base_net_total")),
		flt(r.get("total_taxes_and_charges")),
		flt(r.get("grand_total")),
		flt(r.get("discount_amount")),
	]


def _detail_credit_sales(params, cost_center):
	cond = _base_conditions(params["from_date"], params["to_date"], params["company"], cost_center, "si.posting_date")
	cc = " AND sii.cost_center = %(cost_center)s" if cost_center else ""
	sql = """
		SELECT si.name, si.posting_date, si.customer, si.customer_name,
			si.base_net_total, si.total_taxes_and_charges, si.grand_total, si.discount_amount
		FROM `tabSales Invoice` si
		LEFT JOIN `tabSales Invoice Item` sii ON sii.parent = si.name
		WHERE si.docstatus = 1 AND si.is_return = 0 AND """ + cond + """
		AND NOT EXISTS (
			SELECT 1 FROM `tabPayment Entry Reference` per
			INNER JOIN `tabPayment Entry` pe ON pe.name = per.parent AND pe.docstatus = 1 AND pe.payment_type = 'Receive'
			WHERE per.reference_doctype = 'Sales Invoice' AND per.reference_name = si.name
			  AND pe.posting_date <= si.posting_date
		)
		""" + cc + """
		GROUP BY si.name, si.posting_date, si.customer, si.customer_name, si.base_net_total,
			si.total_taxes_and_charges, si.grand_total, si.discount_amount
		ORDER BY si.posting_date, si.name
	"""
	rows = frappe.db.sql(sql, params, as_dict=True)
	return [_row_si(r) for r in rows]


def _detail_sales_return_cash(params, cost_center):
	cond = _base_conditions(params["from_date"], params["to_date"], params["company"], cost_center, "si.posting_date")
	cc = " AND EXISTS (SELECT 1 FROM `tabSales Invoice Item` sii WHERE sii.parent = si.name AND sii.cost_center = %(cost_center)s)" if cost_center else ""
	sql = """
		SELECT si.name, si.posting_date, si.return_against, si.customer,
			ABS(si.base_net_total) as base_net_total, ABS(si.total_taxes_and_charges) as total_taxes_and_charges
		FROM `tabSales Invoice` si
		WHERE si.docstatus = 1 AND si.is_return = 1 AND """ + cond + """
		AND EXISTS (
			SELECT 1 FROM `tabSales Invoice` orig
			INNER JOIN `tabPayment Entry Reference` per ON per.reference_doctype = 'Sales Invoice' AND per.reference_name = orig.name
			INNER JOIN `tabPayment Entry` pe ON pe.name = per.parent AND pe.docstatus = 1 AND pe.payment_type = 'Receive'
			WHERE orig.name = si.return_against AND orig.docstatus = 1 AND pe.posting_date <= orig.posting_date
		)
		""" + cc + """
		ORDER BY si.posting_date, si.name
	"""
	rows = frappe.db.sql(sql, params, as_dict=True)
	return [[r.name, r.posting_date, r.return_against, r.get("customer"), flt(r.base_net_total), flt(r.total_taxes_and_charges)] for r in rows]


def _detail_credit_purchase(params, cost_center):
	cond = _base_conditions(params["from_date"], params["to_date"], params["company"], cost_center, "pi.posting_date", "pi")
	cc = " AND pii.cost_center = %(cost_center)s" if cost_center else ""
	sql = """
		SELECT pi.name, pi.posting_date, pi.supplier, pi.supplier_name,
			pi.net_total, pi.total_taxes_and_charges, pi.grand_total
		FROM `tabPurchase Invoice` pi
		LEFT JOIN `tabPurchase Invoice Item` pii ON pii.parent = pi.name
		WHERE pi.docstatus = 1 AND """ + cond + """
		""" + cc + """
		GROUP BY pi.name, pi.posting_date, pi.supplier, pi.supplier_name, pi.net_total, pi.total_taxes_and_charges, pi.grand_total
		ORDER BY pi.posting_date, pi.name
	"""
	rows = frappe.db.sql(sql, params, as_dict=True)
	return [[r.name, r.posting_date, r.get("supplier"), r.get("supplier_name") or "", flt(r.net_total), flt(r.total_taxes_and_charges), flt(r.grand_total)] for r in rows]


def _detail_cash_received_credit_sales(params, cost_center):
	cond = _base_conditions(params["from_date"], params["to_date"], params["company"], cost_center, "pe.posting_date", "pe")
	cc = " AND pe.cost_center = %(cost_center)s" if cost_center else ""
	sql = """
		SELECT pe.name, pe.posting_date, pe.party, pe.received_amount, pe.mode_of_payment, per.reference_name
		FROM `tabPayment Entry` pe
		INNER JOIN `tabPayment Entry Reference` per ON per.parent = pe.name
		INNER JOIN `tabMode of Payment` mop ON mop.name = pe.mode_of_payment
		INNER JOIN `tabSales Invoice` si ON si.name = per.reference_name AND per.reference_doctype = 'Sales Invoice'
		WHERE pe.docstatus = 1 AND pe.payment_type = 'Receive' AND """ + cond + """
		AND per.reference_doctype = 'Sales Invoice' AND si.posting_date < pe.posting_date AND mop.type = 'Cash'
		""" + cc + """
		ORDER BY pe.posting_date, pe.name
	"""
	rows = frappe.db.sql(sql, params, as_dict=True)
	return [[r.name, r.posting_date, r.get("party") or "", flt(r.received_amount), r.get("mode_of_payment") or "", r.get("reference_name") or ""] for r in rows]


def _detail_cash_receipts_cash_sales(params, cost_center):
	cond = _base_conditions(params["from_date"], params["to_date"], params["company"], cost_center, "pe.posting_date", "pe")
	cc = " AND pe.cost_center = %(cost_center)s" if cost_center else ""
	sql = """
		SELECT pe.name, pe.posting_date, pe.party, pe.received_amount, pe.mode_of_payment, per.reference_name
		FROM `tabPayment Entry` pe
		INNER JOIN `tabPayment Entry Reference` per ON per.parent = pe.name
		INNER JOIN `tabMode of Payment` mop ON mop.name = pe.mode_of_payment
		INNER JOIN `tabSales Invoice` si ON si.name = per.reference_name AND per.reference_doctype = 'Sales Invoice'
		WHERE pe.docstatus = 1 AND pe.payment_type = 'Receive' AND """ + cond + """
		AND per.reference_doctype = 'Sales Invoice' AND pe.posting_date <= si.posting_date AND mop.type = 'Cash'
		""" + cc + """
		ORDER BY pe.posting_date, pe.name
	"""
	rows = frappe.db.sql(sql, params, as_dict=True)
	return [[r.name, r.posting_date, r.get("party") or "", flt(r.received_amount), r.get("mode_of_payment") or "", r.get("reference_name") or ""] for r in rows]


def _detail_payments_petty_cash(params, cost_center):
	cond = _base_conditions(params["from_date"], params["to_date"], params["company"], cost_center, "pe.posting_date", "pe")
	cc = " AND pe.cost_center = %(cost_center)s" if cost_center else ""
	# Payment Entry (Pay, Cash, PI/PO)
	sql_pe = """
		SELECT 'Payment Entry' as doctype, pe.name, pe.posting_date, pe.party, pe.paid_amount, pe.mode_of_payment
		FROM `tabPayment Entry` pe
		INNER JOIN `tabPayment Entry Reference` per ON per.parent = pe.name
		INNER JOIN `tabMode of Payment` mop ON mop.name = pe.mode_of_payment
		WHERE pe.docstatus = 1 AND """ + cond + """
		AND pe.payment_type = 'Pay' AND per.reference_doctype IN ('Purchase Invoice', 'Purchase Order') AND mop.type = 'Cash'
		""" + cc + """
		ORDER BY pe.posting_date, pe.name
	"""
	rows_pe = frappe.db.sql(sql_pe, params, as_dict=True)
	# Purchase Invoice (is_paid, Cash)
	pi_cond = _base_conditions(params["from_date"], params["to_date"], params["company"], cost_center, "pi.posting_date", "pi")
	pi_cc = " AND pi.cost_center = %(cost_center)s" if cost_center else ""
	sql_pi = """
		SELECT 'Purchase Invoice' as doctype, pi.name, pi.posting_date, pi.supplier as party, pi.base_paid_amount as paid_amount, pi.mode_of_payment
		FROM `tabPurchase Invoice` pi
		INNER JOIN `tabMode of Payment` mop ON mop.name = pi.mode_of_payment
		WHERE pi.docstatus = 1 AND pi.is_paid = 1 AND """ + pi_cond + """
		AND mop.type = 'Cash'
		""" + pi_cc + """
		ORDER BY pi.posting_date, pi.name
	"""
	rows_pi = frappe.db.sql(sql_pi, params, as_dict=True)
	out = []
	for r in rows_pe:
		out.append([r.doctype, r.name, r.posting_date, r.get("party") or "", flt(r.paid_amount), r.get("mode_of_payment") or ""])
	for r in rows_pi:
		out.append([r.doctype, r.name, r.posting_date, r.get("party") or "", flt(r.paid_amount), r.get("mode_of_payment") or ""])
	out.sort(key=lambda x: (x[2], x[1]))
	return out


def _detail_bank_sales_receipts(params, cost_center):
	cond = _base_conditions(params["from_date"], params["to_date"], params["company"], cost_center, "pe.posting_date", "pe")
	cc = " AND pe.cost_center = %(cost_center)s" if cost_center else ""
	sql = """
		SELECT pe.name, pe.posting_date, pe.party, pe.received_amount, pe.mode_of_payment,
			GROUP_CONCAT(DISTINCT per.reference_name ORDER BY per.reference_name SEPARATOR ', ') as refs
		FROM `tabPayment Entry` pe
		INNER JOIN `tabPayment Entry Reference` per ON per.parent = pe.name
		INNER JOIN `tabMode of Payment` mop ON mop.name = pe.mode_of_payment
		WHERE pe.docstatus = 1 AND """ + cond + """
		AND pe.payment_type = 'Receive' AND per.reference_doctype IN ('Sales Invoice', 'Sales Order')
		AND (mop.type IS NULL OR mop.type != 'Cash')
		""" + cc + """
		GROUP BY pe.name, pe.posting_date, pe.party, pe.received_amount, pe.mode_of_payment
		ORDER BY pe.posting_date, pe.name
	"""
	rows = frappe.db.sql(sql, params, as_dict=True)
	return [[r.name, r.posting_date, r.get("party") or "", flt(r.received_amount), r.get("mode_of_payment") or "", r.get("refs") or ""] for r in rows]


def _detail_bank_sales_payments(params, cost_center):
	cond = _base_conditions(params["from_date"], params["to_date"], params["company"], cost_center, "pe.posting_date", "pe")
	cc = " AND pe.cost_center = %(cost_center)s" if cost_center else ""
	sql_pe = """
		SELECT 'Payment Entry' as doctype, pe.name, pe.posting_date, pe.party, pe.paid_amount, pe.mode_of_payment
		FROM `tabPayment Entry` pe
		INNER JOIN `tabMode of Payment` mop ON mop.name = pe.mode_of_payment
		WHERE pe.docstatus = 1 AND """ + cond + """
		AND pe.payment_type = 'Pay' AND (mop.type IS NULL OR mop.type != 'Cash')
		""" + cc + """
		ORDER BY pe.posting_date, pe.name
	"""
	rows_pe = frappe.db.sql(sql_pe, params, as_dict=True)
	pi_cond = _base_conditions(params["from_date"], params["to_date"], params["company"], cost_center, "pi.posting_date", "pi")
	pi_cc = " AND pi.cost_center = %(cost_center)s" if cost_center else ""
	sql_pi = """
		SELECT 'Purchase Invoice' as doctype, pi.name, pi.posting_date, pi.supplier as party, pi.base_paid_amount as paid_amount, pi.mode_of_payment
		FROM `tabPurchase Invoice` pi
		INNER JOIN `tabMode of Payment` mop ON mop.name = pi.mode_of_payment
		WHERE pi.docstatus = 1 AND pi.is_paid = 1 AND """ + pi_cond + """
		AND (mop.type IS NULL OR mop.type != 'Cash')
		""" + pi_cc + """
		ORDER BY pi.posting_date, pi.name
	"""
	rows_pi = frappe.db.sql(sql_pi, params, as_dict=True)
	out = []
	for r in rows_pe:
		out.append([r.doctype, r.name, r.posting_date, r.get("party") or "", flt(r.paid_amount), r.get("mode_of_payment") or ""])
	for r in rows_pi:
		out.append([r.doctype, r.name, r.posting_date, r.get("party") or "", flt(r.paid_amount), r.get("mode_of_payment") or ""])
	out.sort(key=lambda x: (x[2], x[1]))
	return out


def _detail_internal_transfer_out(params, cost_center):
	cond = _base_conditions(params["from_date"], params["to_date"], params["company"], cost_center, "pe.posting_date", "pe")
	cc = " AND pe.cost_center = %(cost_center)s" if cost_center else ""
	sql = """
		SELECT pe.name, pe.posting_date, pe.paid_from, pe.paid_to, pe.paid_amount
		FROM `tabPayment Entry` pe
		INNER JOIN `tabAccount` acc_from ON acc_from.name = pe.paid_from
		WHERE pe.docstatus = 1 AND pe.payment_type = 'Internal Transfer' AND """ + cond + """
		AND acc_from.account_type = 'Cash'
		""" + cc + """
		ORDER BY pe.posting_date, pe.name
	"""
	rows = frappe.db.sql(sql, params, as_dict=True)
	return [[r.name, r.posting_date, r.paid_from, r.paid_to, flt(r.paid_amount)] for r in rows]


def _detail_internal_transfer_in(params, cost_center):
	cond = _base_conditions(params["from_date"], params["to_date"], params["company"], cost_center, "pe.posting_date", "pe")
	cc = " AND pe.cost_center = %(cost_center)s" if cost_center else ""
	sql = """
		SELECT pe.name, pe.posting_date, pe.paid_from, pe.paid_to, pe.received_amount
		FROM `tabPayment Entry` pe
		INNER JOIN `tabAccount` acc_to ON acc_to.name = pe.paid_to
		WHERE pe.docstatus = 1 AND pe.payment_type = 'Internal Transfer' AND """ + cond + """
		AND acc_to.account_type = 'Cash'
		""" + cc + """
		ORDER BY pe.posting_date, pe.name
	"""
	rows = frappe.db.sql(sql, params, as_dict=True)
	return [[r.name, r.posting_date, r.paid_from, r.paid_to, flt(r.received_amount)] for r in rows]
