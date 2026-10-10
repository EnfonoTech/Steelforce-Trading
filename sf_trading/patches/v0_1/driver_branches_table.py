# apps/sf_trading/sf_trading/patches/v0_1/driver_branches_table.py
"""Carry each Delivery Person's single Branch into their new Branches table.

The Driver's Branch Cash Limits table became the list of branches a delivery person serves (see
sf_trading/driver_branches.py). Each person's old `custom_branch` becomes a row in it.

Active delivery people also get every branch they actually delivered for in the last 90 days. On
production three people already have invoices in both branches, which the single field could not
describe; without these rows the new branch check would refuse their next invoice in the second
branch. People who have left get only their old field's value, so a departed driver does not come
back into a picker.

Rows go in with Cash Limit 0 -- no cap of its own for that branch -- which is exactly what a branch
with no row meant before. A branch already in the table is left alone, so the patch can run twice.
"""

import frappe
from frappe.utils import add_days, today

BRANCH_ROW = "Driver Branch Cash Limit"
BRANCHES_FIELD = "custom_branch_cash_limits"
LOOKBACK_DAYS = 90


def execute():
	if not frappe.db.table_exists(BRANCH_ROW) or not frappe.db.has_column("Driver", "custom_branch"):
		return
	for driver, branches in wanted_branches().items():
		add_rows(driver, branches)


def wanted_branches() -> dict:
	"""driver -> branches to have, the old field's value first."""
	wanted = {}
	for driver in frappe.get_all(
		"Driver", filters={"custom_branch": ["is", "set"]}, fields=["name", "custom_branch"], order_by="name"
	):
		wanted.setdefault(driver.name, []).append(driver.custom_branch)

	active = set(frappe.get_all("Driver", filters={"status": "Active"}, pluck="name"))
	for row in recent_invoice_branches():
		if row.custom_driver not in active:
			continue
		branches = wanted.setdefault(row.custom_driver, [])
		if row.branch not in branches:
			branches.append(row.branch)
	return wanted


def recent_invoice_branches() -> list:
	"""Every (delivery person, branch) pair on a submitted invoice in the lookback window, busiest
	branch first for each person."""
	return frappe.db.sql(
		"""
		select custom_driver, branch, count(*) as invoices
		from `tabSales Invoice`
		where docstatus = 1
		  and ifnull(custom_driver, '') != ''
		  and ifnull(branch, '') != ''
		  and posting_date >= %s
		group by custom_driver, branch
		order by custom_driver, invoices desc
		""",
		(add_days(today(), -LOOKBACK_DAYS),),
		as_dict=True,
	)


def add_rows(driver: str, branches: list) -> None:
	scope = {"parenttype": "Driver", "parentfield": BRANCHES_FIELD, "parent": driver}
	existing = set(frappe.get_all(BRANCH_ROW, filters=scope, pluck="branch"))
	idx = frappe.db.count(BRANCH_ROW, scope)
	for branch in branches:
		if branch in existing or not frappe.db.exists("Branch", branch):
			continue
		idx += 1
		# db_insert, not the parent's save(): no validate hooks, no modified bump, no version row --
		# this restates data the Driver already held, it is not an edit by anyone.
		frappe.get_doc(dict(scope, doctype=BRANCH_ROW, branch=branch, cash_limit=0, idx=idx)).db_insert()
		existing.add(branch)
