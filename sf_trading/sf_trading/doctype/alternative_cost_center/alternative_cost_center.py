# sf_trading/sf_trading/doctype/alternative_cost_center/alternative_cost_center.py
# Copyright (c) 2026, Enfono Technologies and contributors
# For license information, please see license.txt

from frappe.model.document import Document


class AlternativeCostCenter(Document):
	"""A cost centre that a voucher can be charged to IN ADDITION to its own (sf_trading/alternative_cost_center.py).

	ERPNext does not let a Cost Center be an Accounting Dimension, so every cost centre of every company has
	a mirror here, kept in step by sf_trading; this master is the accounting dimension, which is what makes
	the choice travel from the voucher row to the General Ledger entry."""

	pass
