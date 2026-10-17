# sf_trading/overrides/item_price_class.py
"""Item Price, with the duplicate check taught about branches (see sf_trading/item_price_branch.py).

ERPNext refuses two Item Prices that agree on item, price list, UOM, customer, supplier, batch,
packing unit and dates. With Applicable Branches two such prices may coexist as long as they do not
compete for the same documents: a branch price beside the general one, or prices for different
branches. Only the same scope twice is a duplicate.

`check_duplicates` below is ERPNext's own with the one change, so it runs only on a release whose
original has a fingerprint listed in item_price_branch.KNOWN_CHECK_DUPLICATES. On any other release it
hands back to the stock check (which refuses a branch's second price) and says so once in
logs/sf_trading.log, so an upgrade can never leave a stale copy answering.
"""

import frappe
from erpnext.stock.doctype.item_price.item_price import ItemPrice, ItemPriceDuplicateItem
from frappe import _
from frappe.query_builder import Criterion
from frappe.query_builder.functions import Cast_

from sf_trading import item_price_branch

_WARNED = {"done": False}


class CustomItemPrice(ItemPrice):
	def check_duplicates(self):
		if item_price_branch.fingerprint(ItemPrice.check_duplicates) not in item_price_branch.KNOWN_CHECK_DUPLICATES:
			if not _WARNED["done"]:
				_WARNED["done"] = True
				frappe.logger("sf_trading").warning(
					"item_price_class: ItemPrice.check_duplicates is not the erpnext source this was written against "
					"(sha1 %s); the stock check runs, so a branch's second price is refused",
					item_price_branch.fingerprint(ItemPrice.check_duplicates) or "unreadable",
				)
			return super().check_duplicates()

		item_price = frappe.qb.DocType("Item Price")

		query = (
			frappe.qb.from_(item_price)
			.select(item_price.name)
			.where(
				(item_price.item_code == self.item_code)
				& (item_price.price_list == self.price_list)
				& (item_price.name != self.name)
			)
		)
		data_fields = (
			"uom",
			"valid_from",
			"valid_upto",
			"customer",
			"supplier",
			"batch_no",
		)

		number_fields = ["packing_unit"]

		for field in data_fields:
			if self.get(field):
				query = query.where(item_price[field] == self.get(field))
			else:
				query = query.where(
					Criterion.any(
						[
							item_price[field].isnull(),
							Cast_(item_price[field], "varchar") == "",
						]
					)
				)

		for field in number_fields:
			if self.get(field):
				query = query.where(item_price[field] == self.get(field))
			else:
				query = query.where(
					Criterion.any(
						[
							item_price[field].isnull(),
							item_price[field] == 0,
						]
					)
				)

		clashes = [row.name for row in query.run(as_dict=True)]
		if not clashes:
			return

		# the one change: a clash counts only when the two prices would compete for the same documents
		mine = {row.branch for row in (self.get(item_price_branch.FIELD) or []) if row.branch}
		theirs = item_price_branch.branches_by_price(clashes)
		if any(item_price_branch.same_scope(mine, theirs.get(name, set())) for name in clashes):
			frappe.throw(
				_(
					"Item Price appears multiple times based on Price List, Supplier/Customer, Currency, Item, Batch, UOM, Qty, and Dates."
				)
				+ " "
				+ _("A price for a branch may sit beside the general one, but not beside another for the same branch."),
				ItemPriceDuplicateItem,
			)
