# apps/sf_trading/sf_trading/report/mis_profit_and_loss/mis_profit_and_loss.py
"""MIS Profit and Loss; see sf_trading/mis_profit_loss.py."""

from sf_trading import mis_profit_loss


def execute(filters=None):
	return mis_profit_loss.execute(filters)
