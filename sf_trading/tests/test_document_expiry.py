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
	def test_queries_the_right_parenttype_and_window(self):
		"""_due_rows must scope by parenttype and pass a BETWEEN window, not just eyeball dates."""
		with patch(
			"sf_trading.document_expiry.frappe.get_cached_doc", return_value=_stub_settings(30, 7)
		), patch("sf_trading.document_expiry.frappe.db.sql", return_value=[]) as sql, patch(
			"sf_trading.document_expiry.frappe.get_all", return_value=[]
		):
			de._due_rows("Customer")
			args, _kwargs = sql.call_args
			query, values = args[0], args[1]
			self.assertIn("parenttype = %s", query)
			self.assertEqual(values[0], "Customer")

	def test_skips_rows_already_notified_today_via_sql_guard(self):
		"""The guard is in the WHERE clause itself, not a Python-side filter afterward."""
		with patch(
			"sf_trading.document_expiry.frappe.get_cached_doc", return_value=_stub_settings()
		), patch("sf_trading.document_expiry.frappe.db.sql", return_value=[]) as sql, patch(
			"sf_trading.document_expiry.frappe.get_all", return_value=[]
		):
			de._due_rows("Supplier")
			query = sql.call_args[0][0]
			self.assertIn("last_notified_on IS NULL OR last_notified_on != %s", query)
