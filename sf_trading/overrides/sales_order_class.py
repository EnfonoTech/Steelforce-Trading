# sf_trading/overrides/sales_order_class.py
"""Sales Order, with the same credit rule the Sales Invoice already had.

Core calls `check_credit_limit()` from three places on this doctype -- `on_submit`, the status
update, and `accounts_controller.update_child_table` when a row is added to a submitted order --
so the rule belongs on the method, not on one hook.
"""

from erpnext.selling.doctype.sales_order.sales_order import SalesOrder

from sf_trading.credit_limit import skip_credit_limit


class CustomSalesOrder(SalesOrder):
	def check_credit_limit(self):
		# A cash order is paid at the counter; core counts every unbilled order as exposure and
		# has no idea what custom_payment_mode means, so it blocked cash sales for any customer
		# whose ledger was already near their limit.
		if skip_credit_limit(self):
			return
		super().check_credit_limit()
