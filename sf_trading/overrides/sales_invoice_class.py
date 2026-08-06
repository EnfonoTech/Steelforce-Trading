from erpnext.accounts.doctype.sales_invoice.sales_invoice import SalesInvoice

from sf_trading.sdbnb import get_sdbnb_gl_entries


class CustomSalesInvoice(SalesInvoice):
	def check_credit_limit(self):
		if self.get("custom_payment_mode") == "Cash":
			return
		super().check_credit_limit()

	def get_gl_entries(self, warehouse_account=None):
		"""Core entries, plus the Stock Delivered But Not Billed reversal.

		Billing a Delivery Note that parked its cost in the SDBNB account has to
		credit that account and debit COGS. Done here rather than in an on_submit
		hook so a Repost Accounting Ledger rebuilds the same entries.
		"""
		gl_entries = super().get_gl_entries(warehouse_account=warehouse_account)

		sdbnb_entries = get_sdbnb_gl_entries(self)
		if sdbnb_entries:
			# core runs this over its own map before returning; ours needs it too
			self.set_transaction_currency_and_rate_in_gl_map(sdbnb_entries)
			gl_entries.extend(sdbnb_entries)

		return gl_entries
