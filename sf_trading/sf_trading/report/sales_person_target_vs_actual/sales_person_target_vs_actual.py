# sf_trading/sf_trading/report/sales_person_target_vs_actual/sales_person_target_vs_actual.py
"""Each salesman's monthly target against what they actually sold.

Ask for a branch and only that branch's targets and invoices answer, which is how a
cross-branch seller is read one branch at a time. Ask for none and the person's whole number
is shown, branch-split records added together.

Invoices before 15 July 2026 carry no sales person -- the field was made mandatory that day,
and everything older was imported from ePromise. Those land in an "Unassigned" row rather than
being hidden, so a period that reaches back reads honestly.
"""

from sf_trading.sales_target import variance_dataset


def execute(filters=None):
	return variance_dataset(filters, "Sales Person")
