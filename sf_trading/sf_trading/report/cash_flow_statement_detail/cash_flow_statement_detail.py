# apps/sf_trading/sf_trading/report/cash_flow_statement_detail/cash_flow_statement_detail.py
"""Cash Flow Statement Detail -- the vouchers behind a figure of the Cash Flow Statement; see
sf_trading/cash_flow_statement_detail.py."""

from sf_trading import cash_flow_statement_detail


def execute(filters=None):
	return cash_flow_statement_detail.execute(filters)
