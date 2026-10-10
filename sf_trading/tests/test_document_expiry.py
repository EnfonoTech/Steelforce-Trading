"""Tests for the supporting-document expiry reminder window.

    bench --site <scratch-site> run-tests --module sf_trading.tests.test_document_expiry
"""

from unittest.mock import MagicMock, patch

from frappe.tests.utils import FrappeTestCase

from sf_trading import document_expiry as de


def _stub_settings(before=30, after=7):
	settings = MagicMock()
	settings.get.side_effect = lambda k: {
		"document_expiry_before_days": before,
		"document_expiry_after_days": after,
	}.get(k)
	return settings


class TestExpiryWindow(FrappeTestCase):
	def test_defaults_when_settings_missing(self):
		with patch("sf_trading.document_expiry.frappe.get_cached_doc", side_effect=de.frappe.DoesNotExistError):
			self.assertEqual(de._before_days(), 30)
			self.assertEqual(de._after_days(), 7)

	def test_reads_configured_window(self):
		with patch(
			"sf_trading.document_expiry.frappe.get_cached_doc", return_value=_stub_settings(45, 10)
		):
			self.assertEqual(de._before_days(), 45)
			self.assertEqual(de._after_days(), 10)


class TestDueRows(FrappeTestCase):
	"""_due_rows reads validated rows of one party type and keeps those inside each row's own window."""

	def _run(self, rows, parenttype="Customer"):
		import frappe
		from frappe.utils import add_days, getdate, nowdate

		today = getdate(nowdate())
		seen = {}

		def get_all(doctype, **kwargs):
			if doctype == "Customer Supporting Document":
				seen["filters"] = kwargs.get("filters")
				return [frappe._dict(r, expiry_date=add_days(today, r["offset"]),
					last_notified_on=today if r.get("notified_today") else None) for r in rows]
			return []

		with patch("sf_trading.document_expiry.frappe.get_cached_doc", return_value=_stub_settings(30, 7)), patch(
			"sf_trading.document_expiry.frappe.get_all", side_effect=get_all
		):
			due = de._due_rows(parenttype)
		return seen["filters"], [r.name for r in due]

	def test_scopes_by_party_type_and_validated_rows(self):
		filters, _due = self._run([], parenttype="Supplier")
		self.assertEqual(filters["parenttype"], "Supplier")
		self.assertEqual(filters["validate_expiry"], 1)

	def test_each_row_keeps_its_own_window(self):
		rows = [
			{"name": "soon", "offset": 20, "days_ahead": 0, "grace_days": 0},
			{"name": "too-far", "offset": 40, "days_ahead": 0, "grace_days": 0},
			{"name": "far-but-its-own-window", "offset": 40, "days_ahead": 45, "grace_days": 0},
			{"name": "just-expired", "offset": -5, "days_ahead": 0, "grace_days": 0},
			{"name": "past-its-grace", "offset": -5, "days_ahead": 0, "grace_days": 3},
			{"name": "already-told-today", "offset": 10, "days_ahead": 0, "grace_days": 0, "notified_today": 1},
		]
		_filters, due = self._run(rows)
		self.assertEqual(sorted(due), ["far-but-its-own-window", "just-expired", "soon"])
