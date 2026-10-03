# sf_trading/stock_reconciliation_account.py
"""One fixed Difference Account per company for a Stock Reconciliation.

The client: a Stock Reconciliation whose Purpose is "Stock Reconciliation" may post its difference
to one account only, the one set on the Company. No such account on the Company, no Stock
Reconciliation at all -- it cannot even be saved. Opening Stock is untouched (core gives it a
Temporary account of its own).

Three pieces:
  * Company field `custom_stock_reconciliation_difference_account`, Accounts tab, under Default
    Cost of Goods Sold Account.
  * The form fills the account itself: core's get_difference_account -- what the form calls on
    company / purpose change -- is overridden to return this account (override_whitelisted_methods).
  * Stock Reconciliation validate refuses a missing setting or any other account, so an API,
    import or console save is held to the same rule as the form.
"""

from __future__ import annotations

import frappe
from frappe import _
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields

ACCOUNT_FIELD = "custom_stock_reconciliation_difference_account"
PURPOSE = "Stock Reconciliation"


def ensure_custom_fields():
	create_custom_fields(
		{
			"Company": [
				{
					"fieldname": ACCOUNT_FIELD,
					"label": "Stock Reconciliation Difference Account",
					"fieldtype": "Link",
					"options": "Account",
					"insert_after": "default_expense_account",
					"description": "The only Difference Account allowed on a Stock Reconciliation "
					"(Purpose: Stock Reconciliation). Without it, no Stock Reconciliation can be saved.",
					"ignore_user_permissions": 1,
					"no_copy": 1,
				}
			]
		},
		update=True,
	)


def get_company_account(company: str) -> str | None:
	return frappe.get_cached_value("Company", company, ACCOUNT_FIELD) if company else None


@frappe.whitelist()
def get_difference_account(purpose, company):
	"""Override of erpnext...stock_reconciliation.get_difference_account: the form fills this
	company's own account for a Stock Reconciliation, and core's answer for anything else."""
	from erpnext.stock.doctype.stock_reconciliation.stock_reconciliation import (
		get_difference_account as core_get_difference_account,
	)

	if purpose == PURPOSE:
		return get_company_account(company)
	return core_get_difference_account(purpose, company)


def set_difference_account(doc, _method=None):
	"""Stock Reconciliation before_validate: fill a blank account with the company's own, before
	core's validate would fill it with Stock Adjustment Account instead."""
	if doc.purpose == PURPOSE and not doc.expense_account:
		doc.expense_account = get_company_account(doc.company)


def validate_difference_account(doc, _method=None):
	"""Stock Reconciliation validate."""
	if doc.purpose != PURPOSE:
		return

	account = get_company_account(doc.company)
	if not account:
		frappe.throw(
			_("Set the Stock Reconciliation Difference Account on Company {0} (Accounts tab) before creating a Stock Reconciliation.").format(
				frappe.bold(doc.company)
			),
			title=_("Difference Account Not Set"),
		)

	if doc.expense_account != account:
		frappe.throw(
			_("Difference Account must be {0}, the account set on Company {1}.").format(
				frappe.bold(account), frappe.bold(doc.company)
			),
			title=_("Wrong Difference Account"),
		)


def validate_company_account(doc, _method=None):
	"""Company validate: the chosen account must be a ledger of this company."""
	account = doc.get(ACCOUNT_FIELD)
	if not account:
		return

	account_company, is_group = frappe.get_cached_value("Account", account, ["company", "is_group"])
	if account_company != doc.name:
		frappe.throw(
			_("Stock Reconciliation Difference Account {0} does not belong to Company {1}.").format(
				frappe.bold(account), frappe.bold(doc.name)
			)
		)
	if is_group:
		frappe.throw(
			_("Stock Reconciliation Difference Account {0} is a group account. Select a ledger.").format(
				frappe.bold(account)
			)
		)
