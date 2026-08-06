from erpnext.stock.doctype.delivery_note.delivery_note import DeliveryNote

from sf_trading.sdbnb import is_sdbnb_account


class CustomDeliveryNote(DeliveryNote):
	def check_expense_account(self, item):
		"""Let a Delivery Note book its cost to the SDBNB balance-sheet account.

		v15's stock controller insists a Delivery Note's expense account is a
		Profit or Loss account. Stock Delivered But Not Billed is deliberately an
		asset (Stock Assets) account, so that rule has to stand aside for it —
		upstream does the same, and goes further: on develop the Delivery Note GL
		composer switches P&L enforcement off for every row. Here it stands aside
		only for SDBNB rows, so every other misconfiguration is still caught.
		"""
		if item.get("expense_account") and is_sdbnb_account(item.get("expense_account")):
			return

		super().check_expense_account(item)
