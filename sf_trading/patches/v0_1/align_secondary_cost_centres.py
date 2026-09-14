# sf_trading/patches/v0_1/align_secondary_cost_centres.py
"""Point the invoice's two unused cost-centre fields at the invoice's own cost centre.

`write_off_cost_center` and `loyalty_redemption_cost_center` are filled in by nobody: a User
Permission marked `is_default` becomes a document default, so both inherit the branch of whoever
raised the document while `cost_center` is corrected to the branch being billed. Eight invoices on
production ended up naming the other branch, and because Frappe gates its list on EVERY parent Link
field, each one was invisible to every user of its own branch. See `sf_trading.user_permission_fields`,
which stops it happening again.

Nothing accounting-wise moves here: the fields are read only when money flows through them, and on
this site nothing ever does -- `write_off_amount` and `loyalty_amount` are zero on all eight, and
the site carries no POS Profile and no Loyalty Program at all. An invoice where either amount is
NOT zero is left exactly as it is: there the cost centre is a posting decision, not a stray default.

Written with `frappe.db.set_value(..., update_modified=False)`. The documents are submitted, neither
field carries `allow_on_submit`, and `save()` on one of them would rebuild its ledger from the
document -- the one thing this site must never do.
"""

import frappe
from frappe.utils import flt

FIELDS = ("write_off_cost_center", "loyalty_redemption_cost_center")


def execute():
	invoice = frappe.qb.DocType("Sales Invoice")
	rows = (
		frappe.qb.from_(invoice)
		.select(
			invoice.name,
			invoice.cost_center,
			invoice.write_off_amount,
			invoice.loyalty_amount,
			invoice.write_off_cost_center,
			invoice.loyalty_redemption_cost_center,
		)
		.where(
			(invoice.docstatus < 2)
			& invoice.cost_center.notnull()
			& (
				(invoice.write_off_cost_center != invoice.cost_center)
				| (invoice.loyalty_redemption_cost_center != invoice.cost_center)
			)
		)
	).run(as_dict=True)

	aligned = skipped = 0
	for row in rows:
		if flt(row.write_off_amount) or flt(row.loyalty_amount):
			skipped += 1
			continue
		for field in FIELDS:
			if row.get(field) and row.get(field) != row.cost_center:
				frappe.db.set_value(
					"Sales Invoice", row.name, field, row.cost_center, update_modified=False
				)
		aligned += 1

	frappe.logger("sf_trading").info(
		f"align_secondary_cost_centres: {aligned} invoice(s) aligned, {skipped} left alone"
	)
