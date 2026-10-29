import frappe
from frappe.tests.utils import FrappeTestCase

from sf_trading.branch_defaults import propagate_dimensions_to_items


class TestTaxRowBranch(FrappeTestCase):
	"""A tax row is seeded with the user's default Branch permission; the header's branch must win."""

	def _doc(self, branch, cost_center="601 - SFSB - SFB"):
		return frappe._dict(
			branch=branch,
			cost_center=cost_center,
			project=None,
			items=[frappe._dict(branch="", cost_center="", project=None)],
			taxes=[frappe._dict(branch="SFSS", cost_center="Main - SFB")],
		)

	def test_header_branch_overwrites_stray_tax_row_branch(self):
		doc = self._doc("SFSB")
		propagate_dimensions_to_items(doc)
		self.assertEqual(doc.taxes[0].branch, "SFSB")
		self.assertEqual(doc.taxes[0].cost_center, "601 - SFSB - SFB")
		self.assertEqual(doc["items"][0].branch, "SFSB")

	def test_blank_header_branch_leaves_tax_row_alone(self):
		doc = self._doc("")
		propagate_dimensions_to_items(doc)
		self.assertEqual(doc.taxes[0].branch, "SFSS")
