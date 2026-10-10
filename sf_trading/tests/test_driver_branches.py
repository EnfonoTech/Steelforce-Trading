# sf_trading/tests/test_driver_branches.py
"""Delivery Person Branches table: the patch, the picker filter, the branch rule, the permissions.

    bench --site <scratch-site> run-tests --module sf_trading.tests.test_driver_branches
"""

import json
from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase

from sf_trading import driver_branches as db
from sf_trading.patches.v0_1 import driver_branches_table as migration

BRANCH_A = "_Test DB Branch A"
BRANCH_B = "_Test DB Branch B"
PERMITTED = "sf_trading.driver_branches.permitted_branches"
SOMEONE = "someone@example.com"


def _branch(name):
	if not frappe.db.exists("Branch", name):
		frappe.get_doc({"doctype": "Branch", "branch": name}).insert(ignore_permissions=True)


def _driver(full_name, branches=(), status="Active", old_branch=None):
	doc = frappe.get_doc({"doctype": "Driver", "full_name": full_name, "status": status})
	for branch in branches:
		doc.append(db.BRANCHES_FIELD, {"branch": branch, "cash_limit": 0})
	doc.insert(ignore_permissions=True)
	if old_branch:
		frappe.db.set_value("Driver", doc.name, "custom_branch", old_branch, update_modified=False)
	return doc.name


def _rows(*branches):
	return frappe._dict({db.BRANCHES_FIELD: [frappe._dict(branch=b) for b in branches]})


class TestDriverBranches(FrappeTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		_branch(BRANCH_A)
		_branch(BRANCH_B)

	# ── the patch ─────────────────────────────────────────────────────────────────────────────

	def test_patch_copies_the_old_branch_once(self):
		name = _driver("_Test DB Old Field", old_branch=BRANCH_A)
		with patch.object(migration, "recent_invoice_branches", return_value=[]):
			migration.execute()
			migration.execute()
		self.assertEqual(db.driver_branches(name), [BRANCH_A])

	def test_patch_adds_recent_branches_for_active_people_only(self):
		active = _driver("_Test DB Active", old_branch=BRANCH_A)
		left = _driver("_Test DB Left", status="Left")
		usage = [
			frappe._dict(custom_driver=active, branch=BRANCH_B),
			frappe._dict(custom_driver=left, branch=BRANCH_B),
		]
		with patch.object(migration, "recent_invoice_branches", return_value=usage):
			migration.execute()
		self.assertEqual(db.driver_branches(active), [BRANCH_A, BRANCH_B])
		self.assertEqual(db.driver_branches(left), [])

	# ── the picker ────────────────────────────────────────────────────────────────────────────

	def test_the_list_filter_offers_the_people_of_the_branch_once_each(self):
		only_a = _driver("_Test DB Only A", [BRANCH_A])
		only_b = _driver("_Test DB Only B", [BRANCH_B])
		both = _driver("_Test DB Both", [BRANCH_A, BRANCH_B])
		found = frappe.get_list(
			"Driver", filters=[[db.BRANCH_ROW, "branch", "=", BRANCH_A]], pluck="name", limit_page_length=0
		)
		self.assertIn(only_a, found)
		self.assertIn(both, found)
		self.assertNotIn(only_b, found)
		self.assertEqual(len(found), len(set(found)))

	def test_link_search_honours_the_same_filter(self):
		from frappe.desk.search import search_link

		both = _driver("_Test DB Search Both", [BRANCH_A, BRANCH_B])
		only_b = _driver("_Test DB Search B", [BRANCH_B])
		results = search_link(
			"Driver", "", filters=json.dumps([[db.BRANCH_ROW, "branch", "=", BRANCH_A]]), page_length=500
		)
		values = [row["value"] for row in results]
		self.assertIn(both, values)
		self.assertNotIn(only_b, values)

	# ── the Driver form rules ─────────────────────────────────────────────────────────────────

	def test_a_branch_appears_once(self):
		doc = frappe.get_doc({"doctype": "Driver", "full_name": "_Test DB Twice", "status": "Active"})
		doc.append(db.BRANCHES_FIELD, {"branch": BRANCH_A})
		doc.append(db.BRANCHES_FIELD, {"branch": BRANCH_A})
		with self.assertRaises(frappe.ValidationError):
			db.validate_branches(doc)

	def test_a_new_person_starts_in_their_old_field_branch(self):
		# new_doc, not get_doc(dict): only new_doc marks the document new, as insert() does
		doc = frappe.new_doc("Driver")
		doc.update({"full_name": "_Test DB New", "status": "Active", "custom_branch": BRANCH_A})
		db.validate_branches(doc)
		self.assertEqual([row.branch for row in doc.get(db.BRANCHES_FIELD)], [BRANCH_A])

	def test_a_branch_user_adds_only_their_own_branches(self):
		doc = frappe.get_doc({"doctype": "Driver", "full_name": "_Test DB Branch User", "status": "Active"})
		doc.append(db.BRANCHES_FIELD, {"branch": BRANCH_A})
		doc.append(db.BRANCHES_FIELD, {"branch": BRANCH_B})
		with patch(PERMITTED, return_value=[BRANCH_A]):
			with self.assertRaises(frappe.ValidationError):
				db.validate_branches(doc)
			doc.set(db.BRANCHES_FIELD, [{"branch": BRANCH_A}])
			db.validate_branches(doc)  # must not raise

	def test_rows_already_there_are_not_held_against_a_branch_user(self):
		name = _driver("_Test DB Shared", [BRANCH_A, BRANCH_B])
		doc = frappe.get_doc("Driver", name)
		doc._doc_before_save = frappe.get_doc("Driver", name)
		with patch(PERMITTED, return_value=[BRANCH_A]):
			db.validate_branches(doc)  # must not raise

	# ── the invoice / order rule ──────────────────────────────────────────────────────────────

	def test_the_document_branch_must_be_served(self):
		only_a = _driver("_Test DB Serves A", [BRANCH_A])
		with self.assertRaises(frappe.ValidationError):
			db.validate_driver_serves_branch(frappe._dict(custom_driver=only_a, branch=BRANCH_B))
		db.validate_driver_serves_branch(frappe._dict(custom_driver=only_a, branch=BRANCH_A))
		db.validate_driver_serves_branch(frappe._dict(custom_driver=only_a, branch=BRANCH_B, is_return=1))

	def test_a_person_with_no_branches_is_not_checked(self):
		unplaced = _driver("_Test DB No Branch")
		db.validate_driver_serves_branch(frappe._dict(custom_driver=unplaced, branch=BRANCH_B))

	# ── who sees whom ─────────────────────────────────────────────────────────────────────────

	def test_branch_users_see_the_people_of_their_branches(self):
		only_a = _driver("_Test DB Perm A", [BRANCH_A])
		only_b = _driver("_Test DB Perm B", [BRANCH_B])
		both = _driver("_Test DB Perm Both", [BRANCH_A, BRANCH_B])
		unplaced = _driver("_Test DB Perm None")
		with patch(PERMITTED, return_value=[BRANCH_A]):
			condition = db.permission_query_conditions(SOMEONE)
		# the condition is this app's own hook output, built from escaped values
		visible = set(frappe.db.sql("select name from `tabDriver` where " + condition, pluck=True))
		self.assertTrue({only_a, both, unplaced} <= visible)
		self.assertNotIn(only_b, visible)

	def test_unrestricted_users_get_no_condition(self):
		with patch(PERMITTED, return_value=None):
			self.assertEqual(db.permission_query_conditions(SOMEONE), "")

	def test_the_form_follows_the_list_rule(self):
		with patch(PERMITTED, return_value=[BRANCH_A]):
			self.assertFalse(db.has_permission(_rows(BRANCH_B), "read", SOMEONE))
			self.assertTrue(db.has_permission(_rows(BRANCH_A, BRANCH_B), "read", SOMEONE))
			self.assertIsNone(db.has_permission(_rows(), "read", SOMEONE))
		with patch(PERMITTED, return_value=None):
			self.assertIsNone(db.has_permission(_rows(BRANCH_B), "read", SOMEONE))

	def test_permitted_branches_counts_only_permissions_that_apply_to_driver(self):
		perms = {
			"Branch": [
				frappe._dict(doc=BRANCH_A, applicable_for=None),
				frappe._dict(doc=BRANCH_B, applicable_for="Sales Invoice"),
			]
		}
		with patch(
			"frappe.core.doctype.user_permission.user_permission.get_user_permissions", return_value=perms
		):
			self.assertEqual(db.permitted_branches(SOMEONE), [BRANCH_A])
		self.assertIsNone(db.permitted_branches("Administrator"))

	def test_the_form_endpoint_checks_read_permission(self):
		name = _driver("_Test DB Api", [BRANCH_A])
		self.assertEqual(db.get_driver_branches(name), [BRANCH_A])
		with patch("sf_trading.driver_branches.frappe.has_permission", return_value=False):
			with self.assertRaises(frappe.PermissionError):
				db.get_driver_branches(name)
