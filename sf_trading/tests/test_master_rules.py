"""New-master rules, the supporting-documents grid, and order cancellation reasons.

    bench --site <site> run-tests --module sf_trading.tests.test_master_rules
"""

from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import add_days, get_datetime, getdate, nowdate

from sf_trading import order_cancellation as oc
from sf_trading import party_documents as pd

TODAY = getdate(nowdate())


class Row(frappe._dict):
	def is_new(self):
		return bool(self.get("__new", True))


class Party(frappe._dict):
	"""Enough of a Customer / Supplier for the rules."""

	def __init__(self, doctype, new=False, created=None, rows=(), **values):
		super().__init__(doctype=doctype, name="P-1", creation=created, **values)
		self.flags = frappe._dict()
		self._new = new
		self[pd.TABLE] = [Row(idx=i + 1, **r) for i, r in enumerate(rows)]

	def is_new(self):
		return self._new


def _values(answers):
	"""get_value answering {(doctype, fieldname-or-None): value}; everything else goes to the database."""
	real = frappe.db.get_value

	def get_value(doctype, *args, **kwargs):
		field = args[1] if len(args) > 1 else kwargs.get("fieldname")
		for (dt, fieldname), value in answers.items():
			if dt == doctype and (fieldname is None or fieldname == field):
				return value
		return real(doctype, *args, **kwargs)

	return get_value


START = get_datetime(add_days(TODAY, -10))


class TestWhoIsGoverned(FrappeTestCase):
	def setUp(self):
		patch.object(pd, "rules_from", return_value=START).start()
		self.addCleanup(patch.stopall)

	def test_suppliers_always_customers_only_when_b2b(self):
		self.assertTrue(pd.is_governed(Party("Supplier")))
		self.assertTrue(pd.is_governed(Party("Customer", custom_vat_registration_number="300000000000003")))
		self.assertFalse(pd.is_governed(Party("Customer")))

	def test_new_means_created_on_or_after_the_start(self):
		self.assertTrue(pd.is_new_master(Party("Supplier", new=True)))
		self.assertTrue(pd.is_new_master(Party("Supplier", created=str(TODAY))))
		self.assertFalse(pd.is_new_master(Party("Supplier", created=str(add_days(TODAY, -30)))))

	def test_rules_off_without_a_start_date(self):
		with patch.object(pd, "rules_from", return_value=None):
			self.assertFalse(pd.is_new_master(Party("Supplier", new=True)))


class TestMasterSave(FrappeTestCase):
	def setUp(self):
		patch.object(pd, "rules_from", return_value=START).start()
		patch.object(pd, "_types", return_value={}).start()
		self.addCleanup(patch.stopall)

	def test_a_new_supplier_needs_payment_terms_at_creation(self):
		with self.assertRaises(frappe.ValidationError) as caught:
			pd.validate_master(Party("Supplier", new=True, supplier_name="New Co"))
		self.assertIn("Payment Terms", str(caught.exception))
		# the full form has no editable mobile: that, like the document, is asked for after creation
		pd.validate_master(Party("Supplier", new=True, payment_terms="CASH"))

	def test_after_creation_the_document_and_mobile_are_needed(self):
		saved = dict(created=str(TODAY), payment_terms="CASH")
		with self.assertRaises(frappe.ValidationError) as caught:
			pd.validate_master(Party("Supplier", **saved))
		self.assertIn("Supporting Document", str(caught.exception))
		self.assertIn("Mobile", str(caught.exception))
		pd.validate_master(Party("Supplier", custom_mobile_no="36000000",
			rows=[{"document_type": "Other", "attachment": "/private/files/cr.pdf"}], **saved))

	def test_a_company_supplier_needs_its_tax_id_on_any_save(self):
		with self.assertRaises(frappe.ValidationError):
			pd.validate_master(Party("Supplier", created=str(add_days(TODAY, -400)), supplier_type="Company"))
		pd.validate_master(Party("Supplier", created=str(add_days(TODAY, -400)), supplier_type="Individual"))

	def test_an_ignore_mandatory_creator_stands_aside(self):
		party = Party("Supplier", new=True)
		party.flags.ignore_mandatory = True
		pd.validate_master(party)

	def test_a_row_without_its_file_does_not_count(self):
		saved = dict(created=str(TODAY), payment_terms="CASH", custom_mobile_no="36000000")
		with self.assertRaises(frappe.ValidationError):
			pd.validate_master(Party("Supplier", rows=[{"document_type": "Other"}], **saved))

	def test_an_old_master_is_not_blocked(self):
		pd.validate_master(Party("Supplier", created=str(add_days(TODAY, -400))))

	def test_a_b2c_customer_is_not_governed(self):
		pd.validate_master(Party("Customer", new=True))

	def test_a_new_b2b_customer_needs_payment_terms(self):
		with self.assertRaises(frappe.ValidationError):
			pd.validate_master(Party("Customer", new=True, custom_vat_registration_number="300000000000003"))

	def test_an_import_stands_aside(self):
		frappe.flags.in_import = True
		try:
			pd.validate_master(Party("Supplier", new=True))
		finally:
			frappe.flags.in_import = False


class TestRows(FrappeTestCase):
	def _validate(self, row, kind=None, doctype="Supplier"):
		types = {row.get("document_type"): frappe._dict(kind)} if kind else {}
		with patch.object(pd, "_types", return_value=types), patch.object(pd, "rules_from", return_value=None):
			pd.validate_master(Party(doctype, rows=[row]))

	def test_a_validated_expiry_needs_its_date(self):
		with self.assertRaises(frappe.ValidationError):
			self._validate({"document_type": "Trade License", "validate_expiry": 1})
		self._validate({"document_type": "Trade License", "validate_expiry": 1, "expiry_date": str(TODAY)})

	def test_issue_after_expiry_is_refused(self):
		with self.assertRaises(frappe.ValidationError):
			self._validate({"document_type": "X", "issue_date": str(TODAY), "expiry_date": str(add_days(TODAY, -1))})

	def test_a_customer_only_type_on_a_supplier(self):
		with self.assertRaises(frappe.ValidationError):
			self._validate({"document_type": "Credit Application"}, {"applies_to": "Customer", "disabled": 0})

	def test_an_id_needs_the_date_of_birth(self):
		kind = {"applies_to": pd.APPLIES_TO_BOTH, "needs_date_of_birth": 1, "disabled": 0}
		with self.assertRaises(frappe.ValidationError):
			self._validate({"document_type": "Passport"}, kind)
		self._validate({"document_type": "Passport", "date_of_birth": "1990-01-01"}, kind)

	def test_financial_implication_needs_validation(self):
		row = Row(idx=1, document_type="X", financial_implication=1, validate_expiry=0)
		with patch.object(pd, "_types", return_value={}), patch.object(pd, "rules_from", return_value=None):
			party = Party("Supplier")
			party[pd.TABLE] = [row]
			pd.validate_master(party)
		self.assertEqual(row.financial_implication, 0)


class TestTransactions(FrappeTestCase):
	def test_who_is_checked_on_which_document(self):
		self.assertEqual(pd._party_of(frappe._dict(doctype="Payment Entry", party_type="Customer", payment_type="Receive",
			party="C")), (None, None))
		self.assertEqual(pd._party_of(frappe._dict(doctype="Payment Entry", party_type="Supplier", payment_type="Pay",
			party="S")), ("Supplier", "S"))
		self.assertEqual(pd._party_of(frappe._dict(doctype="Sales Invoice", customer="C")), ("Customer", "C"))
		self.assertEqual(pd._party_of(frappe._dict(doctype="Purchase Order", supplier="S")), ("Supplier", "S"))

	def test_a_return_or_an_opening_invoice_is_never_refused(self):
		pd.validate_party_at_transaction(frappe._dict(doctype="Purchase Invoice", supplier="ANY", is_return=1))
		pd.validate_party_at_transaction(frappe._dict(doctype="Purchase Invoice", supplier="ANY", is_opening="Yes"))

	def test_an_expired_financial_document_blocks(self):
		supplier = frappe.db.get_value("Supplier", {}, "name")
		if not supplier:
			self.skipTest("no supplier")
		expired = [frappe._dict(document_type="Trade License", document_number="TL-1",
			expiry_date=str(add_days(TODAY, -5)), grace_days=0)]
		with patch.object(pd, "expired_financial_rows", return_value=expired), patch.object(pd, "is_new_master", return_value=False):
			with self.assertRaises(frappe.ValidationError) as caught:
				pd.validate_party_at_transaction(frappe._dict(doctype="Purchase Order", supplier=supplier))
		self.assertIn("Trade License", str(caught.exception))

	def test_grace_days_keep_a_document_valid(self):
		rows = [frappe._dict(document_type="A", document_number=None, expiry_date=str(add_days(TODAY, -3)), grace_days=5),
			frappe._dict(document_type="B", document_number=None, expiry_date=str(add_days(TODAY, -3)), grace_days=1)]
		with patch.object(frappe, "get_all", return_value=rows):
			left = pd.expired_financial_rows("Supplier", "S")
		self.assertEqual([r.document_type for r in left], ["B"])


class TestDialogRow(FrappeTestCase):
	def test_the_type_decides_validation_and_expiry(self):
		kind = frappe._dict(applies_to=pd.APPLIES_TO_BOTH, validate_expiry=1, days_ahead=30, grace_days=0,
			financial_implication=1, disabled=0)
		with patch.object(frappe.db, "get_value", side_effect=_values({(pd.TYPE_DOCTYPE, None): kind})):
			with self.assertRaises(frappe.ValidationError):
				pd.dialog_document_row("Supplier", "Trade License", "TL", None, "/private/files/x.pdf")
			row = pd.dialog_document_row("Supplier", "Trade License", "TL", str(TODAY), "/private/files/x.pdf")
		self.assertEqual((row["validate_expiry"], row["financial_implication"], row["days_ahead"]), (1, 1, 30))


class TestCancellationReason(FrappeTestCase):
	def _compose(self, reason, details=None, doctype="Sales Order", master=None):
		master = master if master is not None else frappe._dict(applies_to=oc.BOTH, needs_details=0, disabled=0)
		with patch.object(frappe.db, "get_value", side_effect=_values({(oc.MASTER, None): master})):
			return oc.compose_remark(doctype, reason, details)

	def test_reason_and_details_make_the_remark(self):
		self.assertEqual(self._compose("Duplicate Order"), "Duplicate Order")
		self.assertEqual(self._compose("Duplicate Order", "raised twice by the counter"),
			"Duplicate Order - raised twice by the counter")

	def test_other_needs_details(self):
		with self.assertRaises(frappe.ValidationError):
			self._compose(oc.OTHER)
		self.assertEqual(self._compose(oc.OTHER, "customer moved abroad"), "Other - customer moved abroad")

	def test_a_purchase_reason_is_refused_on_a_sales_order(self):
		with self.assertRaises(frappe.ValidationError):
			self._compose("Supplier Cannot Supply", master=frappe._dict(applies_to="Purchase Order", needs_details=0, disabled=0))

	def test_unknown_and_disabled_reasons(self):
		with self.assertRaises(frappe.ValidationError):
			self._compose("Nope", master=frappe._dict())
		with self.assertRaises(frappe.ValidationError):
			self._compose("Old", master=frappe._dict(applies_to=oc.BOTH, needs_details=0, disabled=1))

	def test_seeded_reasons_exist(self):
		if not frappe.db.exists("DocType", oc.MASTER):
			self.skipTest("not migrated")
		oc.seed_reasons()
		pd.seed_document_types()
		self.assertTrue(frappe.db.exists(oc.MASTER, oc.OTHER))
		self.assertTrue(frappe.db.exists(pd.TYPE_DOCTYPE, "Other"))

	def test_only_a_sales_manager_stamps_a_sales_order_reason(self):
		order = frappe.db.get_value("Sales Order", {"docstatus": 1}, "name")
		if not order:
			self.skipTest("no submitted Sales Order")
		with patch.object(frappe, "get_roles", return_value=["Sales User"]), patch.object(frappe, "has_permission", return_value=True):
			with self.assertRaises(frappe.PermissionError):
				oc.set_cancellation_reason("Sales Order", order, "Duplicate Order")


class TestMasterDataCompleteness(FrappeTestCase):
	def test_every_view_runs(self):
		from frappe.desk.query_report import run

		for view in ("Detail", "Summary"):
			for scope in ("B2B Customers and Suppliers with Tax ID", "B2B Customers and All Suppliers"):
				with self.subTest(view=view, scope=scope):
					result = run("Master Data Completeness", filters={"view": view, "scope": scope},
						ignore_prepared_report=True)
					self.assertIsInstance(result["result"], list)

	def test_missing_filter_narrows(self):
		from sf_trading.sf_trading.report.master_data_completeness.master_data_completeness import execute

		rows = execute({"missing": "Payment Terms"})[1]
		self.assertTrue(all("Payment Terms" in r.missing_list for r in rows))
