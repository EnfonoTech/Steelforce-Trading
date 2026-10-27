# Copyright (c) 2026, Enfono Technologies and contributors
# For license information, please see license.txt

"""Branch Bonus Eligibility: the client's bonus sheet, worked out from the invoices (sf_trading/bonus_eligibility.py).

Branch Summary: one row per branch -- eligible sales, target, achieved, bonus %, revenue allowed, the bonus and its
branch / operations shares. Calculation Sheet: one branch, line by line, in the sheet's own order and wording.
The outdoor sales persons and the collection days are picked in the report's filters.
A user held to some branches sees only those.
"""

import frappe
from frappe import _
from frappe.utils import getdate

from sf_trading.bonus_eligibility import BRANCH_SHARE, OPERATIONS_SHARE, calculate
from sf_trading.sales_target import allowed_branches

SUMMARY = "Branch Summary"
SHEET = "Calculation Sheet"


def execute(filters=None):
	filters = frappe._dict(filters or {})
	if not (filters.company and filters.from_date and filters.to_date):
		frappe.throw(_("Pick a Company and the period."))
	if getdate(filters.from_date) > getdate(filters.to_date):
		frappe.throw(_("From Date cannot be after To Date."))

	allowed = allowed_branches()
	branches = [filters.branch] if filters.branch else allowed
	if filters.branch and allowed is not None and filters.branch not in allowed:
		frappe.throw(_("You may not see branch {0}.").format(filters.branch), frappe.PermissionError)

	if filters.collection_days not in (None, "") and int(filters.collection_days) < 0:
		frappe.throw(_("Collected Within (Days) cannot be below 0."))
	outdoor = frappe.parse_json(filters.outdoor_sales_persons) if filters.outdoor_sales_persons else []
	result = calculate(filters.company, filters.from_date, filters.to_date, branches, outdoor, filters.collection_days)
	if filters.view == SHEET:
		if not filters.branch:
			frappe.throw(_("Pick a Branch for the Calculation Sheet."))
		return sheet_columns(), sheet_rows(result.get(filters.branch))
	return summary_columns(), sorted(result.values(), key=lambda r: r.branch)


def summary_columns():
	money = lambda fieldname, label, width=120: {"fieldname": fieldname, "label": label, "fieldtype": "Currency", "width": width}
	return [
		{"fieldname": "branch", "label": _("Branch"), "fieldtype": "Link", "options": "Branch", "width": 120},
		money("net_sales", _("Net Sales")),
		money("outdoor_total", _("Outdoor Sales")),
		money("eligible_sales", _("Eligible Sales")),
		money("target", _("Target")),
		{"fieldname": "achieved", "label": _("Achieved %"), "fieldtype": "Percent", "width": 100},
		{"fieldname": "bonus_percent", "label": _("Bonus %"), "fieldtype": "Float", "precision": 2, "width": 85},
		money("revenue_allowed", _("Revenue Allowed")),
		money("bonus", _("Bonus")),
		money("branch_share", _("Branch {0}%").format(BRANCH_SHARE)),
		money("operations_share", _("Operations {0}%").format(OPERATIONS_SHARE)),
	]


def sheet_columns():
	return [
		{"fieldname": "line", "label": _("Line"), "fieldtype": "Data", "width": 420},
		{"fieldname": "amount", "label": _("Amount"), "fieldtype": "Currency", "width": 160},
	]


def sheet_rows(b) -> list:
	if not b:
		return []
	days = b.collection_days
	rows = [
		(_("Bonus Eligibility Calculation"), None, 1),
		(_("1. Cash sales"), b.cash_sales, 0),
		(_("2. Credit sales"), b.credit_sales, 0),
		(_("Less: Credit sales not collected within {0} days").format(days), b.credit_not_collected, 0),
		(_("3. Less: Intercompany cash sales"), b.ic_cash, 0),
		(_("4. Less: Intercompany credit sales"), b.ic_credit, 0),
		(_("5. Less: Cash sales return"), b.cash_returns, 0),
		(_("6. Less: Credit sales return"), b.credit_returns, 0),
		(_("7. Net sales (1 + 2 - 3 - 4 - 5 - 6)"), b.net_sales, 1),
		(_("8. Outdoor sales:"), None, 0),
	]
	rows += [(f"    {person}", amount, 0) for person, amount in sorted(b.outdoor.items())]
	rows += [
		(_("8. Less: Outdoor sales (total)"), b.outdoor_total, 0),
		(_("9. Eligible sales (7 - 8)"), b.eligible_sales, 1),
		("", None, 0),
		(_("Revenue Allowed For Bonus"), None, 1),
		(_("1. Cash sales"), b.cash_sales, 0),
		(_("2. Less: Cash sales return"), b.cash_returns, 0),
		(_("3. Less: Outdoor sales cash (total)"), b.outdoor_cash, 0),
		(_("4. Add: Cash collected on credit sales excluding VAT (within {0} days)").format(days), b.credit_collected, 0),
		(_("5. Total revenue allowed for bonus (1 - 2 - 3 + 4)"), b.revenue_allowed, 1),
		("", None, 0),
		(_("Bonus"), None, 1),
		(_("Target"), b.target, 0),
		(_("Target achieved % (eligible sales / target)"), None, 0, f"{b.achieved:.2f}%"),
		(_("Bonus %"), None, 0, f"{b.bonus_percent:g}%"),
		(_("Bonus (revenue allowed x bonus %)"), b.bonus, 1),
		(_("Branch {0}%").format(BRANCH_SHARE), b.branch_share, 0),
		(_("Operations {0}%").format(OPERATIONS_SHARE), b.operations_share, 0),
	]
	out = []
	for row in rows:
		text, amount, bold = row[0], row[1], row[2]
		if len(row) > 3:
			text = f"{text}: {row[3]}"
		out.append({"line": text, "amount": amount, "bold": bold})
	return out
