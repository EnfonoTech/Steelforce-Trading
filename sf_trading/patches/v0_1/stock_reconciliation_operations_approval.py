# sf_trading/patches/v0_1/stock_reconciliation_operations_approval.py
"""A Stock Reconciliation is approved by the Inventory Head and then by the Operations Head.

Client, 2026-10-13: a second approval, by the Operations Head (Shafeek). The workflow was created on
11 Oct with the Inventory Head's approval posting the count; approval_routing.ensure_workflows never
touches a workflow that exists, so a site that has it gets the second stage here, once. Idempotent --
a workflow that already has the stage, or that someone has reshaped, is left as it is.

Giving the role to a person is deliberately not done here: who the Operations Head is stays a site
decision (User > Roles).
"""

from sf_trading.approval_routing import ensure_operations_approval


def execute():
	ensure_operations_approval()
