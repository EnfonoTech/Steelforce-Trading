# sf_trading/sf_trading/doctype/sales_target/sales_target.py
"""One target: a branch's or a salesman's twelve monthly numbers for one fiscal year.

Named after what it is about rather than a series, so the list reads as
`SFSB - 2026`, `Akhil - 2026`, `Prakash - SFSS - 2026` and a person hunting for their own
number finds it by typing their name.
"""

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt

from sf_trading.sales_target import DIMENSIONS, MONTHS, month_slots


class SalesTarget(Document):
	def autoname(self):
		parts = [self.dimension_value]
		if self.dimension_type == "Sales Person" and self.branch:
			parts.append(self.branch)
		parts.append(self.fiscal_year)
		self.name = " - ".join(str(p) for p in parts if p)

	def before_insert(self):
		# frappe checks Dynamic Links in insert() BEFORE it runs validate(), so a doctype set
		# in validate() arrives too late and the insert dies with "Dimension DocType must be
		# set first". On update the order is the other way round, hence both hooks.
		self.set_dimension_doctype()

	def validate(self):
		self.set_dimension_doctype()
		self.validate_dimension()
		self.validate_months()
		self.set_total()
		self.validate_duplicate()

	def set_dimension_doctype(self):
		self.dimension_doctype = DIMENSIONS[self.dimension_type]["doctype"]

	def validate_dimension(self):
		if not frappe.db.exists(self.dimension_doctype, self.dimension_value):
			frappe.throw(_("{0} {1} does not exist").format(
				_(self.dimension_doctype), frappe.bold(self.dimension_value)))

		if self.dimension_type == "Branch":
			# a branch target is the branch's whole number; a second branch field would be a
			# contradiction, not a refinement
			self.branch = None
			return

		if frappe.db.get_value("Sales Person", self.dimension_value, "is_group"):
			frappe.throw(
				_("{0} is a group in the Sales Person tree, not a salesman. Set the target on the "
				  "people under it.").format(frappe.bold(self.dimension_value)))

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
		existing = frappe.db.get_value(
			"Sales Target",
			{
				"company": self.company,
				"fiscal_year": self.fiscal_year,
				"dimension_type": self.dimension_type,
				"dimension_value": self.dimension_value,
				"branch": self.branch or "",
				"name": ["!=", self.name],
			},
			"name",
		)
		if existing:
			frappe.throw(_("{0} already carries a target for {1}. Edit {2} instead of adding a "
			               "second one — two records would both be counted.").format(
				frappe.bold(self.dimension_value), self.fiscal_year, frappe.bold(existing)))
