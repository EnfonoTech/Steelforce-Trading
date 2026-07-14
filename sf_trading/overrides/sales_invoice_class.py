from erpnext.accounts.doctype.sales_invoice.sales_invoice import SalesInvoice


class CustomSalesInvoice(SalesInvoice):
	def check_credit_limit(self):
		if self.get("custom_payment_mode") == "Cash":
			return
		super().check_credit_limit()
