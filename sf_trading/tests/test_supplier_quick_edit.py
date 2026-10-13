# sf_trading/tests/test_supplier_quick_edit.py
"""Quick Edit Billing Fields for a supplier (sf_trading/api/supplier_quick_edit.py).

    bench --site <site> run-tests --module sf_trading.tests.test_supplier_quick_edit
"""

from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase

from sf_trading import party_documents as pd
from sf_trading.api import supplier_quick_edit as sqe


class TestSupplierQuickEdit(FrappeTestCase):
	def setUp(self):
		group = frappe.db.get_value("Supplier Group", {"is_group": 0}, "name")
		if not group:
			self.skipTest("no Supplier Group")
		doc = frappe.get_doc({"doctype": "Supplier", "supplier_name": "_SF Quick Edit Supplier " + frappe.generate_hash(length=6),
			"supplier_type": "Company", "supplier_group": group})
		doc.flags.ignore_mandatory = True  # created bare, as an import would leave it
		self.supplier = doc.insert(ignore_permissions=True).name

	def test_the_banner_names_what_blocks_a_new_company_supplier(self):
		with patch.object(pd, "rules_from", return_value=frappe.utils.add_days(frappe.utils.now_datetime(), -1)):
			data = sqe.get_quick_edit_data(self.supplier)
		self.assertTrue(data["rules_apply"])
		self.assertTrue(data["needs_document"])
		self.assertTrue(data["payment_terms_required"])
		for item in ("Payment Terms", "Tax ID"):
			self.assertIn(item, data["blocking"])
		self.assertEqual(data["blocking"].count("Payment Terms"), 1)

	def test_saving_the_dialog_clears_the_blockers(self):
		terms = frappe.db.get_value("Payment Terms Template", {}, "name")
		if not terms:
			self.skipTest("no Payment Terms Template")
		upload = frappe.get_doc({"doctype": "File", "file_name": "sf-quick-edit-cr.txt", "content": "CR copy",
			"is_private": 1}).insert(ignore_permissions=True)
		with patch.object(pd, "rules_from", return_value=frappe.utils.add_days(frappe.utils.now_datetime(), -1)):
			result = sqe.save_quick_edit_data(self.supplier, {"mobile": "36000001", "email": "qe@example.com",
				"tax_id": "SFQE-" + frappe.generate_hash(length=8), "payment_terms": terms, "attachment": upload.file_url, "document_type": "Other"})
			data = sqe.get_quick_edit_data(self.supplier)
		self.assertEqual(result["warnings"], [])
		self.assertEqual(data["blocking"], [])
		self.assertTrue(data["has_document_row"])
		self.assertEqual(frappe.db.get_value("File", upload.name, "attached_to_name"), self.supplier)

	def test_an_expired_financial_document_is_listed_for_an_old_supplier_too(self):
		row = frappe._dict(document_type="Trade License", document_number="TL-1", expiry_date="2026-01-01")
		with patch.object(pd, "rules_from", return_value=None), patch.object(pd, "expired_financial_rows", return_value=[row]):
			data = sqe.get_quick_edit_data(self.supplier)
		self.assertFalse(data["rules_apply"])
		self.assertTrue(any("TL-1" in e for e in data["expired"]))


class TestPaymentTermsInTheBanner(FrappeTestCase):
	"""Every supplier carries Payment Terms (party_documents.missing_payment_terms): the banner lists them
	for an old supplier as for a new one, and the dialog marks the field."""

	def _blocking(self, supplier, rules_on=True):
		from sf_trading import supplier_validation as sv

		start = frappe.utils.add_days(frappe.utils.now_datetime(), -30) if rules_on else None
		with patch.object(pd, "rules_from", return_value=start), patch.object(sv, "enforcement_enabled", return_value=False), \
				patch.object(pd, "expired_financial_rows", return_value=[]), patch.object(pd, "has_document", return_value=True):
			return sqe.blocking(supplier)

	def _supplier(self, created, **values):
		from sf_trading.tests.test_master_rules import Party

		return Party("Supplier", created=created, supplier_type="Individual", **values)

	def test_an_old_supplier_without_terms_is_listed(self):
		state = self._blocking(self._supplier("2025-01-01"))
		self.assertFalse(state.applies)
		self.assertEqual(state.missing, ["Payment Terms"])

	def test_an_old_supplier_with_terms_blocks_nothing(self):
		self.assertEqual(self._blocking(self._supplier("2025-01-01", payment_terms="Net 30")).missing, [])

	def test_a_new_supplier_is_not_listed_twice(self):
		new = self._supplier(str(frappe.utils.today()))
		with patch.object(pd, "has_mobile", return_value=True), patch.object(pd, "has_email", return_value=True):
			state = self._blocking(new)
		self.assertEqual(state.missing.count("Payment Terms"), 1)

	def test_nothing_is_listed_once_the_master_rules_are_off(self):
		self.assertEqual(self._blocking(self._supplier("2025-01-01"), rules_on=False).missing, [])
