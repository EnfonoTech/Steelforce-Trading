# sf_trading/patches/v0_1/stock_transfer_segregation.py
"""A stock transfer is sent by the source warehouse's keeper and accepted by someone else.

Client, 2026-10-20: MAT-STE-2026-06511 was raised, sent and accepted by one user. The workflow is a
site record, so the two conditions are added to it here, once (approval_routing.ensure_transfer_segregation).
Idempotent; a site without the workflow is left as it is.
"""

from sf_trading.approval_routing import ensure_transfer_segregation


def execute():
	ensure_transfer_segregation()
