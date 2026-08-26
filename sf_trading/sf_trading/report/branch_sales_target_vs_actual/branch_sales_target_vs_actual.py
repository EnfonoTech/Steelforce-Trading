# sf_trading/sf_trading/report/branch_sales_target_vs_actual/branch_sales_target_vs_actual.py
"""Each branch's monthly target against what it actually sold.

Both this and the sales-person report are one call into sf_trading.sales_target, so a figure
here can never disagree with the same figure on a card or a chart.
"""

from sf_trading.sales_target import variance_dataset


def execute(filters=None):
	return variance_dataset(filters, "Branch")
