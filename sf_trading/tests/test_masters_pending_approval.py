# sf_trading/tests/test_masters_pending_approval.py
"""The Masters Pending Approval report lists the masters the approval holds AND the existing masters that
still lack what the master rules ask for, whatever the switches in SF Trading Settings say.

    bench --site <site> run-tests --module sf_trading.tests.test_masters_pending_approval
"""

from datetime import date, datetime
from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase

from sf_trading import master_approval as ma
from sf_trading.sf_trading.report.masters_pending_approval import masters_pending_approval as report

START = datetime(2026, 10, 28, 0, 0)
OLD = datetime(2026, 1, 1, 0, 0)
NEW = datetime(2026, 11, 1, 0, 0)


def master(**fields):
	return frappe._dict({"name": "S-1", "supplier_name": "Supplier One", "supplier_group": "Local", "creation": OLD,
		"modified": OLD, "owner": "Administrator", "disabled": 0, ma.STATUS_FIELD: None, ma.APPROVED_ON: None,
		ma.BYPASSED: 0, **fields})


class Existing(FrappeTestCase):
	def setUp(self):
		self.enabled = {"Customer": True, "Supplier": True, "Item": False}
		self.patches = [
			patch.object(ma, "is_enabled", side_effect=lambda dt: self.enabled.get(dt, False)),
			patch.object(ma, "has_fields", return_value=True),
			patch.object(ma, "rules_from", return_value=START),
			patch.object(ma, "is_strict", return_value=False),
			patch.object(ma, "has_credit_limit", return_value=True),
			patch.object(ma, "workflow_approver_roles", return_value=("Supplier Approver",)),
		]
		for p in self.patches:
			p.start()

	def tearDown(self):
		for p in self.patches:
			p.stop()

	def test_an_older_master_is_listed_as_existing_without_strict(self):
		self.assertIsNone(report._held("Supplier", master()))
		row = report._listed("Supplier", master())
		self.assertEqual(row.stage_key, report.EXISTING)
		self.assertEqual(row.created_on, date(2026, 1, 1))

	def test_a_master_whose_switch_is_off_is_listed_as_existing_even_when_new(self):
		self.enabled["Supplier"] = False
		row = report._listed("Supplier", master(creation=NEW))
		self.assertEqual(row.stage_key, report.EXISTING)
		self.assertIn("switched off", row.stage)

	def test_a_master_the_approval_holds_stays_a_held_row(self):
		row = report._listed("Supplier", master(creation=NEW, **{ma.STATUS_FIELD: ma.DRAFT}))
		self.assertEqual(row.stage_key, ma.DRAFT)

	def test_an_approved_or_bypassed_master_is_not_listed(self):
		self.assertIsNone(report._listed("Supplier", master(**{ma.STATUS_FIELD: ma.APPROVED, ma.APPROVED_ON: NEW})))
		self.assertIsNone(report._listed("Supplier", master(**{ma.BYPASSED: 1})))

	def test_an_unstamped_approved_master_is_existing(self):
		row = report._listed("Supplier", master(**{ma.STATUS_FIELD: ma.APPROVED}))
		self.assertEqual(row.stage_key, report.EXISTING)

	def test_a_customer_without_a_credit_limit_is_not_listed(self):
		with patch.object(ma, "has_credit_limit", return_value=False):
			self.assertIsNone(report._existing("Customer", master(customer_name="C"), 0))

	def test_the_status_filter_and_the_include_box(self):
		row = report._listed("Supplier", master())
		self.assertFalse(report.wanted(row, frappe._dict(include_existing=0)))
		self.assertTrue(report.wanted(row, frappe._dict()))
		self.assertTrue(report.wanted(row, frappe._dict(status=report.EXISTING, include_existing=0)))
		self.assertFalse(report.wanted(row, frappe._dict(status=ma.DRAFT)))

	def test_only_existing_masters_with_gaps_are_kept(self):
		complete, lacking = master(name="S-OK"), master(name="S-GAP")
		missing = {"S-OK": [], "S-GAP": ["Payment Terms"]}

		def requirements(doc):
			return missing[doc.name]

		with patch.object(report, "customers", return_value=[]), \
			patch.object(report, "suppliers", return_value=[report._listed("Supplier", complete),
				report._listed("Supplier", lacking)]), \
			patch.object(frappe, "has_permission", return_value=True), \
			patch.object(frappe, "get_doc", side_effect=lambda dt, name: frappe._dict(name=name, doctype=dt)), \
			patch.object(ma, "missing_requirements", side_effect=requirements):
			_columns, rows, note, _chart, cards = report.execute({"party_type": "Supplier"})
		self.assertEqual([r.party for r in rows], ["S-GAP"])
		self.assertEqual(rows[0].missing, "Payment Terms")
		self.assertEqual({c["label"]: c["value"] for c in cards}["Existing, Incomplete"], 1)
		self.assertEqual({c["label"]: c["value"] for c in cards}["Waiting for Approval"], 0)
		self.assertIn("Existing Master", note)
