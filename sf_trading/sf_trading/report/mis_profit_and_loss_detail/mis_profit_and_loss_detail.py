# apps/sf_trading/sf_trading/report/mis_profit_and_loss_detail/mis_profit_and_loss_detail.py
"""MIS Profit and Loss Detail; see sf_trading/mis_profit_loss_detail.py."""

from sf_trading import mis_profit_loss_detail


def execute(filters=None):
	return mis_profit_loss_detail.execute(filters)
