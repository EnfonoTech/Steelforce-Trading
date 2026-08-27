# sf_trading/sf_trading/doctype/sf_payment_unreconciliation/sf_payment_unreconciliation.py
"""The screen: Payment Reconciliation's shape, running backwards.

Single doctype with no submit, exactly like Payment Reconciliation — it is a tool, not a
document. The work lives in sf_trading/payment_unreconciliation.py; this only moves rows
between the form and that module.
"""

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt

from sf_trading.payment_unreconciliation import (
	DIMENSION_FIELDS,
	reconciled_entries,
	unreconcile,
)

ROW_FIELDS = ("company", "voucher_type", "voucher_no", "posting_date", "against_voucher_type",
              "against_voucher_no", "allocated_amount", "outstanding_amount", "currency",
              "account", "party_type", "party", "in_closed_period", "entry_type",
              # the insight layer
              "insight", "severity", "allocated_by", "allocated_on", "leg_count",
              "payment_branch", "payment_cost_center", "target_date", "target_total",
              "applied_total", "target_branch", "target_status", "unallocated_amount",
              "is_amendment", "imported", "days_gap", "leg_rows", "payers", "remarks",
              "undoable")


class SFPaymentUnreconciliation(Document):
	def _fetch(self):
		"""The current filters, applied. Kept separate so the refresh after unreconciling can
		reuse them without touching the stored document."""
		return reconciled_entries(
			company=self.company, party_type=self.party_type, party=self.party,
			account=self.receivable_payable_account, voucher_type=self.voucher_type,
			from_date=self.from_date, to_date=self.to_date,
			payment_no=self.payment_no, reconciled_within=self.reconciled_within,
			minimum_amount=self.minimum_amount, maximum_amount=self.maximum_amount,
			against_voucher_no=self.against_voucher_no, limit=self.limit or 500,
			dimensions={f: self.get(f) for f in DIMENSION_FIELDS},
		)

	@frappe.whitelist()
	def get_allocations(self):
		"""Fill the grid with every live allocation matching the filters."""
		rows = self._fetch()
		self.set("allocations", [])
		for row in rows:
			self.append("allocations", {f: row.get(f) for f in ROW_FIELDS})
		return len(rows)

	@frappe.whitelist()
	def unreconcile_selected(self):
		"""Undo the ticked rows, then re-read the grid so it shows what is left."""
		selected = [
			{f: row.get(f) for f in ROW_FIELDS}
			for row in self.allocations if row.select_row
		]
		if not selected:
			frappe.throw(_("Tick the allocations to undo first."))

		result = unreconcile(selected)
		frappe.db.commit()  # keep what succeeded, whatever the refresh below does

		# The filters live only in the browser: this is a Single that is never saved, exactly like
		# Payment Reconciliation. reload() therefore wiped company/party_type/party and the
		# refresh threw "Company, Party Type and Party are all needed" AFTER the work was already
		# committed -- an error message for a job that had in fact succeeded. Refetch in memory
		# and hand the rows back for the grid instead.
		rows = self._fetch()

		return {
			"done": result["done"],
			"failed": result["failed"],
			"total": flt(sum(flt(r.get("allocated_amount")) for r in result["done"])),
			"rows": [{f: row.get(f) for f in ROW_FIELDS} for row in rows],
		}
