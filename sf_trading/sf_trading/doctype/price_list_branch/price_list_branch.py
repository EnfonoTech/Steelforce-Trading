# Copyright (c) 2026, Enfono Technologies and contributors
# For license information, please see license.txt

from frappe.model.document import Document


class PriceListBranch(Document):
	"""One branch a Price List applies to — the twin of ERPNext's Price List Country.

	Empty table means the price list applies everywhere, exactly as Applicable for Countries
	reads. See sf_trading/branch_price_list.py for how a document's branch picks its list.
	"""

	pass
