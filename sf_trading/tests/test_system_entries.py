"""Tests for journals ERPNext makes itself skipping the approval workflow.

    bench --site <scratch-site> run-tests --module sf_trading.tests.test_system_entries
"""

from unittest import mock

import frappe
from frappe.tests.utils import FrappeTestCase

from sf_trading import system_entries as se

SCRAP = "erpnext.assets.doctype.asset.depreciation.scrap_asset"


def journal(**fields):
	return frappe.get_doc({"doctype": "Journal Entry", "company": "_SF Test Co", **fields})


class TestSystemEntries(FrappeTestCase):
	def setUp(self):
		self._on = True
		patcher = mock.patch.object(se, "switch_on", lambda: self._on)
		patcher.start()
		self.addCleanup(patcher.stop)

	# ---- the mark ------------------------------------------------------------------------------

	def test_depreciation_journal_is_marked(self):
		doc = journal(voucher_type="Depreciation Entry")
		doc.flags.planned_depr_entry = True
		se.mark_system_generated(doc)
		self.assertEqual(doc.is_system_generated, 1)

	def test_reversal_of_a_depreciation_is_marked(self):
		doc = journal()
		with mock.patch.object(frappe, "flags", frappe._dict(is_reverse_depr_entry=True)):
			se.mark_system_generated(doc)
		self.assertEqual(doc.is_system_generated, 1)

	def test_scrap_asset_request_marks_the_journal(self):
		doc = journal()
		with mock.patch.object(frappe.local, "form_dict", frappe._dict(cmd=SCRAP)):
			se.mark_system_generated(doc)
		self.assertEqual(doc.is_system_generated, 1)

	def test_another_request_does_not(self):
		doc = journal()
		with mock.patch.object(frappe.local, "form_dict", frappe._dict(cmd="frappe.client.insert")):
			se.mark_system_generated(doc)
		self.assertFalse(doc.is_system_generated)

	def test_a_journal_a_person_writes_is_never_marked(self):
		# even one that calls itself a Depreciation Entry: the voucher type is something a user can type
		doc = journal(voucher_type="Depreciation Entry")
		se.mark_system_generated(doc)
		self.assertFalse(doc.is_system_generated)

	def test_switched_off_marks_nothing(self):
		self._on = False
		doc = journal(voucher_type="Depreciation Entry")
		doc.flags.planned_depr_entry = True
		se.mark_system_generated(doc)
		self.assertFalse(doc.is_system_generated)

	# ---- the answer to permission_manager ------------------------------------------------------

	def test_system_journal_is_governed_by_no_workflow(self):
		self.assertEqual(se.workflow_applicability("Journal Entry", journal(is_system_generated=1)), {"applies": False})

	def test_ordinary_journal_has_no_opinion(self):
		self.assertIsNone(se.workflow_applicability("Journal Entry", journal()))

	def test_the_doctype_question_has_no_opinion(self):
		# answering it would take Journal Entry off the boot-time list of routed doctypes
		self.assertIsNone(se.workflow_applicability("Journal Entry", None))

	def test_other_doctypes_have_no_opinion(self):
		self.assertIsNone(se.workflow_applicability("Purchase Invoice", frappe._dict(is_system_generated=1)))

	def test_switched_off_the_journal_is_routed_again(self):
		self._on = False
		self.assertIsNone(se.workflow_applicability("Journal Entry", journal(is_system_generated=1)))

	def test_permission_manager_sees_the_answer(self):
		from permission_manager.permission_manager.workflow import workflow_applies

		self.assertFalse(workflow_applies("Journal Entry", journal(is_system_generated=1)))
		self.assertTrue(workflow_applies("Journal Entry", journal()))

	def test_hooks_are_registered(self):
		self.assertIn("sf_trading.system_entries.workflow_applicability", frappe.get_hooks("pm_workflow_applicability"))
		handlers = frappe.get_hooks("doc_events")["Journal Entry"]["before_validate"]
		self.assertIn("sf_trading.system_entries.mark_system_generated", handlers)
