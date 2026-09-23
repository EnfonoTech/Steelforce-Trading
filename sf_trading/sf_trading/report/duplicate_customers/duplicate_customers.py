# Copyright (c) 2026, Enfono Technologies and contributors
# For license information, please see license.txt
"""GS Issue 4 -- the read-only half: which customers already share a CR number.

Scoped to Customer only for now. Item and Supplier duplicates need their own matching logic (name
similarity for Item; Supplier has no CR field yet at all) rather than being forced through the same
CR-based query -- a separate follow-up, not guessed at here.
"""

from frappe import _

from sf_trading.duplicate_masters import duplicate_customers_by_cr


def execute(filters=None):
	filters = filters or {}
	columns = [
		{"label": _("CR Number"), "fieldname": "cr_number", "fieldtype": "Data", "width": 150},
		{"label": _("Count"), "fieldname": "count", "fieldtype": "Int", "width": 70},
		{"label": _("Customers"), "fieldname": "customers", "fieldtype": "Data", "width": 250},
		{"label": _("Names"), "fieldname": "names", "fieldtype": "Data", "width": 350},
		{"label": _("Any Active"), "fieldname": "any_active", "fieldtype": "Check", "width": 90},
	]
	data = duplicate_customers_by_cr(company=filters.get("company"))
	return columns, data
