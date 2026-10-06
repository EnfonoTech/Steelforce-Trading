"""Tests for the Supplier completeness rules (supplier_validation.py).

    bench --site <scratch-site> run-tests --module sf_trading.tests.test_supplier_validation
"""

from unittest import mock

import frappe
from frappe.tests.utils import FrappeTestCase

from sf_trading import supplier_validation
from sf_trading.supplier_validation import validate_supplier_at_transaction

GATE = "sf_trading.supplier_validation.validate_supplier_at_transaction"
BUYING_DOCTYPES = ("Supplier Quotation", "Purchase Order", "Purchase Receipt", "Purchase Invoice", "Payment Entry")


class Enforced:
	"""The rules ship switched off (SF Trading Settings > Enforce Supplier Completeness), so the
	tests of the rules themselves run with it on."""

	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		patcher = mock.patch.object(supplier_validation, "enforcement_enabled", return_value=True)
		patcher.start()
		cls.addClassCleanup(patcher.stop)


def attach(supplier_name, file_name="any-document.txt"):
	return frappe.get_doc(
		{
			"doctype": "File",
			"file_name": file_name,
			"content": "any document",
			"attached_to_doctype": "Supplier",
			"attached_to_name": supplier_name,
		}
	).insert(ignore_permissions=True)


class TestSupplierMaster(Enforced, FrappeTestCase):
	def _supplier(self, name, **fields):
		doc = {
			"doctype": "Supplier",
			"supplier_name": name,
			"supplier_group": frappe.db.get_value("Supplier Group", {"is_group": 0}, "name"),
			"supplier_type": "Company",
			"tax_id": frappe.generate_hash(length=12),
		}
		doc.update(fields)
		return frappe.get_doc(doc)

	def test_new_supplier_without_tax_id_is_refused(self):
		with self.assertRaises(frappe.ValidationError):
			self._supplier("Test Supplier No Tax", tax_id="").insert(ignore_permissions=True)

	def test_individual_supplier_needs_no_tax_id(self):
		supplier = self._supplier("Test Supplier Individual", supplier_type="Individual", tax_id="")
		supplier.insert(ignore_permissions=True)  # must not raise
		attach(supplier.name)
		supplier.reload()
		supplier.website = "https://example.com"
		supplier.save(ignore_permissions=True)  # must not raise

	def test_individual_supplier_tax_id_is_still_unique(self):
		first = self._supplier("Test Supplier Tax K").insert(ignore_permissions=True)
		with self.assertRaises(frappe.ValidationError):
			self._supplier(
				"Test Supplier Individual Dup", supplier_type="Individual", tax_id=first.tax_id
			).insert(ignore_permissions=True)

	def test_switching_individual_to_company_needs_tax_id(self):
		supplier = self._supplier("Test Supplier Switch", supplier_type="Individual", tax_id="")
		supplier.insert(ignore_permissions=True)
		attach(supplier.name)
		supplier.reload()
		supplier.supplier_type = "Company"
		with self.assertRaises(frappe.ValidationError):
			supplier.save(ignore_permissions=True)

	def test_blank_spaces_are_not_a_tax_id(self):
		with self.assertRaises(frappe.ValidationError):
			self._supplier("Test Supplier Space Tax", tax_id="   ").insert(ignore_permissions=True)

	def test_master_needs_no_phone_or_email(self):
		"""Phone and email are checked on the documents, not on the master."""
		supplier = self._supplier("Test Supplier Tax Only").insert(ignore_permissions=True)
		attach(supplier.name)
		supplier.reload()
		supplier.website = "https://example.com"
		supplier.save(ignore_permissions=True)  # must not raise

	def test_duplicate_tax_id_is_refused(self):
		first = self._supplier("Test Supplier Tax A").insert(ignore_permissions=True)
		with self.assertRaises(frappe.ValidationError):
			self._supplier("Test Supplier Tax B", tax_id=first.tax_id).insert(ignore_permissions=True)

	def test_duplicate_tax_id_ignores_surrounding_spaces(self):
		first = self._supplier("Test Supplier Tax C").insert(ignore_permissions=True)
		with self.assertRaises(frappe.ValidationError):
			self._supplier("Test Supplier Tax D", tax_id=f"  {first.tax_id} ").insert(ignore_permissions=True)

	def test_changing_to_a_taken_tax_id_is_refused(self):
		first = self._supplier("Test Supplier Tax E").insert(ignore_permissions=True)
		second = self._supplier("Test Supplier Tax F").insert(ignore_permissions=True)
		# a document attached, so the only thing left to refuse is the taken Tax ID
		attach(second.name)
		second.reload()
		second.tax_id = first.tax_id
		with self.assertRaises(frappe.ValidationError):
			second.save(ignore_permissions=True)

	def test_existing_duplicate_pair_stays_editable(self):
		"""A pair that already shares a Tax ID (manager override, or data from before this rule)
		must not leave both suppliers unsaveable."""
		first = self._supplier("Test Supplier Dup G").insert(ignore_permissions=True)
		second = self._supplier("Test Supplier Dup H").insert(ignore_permissions=True)
		frappe.db.set_value("Supplier", second.name, "tax_id", first.tax_id)
		attach(second.name)
		second.reload()
		second.website = "https://example.com"
		second.save(ignore_permissions=True)  # must not raise

	def test_manager_override_flag_allows_duplicate_on_create(self):
		first = self._supplier("Test Supplier Override I").insert(ignore_permissions=True)
		dup = self._supplier("Test Supplier Override J", tax_id=first.tax_id)
		dup.flags.allow_duplicate_tax_id = True
		dup.insert(ignore_permissions=True)  # must not raise

	def test_first_save_needs_no_attachment(self):
		supplier = self._supplier("Test Supplier First Save").insert(ignore_permissions=True)
		self.assertTrue(frappe.db.exists("Supplier", supplier.name))

	def test_edit_without_attachment_is_refused(self):
		supplier = self._supplier("Test Supplier Edit No File").insert(ignore_permissions=True)
		supplier.reload()
		supplier.website = "https://example.com"
		with self.assertRaises(frappe.ValidationError):
			supplier.save(ignore_permissions=True)

	def test_any_file_type_counts_as_the_document(self):
		supplier = self._supplier("Test Supplier Any File").insert(ignore_permissions=True)
		attach(supplier.name, "price-list.csv")
		supplier.reload()
		supplier.website = "https://example.com"
		supplier.save(ignore_permissions=True)  # must not raise

	def test_existing_supplier_without_tax_id_cannot_be_edited(self):
		supplier = self._supplier("Test Supplier Old No Tax").insert(ignore_permissions=True)
		frappe.db.set_value("Supplier", supplier.name, "tax_id", None)
		attach(supplier.name)
		supplier.reload()
		supplier.website = "https://example.com"
		with self.assertRaises(frappe.ValidationError):
			supplier.save(ignore_permissions=True)

	def test_existing_supplier_without_tax_id_can_still_be_frozen_or_disabled(self):
		supplier = self._supplier("Test Supplier Freeze").insert(ignore_permissions=True)
		frappe.db.set_value("Supplier", supplier.name, "tax_id", None)
		for field in ("is_frozen", "disabled"):
			supplier.reload()
			supplier.set(field, 1)
			supplier.save(ignore_permissions=True)  # must not raise
			self.assertEqual(frappe.db.get_value("Supplier", supplier.name, field), 1)
			frappe.db.set_value("Supplier", supplier.name, field, 0)


class TestSupplierAtTransaction(Enforced, FrappeTestCase):
	def _supplier(self, name, tax_id=True, document=True, supplier_type="Company"):
		supplier = frappe.get_doc(
			{
				"doctype": "Supplier",
				"supplier_name": name,
				"supplier_group": frappe.db.get_value("Supplier Group", {"is_group": 0}, "name"),
				"supplier_type": supplier_type,
				"tax_id": frappe.generate_hash(length=12) if tax_id else None,
			}
		).insert(ignore_permissions=True)
		if document:
			attach(supplier.name)
		return supplier.name

	def _contact(self, supplier, phone=None, email=None):
		contact = frappe.get_doc(
			{"doctype": "Contact", "first_name": supplier, "links": [{"link_doctype": "Supplier", "link_name": supplier}]}
		)
		if phone:
			contact.append("phone_nos", {"phone": phone, "is_primary_mobile_no": 1})
		if email:
			contact.append("email_ids", {"email_id": email, "is_primary": 1})
		contact.insert(ignore_permissions=True)

	def _check(self, supplier, doctype="Purchase Order"):
		if doctype == "Payment Entry":
			doc = frappe._dict(doctype=doctype, party_type="Supplier", party=supplier)
		else:
			doc = frappe._dict(doctype=doctype, supplier=supplier)
		validate_supplier_at_transaction(doc)

	def test_complete_supplier_passes(self):
		supplier = self._supplier("Test Supplier Complete")
		self._contact(supplier, phone="33445566", email="complete@example.com")
		for doctype in ("Supplier Quotation", "Purchase Order", "Purchase Receipt", "Purchase Invoice", "Payment Entry"):
			self._check(supplier, doctype)  # must not raise

	def test_missing_phone_is_refused(self):
		supplier = self._supplier("Test Supplier No Phone")
		self._contact(supplier, email="nophone@example.com")
		with self.assertRaises(frappe.ValidationError):
			self._check(supplier)

	def test_missing_email_is_refused(self):
		supplier = self._supplier("Test Supplier No Email")
		self._contact(supplier, phone="33445566")
		with self.assertRaises(frappe.ValidationError):
			self._check(supplier, "Purchase Invoice")

	def test_missing_tax_id_is_refused(self):
		supplier = self._supplier("Test Supplier Old No Tax Txn")
		frappe.db.set_value("Supplier", supplier, "tax_id", None)
		self._contact(supplier, phone="33445566", email="notax@example.com")
		with self.assertRaises(frappe.ValidationError):
			self._check(supplier)

	def test_individual_without_tax_id_passes(self):
		supplier = self._supplier("Test Supplier Individual Txn", tax_id=False, supplier_type="Individual")
		self._contact(supplier, phone="33445566", email="individual@example.com")
		self._check(supplier, "Purchase Invoice")  # must not raise

	def test_missing_document_is_refused(self):
		supplier = self._supplier("Test Supplier No Document", document=False)
		self._contact(supplier, phone="33445566", email="nodoc@example.com")
		with self.assertRaises(frappe.ValidationError):
			self._check(supplier, "Purchase Receipt")

	def test_payment_entry_to_incomplete_supplier_is_refused(self):
		supplier = self._supplier("Test Supplier Payment")
		with self.assertRaises(frappe.ValidationError):
			self._check(supplier, "Payment Entry")

	def test_payment_entry_to_a_customer_is_not_checked(self):
		validate_supplier_at_transaction(
			frappe._dict(doctype="Payment Entry", party_type="Customer", party="Anyone")
		)  # must not raise


class TestCreateSupplierDialog(Enforced, FrappeTestCase):
	def _upload(self):
		# what the dialog's Attach field leaves behind: a File attached to nothing yet
		return frappe.get_doc(
			{"doctype": "File", "file_name": "dialog-upload.txt", "content": "dialog upload"}
		).insert(ignore_permissions=True)

	def test_dialog_links_its_upload_and_saves_with_an_address(self):
		from sf_trading.api.supplier import create_supplier_with_address

		upload = self._upload()
		out = create_supplier_with_address(
			supplier_name="Test Supplier Dialog",
			mobile_no="33445566",
			email_id="dialog@example.com",
			tax_id=frappe.generate_hash(length=12),
			address_line1="Road 1",
			city="Manama",
			attachment=upload.file_url,
		)
		self.assertEqual(
			frappe.db.get_value("File", upload.name, ["attached_to_doctype", "attached_to_name"]),
			("Supplier", out["supplier"]),
		)

	def test_dialog_without_attachment_is_refused(self):
		from sf_trading.api.supplier import create_supplier_with_address

		with self.assertRaises(frappe.ValidationError):
			create_supplier_with_address(
				supplier_name="Test Supplier Dialog No File",
				mobile_no="33445566",
				email_id="dialog@example.com",
				tax_id=frappe.generate_hash(length=12),
			)

	def test_dialog_individual_needs_no_tax_id(self):
		from sf_trading.api.supplier import create_supplier_with_address

		out = create_supplier_with_address(
			supplier_name="Test Supplier Dialog Individual",
			mobile_no="33445566",
			email_id="dialog@example.com",
			buyer_kind="B2C (Individual)",
			attachment=self._upload().file_url,
		)
		self.assertEqual(frappe.db.get_value("Supplier", out["supplier"], "supplier_type"), "Individual")

	def test_dialog_company_without_tax_id_is_refused(self):
		from sf_trading.api.supplier import create_supplier_with_address

		with self.assertRaises(frappe.ValidationError):
			create_supplier_with_address(
				supplier_name="Test Supplier Dialog Company",
				mobile_no="33445566",
				email_id="dialog@example.com",
				buyer_kind="B2B (Company)",
				attachment=self._upload().file_url,
			)


class TestEnforcementSwitch(FrappeTestCase):
	"""Off by default: the rules reach existing suppliers with no bypass, and most of them are
	incomplete, so they are switched on only after the data is (Supplier Validation Gaps report).

	These drive the real SF Trading Settings value, not a mock of frappe.get_cached_doc, which every
	other hook on a Supplier insert (party accounts reads the Company) also goes through."""

	def setUp(self):
		self.addCleanup(self._set, 0)

	def _set(self, value):
		frappe.db.set_single_value("SF Trading Settings", supplier_validation.ENFORCE_FIELD, value)
		frappe.clear_document_cache("SF Trading Settings")

	def _new_supplier(self, name):
		return frappe.get_doc(
			{
				"doctype": "Supplier",
				"supplier_name": name,
				"supplier_group": frappe.db.get_value("Supplier Group", {"is_group": 0}, "name"),
				"supplier_type": "Company",
			}
		)

	def test_off_unless_ticked(self):
		self._set(0)
		self.assertFalse(supplier_validation.enforcement_enabled())
		self._set(1)
		self.assertTrue(supplier_validation.enforcement_enabled())

	def test_off_when_the_settings_do_not_exist(self):
		with mock.patch.object(frappe, "get_cached_doc", side_effect=frappe.DoesNotExistError):
			self.assertFalse(supplier_validation.enforcement_enabled())

	def test_incomplete_supplier_passes_every_gate_while_off(self):
		self._set(0)
		supplier = self._new_supplier("Test Supplier Switch Off")
		supplier.insert(ignore_permissions=True)  # no Tax ID: must not raise
		supplier.reload()
		supplier.website = "https://example.com"
		supplier.save(ignore_permissions=True)  # no file attached: must not raise
		for doctype in BUYING_DOCTYPES:
			party = {"party_type": "Supplier", "party": supplier.name} if doctype == "Payment Entry" else {"supplier": supplier.name}
			validate_supplier_at_transaction(frappe._dict(doctype=doctype, **party))  # must not raise

	def test_the_same_supplier_is_refused_once_on(self):
		self._set(0)
		supplier = self._new_supplier("Test Supplier Switch On")
		supplier.insert(ignore_permissions=True)
		self._set(1)
		with self.assertRaises(frappe.ValidationError):
			validate_supplier_at_transaction(frappe._dict(doctype="Purchase Order", supplier=supplier.name))
		supplier.reload()
		supplier.website = "https://example.com"
		with self.assertRaises(frappe.ValidationError):
			supplier.save(ignore_permissions=True)


class TestHookWiring(FrappeTestCase):
	"""The gate is only as good as its registration: assert it is really wired to every buying
	document and to the Supplier master, so removing a hook entry fails a test."""

	def _registered(self, doctype, event):
		return str(frappe.get_hooks("doc_events").get(doctype, {}).get(event))

	def test_gate_is_on_every_buying_document_and_payment_entry(self):
		for doctype in BUYING_DOCTYPES:
			self.assertIn(GATE, self._registered(doctype, "validate"), doctype)

	def test_master_rules_are_on_supplier(self):
		self.assertIn("sf_trading.supplier_validation.validate", self._registered("Supplier", "validate"))
		self.assertIn("sf_trading.supplier_validation.remind_attachment", self._registered("Supplier", "after_insert"))

	def test_every_registered_path_imports(self):
		import importlib

		for path in (GATE, "sf_trading.supplier_validation.validate", "sf_trading.supplier_validation.remind_attachment"):
			module, _, name = path.rpartition(".")
			self.assertTrue(callable(getattr(importlib.import_module(module), name)), path)
