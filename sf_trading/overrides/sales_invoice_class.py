from erpnext.accounts.doctype.sales_invoice.sales_invoice import SalesInvoice


class CustomSalesInvoice(SalesInvoice):
	def validate_credit_limit_on_save(self):
		if self.get("custom_payment_mode") == "Cash":
			return
		super().validate_credit_limit_on_save()
