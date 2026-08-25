# Copyright (c) 2026, Enfono Technologies and contributors
# For license information, please see license.txt

from frappe.model.document import Document


class BranchConfigurationPriceList(Document):
	"""One price list a branch may sell or buy on.

	A branch can carry several -- a retail list and an offer list, say -- with one marked Default.
	The default is what a document for that branch is priced from; the rest are what the Price List
	field will let somebody choose. See sf_trading/branch_price_list.py.
	"""

	pass
