# sf_trading/sf_trading/doctype/sales_target/sales_target.py
"""One target: a branch's or a salesman's twelve monthly numbers for one fiscal year.

Named after what it is about rather than a series, so the list reads `SFSB - 2026`,
`Akhil - 2026`, `Prakash - SFSS - 2026`, and somebody hunting for their own number finds it by
typing their name.

`branch` carries two meanings, on purpose. For a branch target it IS the target. For a person's
target it is the optional split -- blank means their whole number across branches, set means a
separate target per branch, which is how Prakash and Shihab Ck (who both sell out of SFSB and
SFSS) can be given either shape. `dimension_value` is the one field every report groups on, so
the reports never have to know which of the two applies.
"""

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt

from sf_trading.sales_target import MONTHS, month_slots


class SalesTarget(Document):
	def autoname(self):
		self.set_dimension_value()
		parts = [self.dimension_value]
		if self.dimension_type == "Sales Person" and self.branch:
			parts.append(self.branch)
		parts.append(self.fiscal_year)
		self.name = " - ".join(str(p) for p in parts if p)

	def validate(self):
		self.set_dimension_value()
		self.validate_dimension()
		self.validate_months()
		self.set_total()
		self.validate_duplicate()

	def set_dimension_value(self):
		if self.dimension_type == "Branch":
			self.sales_person = None
			self.dimension_value = self.branch
		else:
			self.dimension_value = self.sales_person

	def validate_dimension(self):
		if not self.dimension_value:
			frappe.throw(_("Name the {0} this target belongs to.").format(_(self.dimension_type)))

		if self.dimension_type == "Sales Person" and frappe.db.get_value(
			"Sales Person", self.sales_person, "is_group"
		):
			frappe.throw(
				_("{0} is a group in the Sales Person tree, not a salesman. Set the target on the "
				  "people under it.").format(frappe.bold(self.sales_person)))

	def validate_months(self):
		allowed = {s.month for s in month_slots(self.fiscal_year)}
		seen = set()
		for row in self.targets:
			if row.month not in allowed:
				frappe.throw(_("Row #{0}: {1} is not a month of fiscal year {2}.").format(
					row.idx, row.month, self.fiscal_year))
			if row.month in seen:
				frappe.throw(_("Row #{0}: {1} is listed twice. One row per month.").format(
					row.idx, row.month))
			seen.add(row.month)
			if flt(row.target_amount) < 0:
				frappe.throw(_("Row #{0}: a target cannot be negative.").format(row.idx))
		self.targets.sort(key=lambda r: MONTHS.index(r.month))
		for i, row in enumerate(self.targets, 1):
			row.idx = i

	def set_total(self):
		self.total_target = sum(flt(r.target_amount) for r in self.targets)

	def validate_duplicate(self):
		filters = {
			"company": self.company,
			"fiscal_year": self.fiscal_year,
			"dimension_type": self.dimension_type,
			"dimension_value": self.dimension_value,
			"branch": self.branch or "",
		}
		# On a new record the twin carries the SAME autoname, so excluding by name would exclude
		# the very row being guarded against -- and the user would meet a raw
		# "Duplicate entry ... for key PRIMARY" from the insert instead of a sentence.
		if not self.is_new():
			filters["name"] = ["!=", self.name]
		existing = frappe.db.get_value("Sales Target", filters, "name")
		if existing:
			frappe.throw(_("{0} already carries a target for {1}. Edit {2} instead of adding a "
			               "second one — both would be counted.").format(
				frappe.bold(self.dimension_value), self.fiscal_year, frappe.bold(existing)))
