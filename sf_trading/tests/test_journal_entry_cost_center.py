"""Tests for the Journal Entry row cost centre following the row's Branch.

    bench --site <scratch-site> run-tests --module sf_trading.tests.test_journal_entry_cost_center
"""

import frappe
from frappe.tests.utils import FrappeTestCase

from sf_trading import journal_entry_cost_center as jecc

COMPANY = "_SF Test Co"
COMPANY_DEFAULT = "Main - SFT"
SFSB_CC = "SFSB - SFT"
SFSS_CC = "SFSS - SFT"
SFWH_CC = "SFWH - SFT"


def make_row(branch=None, cost_center=None):
	row = frappe._dict({"branch": branch, "cost_center": cost_center})
	row.meta = frappe._dict({"has_field": lambda f: True})
	row.get = lambda key, default=None: row.__dict__.get(key, default)
	return row


def make_doc(rows, company=COMPANY, is_system_generated=0):
	doc = frappe._dict(
		{"company": company, "accounts": rows, "is_system_generated": is_system_generated}
	)
	doc.get = lambda key, default=None: doc.__dict__.get(key, default)
	return doc


class TestJournalEntryCostCenter(FrappeTestCase):
	def setUp(self):
		# the module reads three things from the database; each test states them outright rather
		# than depending on whichever site the suite happens to run against.
		# SFWH lists two cost centres, exactly as production does.
		self._branches = {"SFSB": [SFSB_CC], "SFSS": [SFSS_CC],
		                  "SFWH": [SFWH_CC, COMPANY_DEFAULT], "SFXX": []}
		self._user_branch = None

		self._real = (jecc.branch_cost_centers, jecc.branch_cost_center, jecc.user_branch,
		              jecc._permitted, frappe.get_cached_value)
		self.addCleanup(self._restore)

		jecc.branch_cost_centers = lambda branch: self._branches.get(branch) or []
		jecc.branch_cost_center = lambda branch: (self._branches.get(branch) or [None])[0]
		jecc.user_branch = lambda user=None: self._user_branch
		jecc._permitted = lambda cost_center, user=None: True

		real_get_cached_value = frappe.get_cached_value

		def fake(doctype, name, fieldname, *args, **kwargs):
			if doctype == "Company" and fieldname == "cost_center":
				return COMPANY_DEFAULT
			return real_get_cached_value(doctype, name, fieldname, *args, **kwargs)

		frappe.get_cached_value = fake

	def _restore(self):
		(jecc.branch_cost_centers, jecc.branch_cost_center, jecc.user_branch,
		 jecc._permitted, frappe.get_cached_value) = self._real

	def test_branch_on_the_row_decides(self):
		rows = [make_row("SFSB", COMPANY_DEFAULT), make_row("SFSS", COMPANY_DEFAULT)]
		jecc.set_cost_center_from_branch(make_doc(rows))
		self.assertEqual([r.cost_center for r in rows], [SFSB_CC, SFSS_CC])

	def test_a_wrong_cost_center_is_corrected_not_only_a_blank_one(self):
		# the whole defect is that the field was FULL of the company default, never blank
		rows = [make_row("SFSB", SFSS_CC), make_row("SFSB", None)]
		jecc.set_cost_center_from_branch(make_doc(rows))
		self.assertEqual([r.cost_center for r in rows], [SFSB_CC, SFSB_CC])

	def test_a_branch_keeps_the_alternatives_it_declared(self):
		# SFWH's own configuration lists Main second, so a warehouse row sitting on Main was a
		# choice the branch itself allows -- and stays
		rows = [make_row("SFWH", COMPANY_DEFAULT)]
		jecc.set_cost_center_from_branch(make_doc(rows))
		self.assertEqual(rows[0].cost_center, COMPANY_DEFAULT)

	def test_a_centre_the_branch_never_declared_is_corrected(self):
		rows = [make_row("SFWH", SFSS_CC)]
		jecc.set_cost_center_from_branch(make_doc(rows))
		self.assertEqual(rows[0].cost_center, SFWH_CC)

	def test_branch_with_no_configured_cost_center_is_left_alone(self):
		rows = [make_row("SFXX", COMPANY_DEFAULT)]
		jecc.set_cost_center_from_branch(make_doc(rows))
		self.assertEqual(rows[0].cost_center, COMPANY_DEFAULT)

	def test_branchless_user_keeps_choosing(self):
		# nothing is written for a user who belongs to no branch: the form clears the pre-filled
		# default and the mandatory field asks. Guessing head office is what this fixes.
		rows = [make_row(None, COMPANY_DEFAULT)]
		jecc.set_cost_center_from_branch(make_doc(rows))
		self.assertEqual(rows[0].cost_center, COMPANY_DEFAULT)
		self.assertIsNone(rows[0].branch)

	def test_users_own_branch_fills_a_row_that_names_none(self):
		self._user_branch = "SFSS"
		rows = [make_row(None, COMPANY_DEFAULT), make_row(None, None)]
		jecc.set_cost_center_from_branch(make_doc(rows))
		self.assertEqual([r.cost_center for r in rows], [SFSS_CC, SFSS_CC])
		self.assertEqual([r.branch for r in rows], ["SFSS", "SFSS"],
		                 "the row should say why it carries that cost centre")

	def test_a_chosen_cost_center_survives_when_the_row_names_no_branch(self):
		self._user_branch = "SFSS"
		rows = [make_row(None, SFSB_CC)]
		jecc.set_cost_center_from_branch(make_doc(rows))
		self.assertEqual(rows[0].cost_center, SFSB_CC, "somebody picked this; it is not ours to move")

	def test_system_generated_entries_get_no_convenience_fill(self):
		# a write-off or credit note erpnext raised for itself must not inherit the branch of
		# whoever happened to click the button
		self._user_branch = "SFSS"
		rows = [make_row(None, COMPANY_DEFAULT)]
		jecc.set_cost_center_from_branch(make_doc(rows, is_system_generated=1))
		self.assertEqual(rows[0].cost_center, COMPANY_DEFAULT)

	def test_system_generated_still_honours_a_branch_it_states(self):
		self._user_branch = "SFSS"
		rows = [make_row("SFSB", COMPANY_DEFAULT)]
		jecc.set_cost_center_from_branch(make_doc(rows, is_system_generated=1))
		self.assertEqual(rows[0].cost_center, SFSB_CC)

	def test_a_cost_center_the_user_may_not_use_is_never_forced(self):
		# forcing it would make validate_link refuse the value and block the entire save
		jecc._permitted = lambda cost_center, user=None: cost_center != SFSB_CC
		rows = [make_row("SFSB", COMPANY_DEFAULT)]
		jecc.set_cost_center_from_branch(make_doc(rows))
		self.assertEqual(rows[0].cost_center, COMPANY_DEFAULT)

	def test_no_rows_is_not_an_error(self):
		jecc.set_cost_center_from_branch(make_doc([]))
