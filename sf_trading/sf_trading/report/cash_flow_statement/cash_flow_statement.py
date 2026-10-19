# apps/sf_trading/sf_trading/report/cash_flow_statement/cash_flow_statement.py
"""Cash Flow Statement, direct or indirect, read from the General Ledger -- see sf_trading/cash_flow_statement.py."""

from sf_trading import cash_flow_statement


def execute(filters=None):
	return cash_flow_statement.execute(filters)
