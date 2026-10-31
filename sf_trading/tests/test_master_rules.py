"""New-master rules, the supporting-documents grid, and order cancellation reasons.

    bench --site <site> run-tests --module sf_trading.tests.test_master_rules
"""

from types import SimpleNamespace
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

	def has_value_changed(self, fieldname):
		return False

	def append(self, table, row):
		self[table].append(Row(idx=len(self[table]) + 1, **row))

	def remove(self, row):
		self[pd.TABLE].remove(row)


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

	def test_suppliers_always_customers_only_when_on_credit(self):
		self.assertTrue(pd.is_governed(Party("Supplier")))
		self.assertTrue(pd.is_governed(Party("Customer", credit_limits=[frappe._dict(credit_limit=500)])))
		self.assertFalse(pd.is_governed(Party("Customer", credit_limits=[frappe._dict(credit_limit=0)])))
		# a VAT number alone no longer makes a customer governed (2026-10-11)
		self.assertFalse(pd.is_governed(Party("Customer", custom_vat_registration_number="300000000000003")))

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

	def test_after_creation_the_document_mobile_and_email_are_needed(self):
		saved = dict(created=str(TODAY), payment_terms="CASH")
		with self.assertRaises(frappe.ValidationError) as caught:
			pd.validate_master(Party("Supplier", **saved))
		self.assertIn("Supporting Document", str(caught.exception))
		self.assertIn("Mobile", str(caught.exception))
		self.assertIn("Email", str(caught.exception))
		pd.validate_master(Party("Supplier", custom_mobile_no="36000000", email_id="a@b.co",
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

	def test_a_cash_customer_is_not_governed(self):
		pd.validate_master(Party("Customer", new=True))
		pd.validate_master(Party("Customer", new=True, custom_vat_registration_number="300000000000003"))

	def test_a_new_credit_customer_needs_payment_terms(self):
		with self.assertRaises(frappe.ValidationError):
			pd.validate_master(Party("Customer", new=True, credit_limits=[frappe._dict(credit_limit=500)]))

	def test_a_cash_sale_with_the_override_skips_the_document_gate(self):
		customer = Party("Customer", created=str(TODAY), credit_limits=[frappe._dict(credit_limit=500)])
		invoice = frappe._dict(doctype="Sales Invoice", customer="P-1", custom_payment_mode="Cash")
		with patch.object(frappe.db, "exists", return_value=True), patch.object(frappe, "get_doc", return_value=customer), \
				patch.object(pd, "expired_financial_rows", return_value=[]):
			with patch("sf_trading.sales_order_governance.cash_sale_overrides_credit_documents", return_value=False):
				self.assertRaises(frappe.ValidationError, pd.validate_party_at_transaction, invoice)
			with patch("sf_trading.sales_order_governance.cash_sale_overrides_credit_documents", return_value=True):
				pd.validate_party_at_transaction(invoice)

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


class TestCancelDialogWithoutWorkflowState(FrappeTestCase):
	"""A Quotation on a site with no Quotation Cancellation workflow has no workflow_state column."""

	def _set_reason(self, has_state_field, state=None):
		asked = []

		def get_value(doctype, name=None, fieldname=None, **kwargs):
			if doctype == oc.MASTER:
				return frappe._dict(applies_to=oc.BOTH, needs_details=0, disabled=0)
			if isinstance(fieldname, str):
				return "Customer Request - asked"
			asked.append(list(fieldname))
			row = frappe._dict(docstatus=1)
			if "workflow_state" in fieldname:
				row.workflow_state = state
			return row

		meta = SimpleNamespace(has_field=lambda f: has_state_field or f != "workflow_state")
		with patch.object(frappe, "get_meta", return_value=meta), \
			patch.object(frappe, "has_permission", return_value=True), \
			patch.object(frappe, "get_roles", return_value=["Sales Manager"]), \
			patch.object(frappe.db, "get_value", side_effect=get_value), \
			patch.object(frappe.db, "set_value") as set_value:
			result = oc.set_cancellation_reason("Quotation", "SAL-QTN-X", "Price Changed", "okay")
		return result, asked, set_value

	def test_a_quotation_without_the_column_is_not_asked_for_it(self):
		result, asked, set_value = self._set_reason(has_state_field=False)
		self.assertEqual(result, "Price Changed - okay")
		self.assertEqual(asked, [["docstatus"]])
		set_value.assert_called_once()

	def test_a_requested_cancellation_keeps_the_requesters_reason(self):
		result, asked, set_value = self._set_reason(has_state_field=True, state=oc.REQUESTED_STATE)
		self.assertEqual(result, "Customer Request - asked")
		self.assertEqual(asked, [["docstatus", "workflow_state"]])
		set_value.assert_not_called()


class TestMasterDataCompleteness(FrappeTestCase):
	def test_every_view_runs(self):
		from frappe.desk.query_report import run

		for view in ("Detail", "Summary"):
			# the old "B2B ..." labels may still sit in a saved filter and must keep working
			for scope in ("Credit Customers and Suppliers with Tax ID", "Credit Customers and All Suppliers",
					"B2B Customers and All Suppliers"):
				with self.subTest(view=view, scope=scope):
					result = run("Master Data Completeness", filters={"view": view, "scope": scope},
						ignore_prepared_report=True)
					self.assertIsInstance(result["result"], list)

	def test_missing_filter_narrows(self):
		from sf_trading.sf_trading.report.master_data_completeness.master_data_completeness import execute

		rows = execute({"missing": "Payment Terms"})[1]
		self.assertTrue(all("Payment Terms" in r.missing_list for r in rows))

	def test_customers_listed_are_credit_customers(self):
		from sf_trading.sf_trading.report.master_data_completeness.master_data_completeness import execute

		company = frappe.db.get_value("Company", {}, "name")
		branch = frappe.db.get_value("Branch", {}, "name")
		if not company or not branch:
			self.skipTest("no company or branch")

		def make(name, vat=None, limit=0):
			doc = frappe.get_doc({"doctype": "Customer", "customer_name": name, "customer_type": "Company",
				"customer_group": frappe.db.get_value("Customer Group", {"is_group": 0}, "name"),
				"territory": frappe.db.get_value("Territory", {"is_group": 0}, "name"),
				"custom_vat_registration_number": vat,
				"credit_limits": [{"company": company, "credit_limit": limit}] if limit else [],
				"custom_branch_access": [{"branch": branch}] if limit else []})
			# fixtures only: the VAT and credit rules of other modules are not what is under test here
			doc.flags.ignore_mandatory = True
			doc.flags.ignore_validate = True
			return doc.insert(ignore_permissions=True).name

		on_credit = make("_SF MDC Credit Customer", limit=500)
		vat_only = make("_SF MDC VAT Customer", vat="300000000000003")
		listed = {r.party for r in execute({"party_type": "Customer", "show_complete": 1, "include_disabled": 1})[1]}
		self.assertIn(on_credit, listed)
		self.assertNotIn(vat_only, listed)


class TestDocumentRequired(FrappeTestCase):
	"""A Company supplier and a VAT-registered customer hold a document, old or new."""

	def test_who_needs_one(self):
		self.assertTrue(pd.needs_document(Party("Supplier", supplier_type="Company")))
		self.assertFalse(pd.needs_document(Party("Supplier", supplier_type="Individual")))
		self.assertTrue(pd.needs_document(Party("Customer", custom_vat_registration_number="300000000000003")))
		self.assertFalse(pd.needs_document(Party("Customer")))

	def test_a_grid_row_or_any_old_file_counts(self):
		company = Party("Supplier", supplier_type="Company")
		with patch.object(frappe.db, "exists", return_value=False):
			self.assertTrue(pd.missing_document(company))
		with patch.object(frappe.db, "exists", return_value=True):
			self.assertEqual(pd.missing_document(company), [])
		with_row = Party("Supplier", supplier_type="Company", rows=[{"document_type": "Other", "attachment": "/f.pdf"}])
		self.assertEqual(pd.missing_document(with_row), [])

	def test_an_old_company_supplier_without_one_is_refused(self):
		old = Party("Supplier", created="2025-01-01", supplier_type="Company", payment_terms="Net 30")
		order = frappe._dict(doctype="Purchase Order", supplier="P-1")
		with patch.object(pd, "rules_from", return_value=START), patch.object(frappe, "get_doc", return_value=old), \
				patch.object(pd, "expired_financial_rows", return_value=[]), \
				patch.object(pd, "has_document", return_value=False), \
				patch.object(frappe.db, "exists", side_effect=lambda dt, *a, **k: dt == "Supplier"):
			with self.assertRaises(frappe.ValidationError):
				pd.validate_party_at_transaction(order)
			with patch.object(pd, "has_document", return_value=True):
				pd.validate_party_at_transaction(order)

	def test_returns_still_stand_aside(self):
		pd.validate_party_at_transaction(frappe._dict(doctype="Purchase Invoice", supplier="P-1", is_return=1))


class VatParty(Party):
	"""A Party that says whether its VAT number changed since the last save."""

	def __init__(self, doctype, changed=False, **values):
		super().__init__(doctype, **values)
		self._changed = changed

	def has_value_changed(self, fieldname):
		return self._changed


KIND = frappe._dict(applies_to=pd.APPLIES_TO_BOTH, validate_expiry=0, days_ahead=0, grace_days=0,
	financial_implication=0, disabled=0)


class TestVatNumberIsADocument(FrappeTestCase):
	"""The VAT Registration Number (a supplier's Tax ID) holds a Supporting Documents row of its own."""

	def setUp(self):
		patch.object(frappe.db, "get_value", return_value=KIND).start()
		self.addCleanup(patch.stopall)

	def rows(self, party):
		return [(r.document_type, r.get("document_number")) for r in party[pd.TABLE]]

	def test_a_new_customer_gets_a_vat_row_carrying_the_number(self):
		party = VatParty("Customer", new=True, custom_vat_registration_number="300000000000003")
		pd.ensure_vat_row(party)
		self.assertEqual(self.rows(party), [("VAT Certificate", "300000000000003")])

	def test_a_supplier_tax_id_is_its_vat_number(self):
		party = VatParty("Supplier", new=True, tax_id="300000000000003")
		pd.ensure_vat_row(party)
		self.assertEqual(self.rows(party), [("VAT Certificate", "300000000000003")])

	def test_a_row_the_dialog_made_is_not_doubled(self):
		party = VatParty("Customer", new=True, custom_vat_registration_number="300000000000003",
			rows=[{"document_type": "VAT Certificate", "document_number": "300000000000003", "attachment": "/f.pdf"}])
		pd.ensure_vat_row(party)
		self.assertEqual(len(party[pd.TABLE]), 1)

	def test_a_blank_vat_row_takes_the_number(self):
		party = VatParty("Customer", new=True, custom_vat_registration_number="300000000000003",
			rows=[{"document_type": "VAT Certificate", "attachment": "/f.pdf"}])
		pd.ensure_vat_row(party)
		self.assertEqual(self.rows(party), [("VAT Certificate", "300000000000003")])

	def test_a_changed_number_retypes_the_row_that_only_held_the_old_one(self):
		party = VatParty("Customer", new=True, custom_vat_registration_number="300000000000003",
			rows=[{"document_type": "VAT Certificate", "document_number": "22"}])
		pd.ensure_vat_row(party)
		self.assertEqual(self.rows(party), [("VAT Certificate", "300000000000003")])

	def test_stacked_placeholder_rows_collapse_to_one(self):
		party = VatParty("Customer", new=True, custom_vat_registration_number="300000000000003",
			rows=[{"document_type": "VAT Certificate", "document_number": n} for n in ("22", "2200", "22000", "300000000000003")])
		pd.ensure_vat_row(party)
		self.assertEqual(self.rows(party), [("VAT Certificate", "300000000000003")])

	def test_a_real_certificate_row_for_an_old_number_is_kept(self):
		party = VatParty("Customer", new=True, custom_vat_registration_number="300000000000003",
			rows=[{"document_type": "VAT Certificate", "document_number": "22", "attachment": "/old.pdf"}])
		pd.ensure_vat_row(party)
		self.assertEqual(self.rows(party), [("VAT Certificate", "22"), ("VAT Certificate", "300000000000003")])

	def test_another_document_type_still_gets_the_vat_row_as_well(self):
		party = VatParty("Customer", new=True, custom_vat_registration_number="300000000000003",
			rows=[{"document_type": "Commercial Registration", "attachment": "/f.pdf"}])
		pd.ensure_vat_row(party)
		self.assertEqual(len(party[pd.TABLE]), 2)

	def test_an_old_master_is_left_alone_until_its_number_changes(self):
		old = VatParty("Customer", custom_vat_registration_number="300000000000003", changed=False)
		pd.ensure_vat_row(old)
		self.assertEqual(old[pd.TABLE], [])
		edited = VatParty("Customer", custom_vat_registration_number="300000000000003", changed=True)
		pd.ensure_vat_row(edited)
		self.assertEqual(len(edited[pd.TABLE]), 1)

	def test_no_number_no_row(self):
		for number in ("", "0", "  "):
			party = VatParty("Customer", new=True, custom_vat_registration_number=number)
			pd.ensure_vat_row(party)
			self.assertEqual(party[pd.TABLE], [])

	def test_a_disabled_vat_type_adds_nothing(self):
		with patch.object(frappe.db, "get_value", return_value=frappe._dict(KIND, disabled=1)):
			party = VatParty("Customer", new=True, custom_vat_registration_number="300000000000003")
			pd.ensure_vat_row(party)
		self.assertEqual(party[pd.TABLE], [])


class TestOtherSupplierHoldsNoDocument(FrappeTestCase):
	"""Client, 2026-10-11: a supplier in the "Other Supplier" group is not asked for documents."""

	def test_the_group_is_matched_loosely(self):
		self.assertTrue(pd.document_exempt_group("Other Supplier"))
		self.assertTrue(pd.document_exempt_group("  other supplier "))
		self.assertFalse(pd.document_exempt_group("Distributor"))
		self.assertFalse(pd.document_exempt_group(None))

	def test_a_company_supplier_in_the_group_needs_no_document(self):
		other = Party("Supplier", supplier_type="Company", supplier_group="Other Supplier")
		self.assertFalse(pd.needs_document(other))
		self.assertEqual(pd.missing_document(other), [])
		usual = Party("Supplier", supplier_type="Company", supplier_group="Distributor")
		self.assertTrue(pd.needs_document(usual))

	def test_a_new_master_is_not_asked_for_the_document_line_but_still_for_payment_terms(self):
		with patch.object(pd, "has_mobile", return_value=True), patch.object(pd, "has_email", return_value=True):
			other = Party("Supplier", payment_terms="Net 30", supplier_group="Other Supplier")
			self.assertEqual(pd.missing_details(other, after_insert=True), [])
			no_terms = Party("Supplier", supplier_group="Other Supplier")
			self.assertEqual(len(pd.missing_details(no_terms, after_insert=True)), 1)
			usual = Party("Supplier", payment_terms="Net 30", supplier_group="Distributor")
			self.assertEqual(len(pd.missing_details(usual, after_insert=True)), 1)

	def test_a_customer_is_never_exempt(self):
		self.assertFalse(pd.documents_exempt(Party("Customer", supplier_group="Other Supplier")))


class TestNewCompanySupplierNeedsAnAddress(FrappeTestCase):
	"""Every B2B (Company) supplier carries an address, wherever it was made."""

	def _asked(self, supplier, has_address):
		with patch.object(pd, "has_mobile", return_value=True), patch.object(pd, "has_email", return_value=True), \
				patch.object(pd, "has_address", return_value=has_address):
			return pd.missing_details(supplier, after_insert=True)

	def test_a_company_supplier_without_one_is_told(self):
		supplier = Party("Supplier", supplier_type="Company", payment_terms="Net 30", rows=[{"attachment": "/f.pdf"}])
		self.assertEqual(self._asked(supplier, False), ["an Address"])
		self.assertEqual(self._asked(supplier, True), [])

	def test_an_individual_supplier_is_not_asked(self):
		supplier = Party("Supplier", supplier_type="Individual", payment_terms="Net 30", rows=[{"attachment": "/f.pdf"}])
		self.assertEqual(self._asked(supplier, False), [])

	def test_a_customer_is_not_asked(self):
		customer = Party("Customer", payment_terms="Net 30", rows=[{"attachment": "/f.pdf"}])
		self.assertEqual(self._asked(customer, False), [])

	def test_the_first_save_cannot_carry_one(self):
		supplier = Party("Supplier", supplier_type="Company", payment_terms="Net 30", rows=[{"attachment": "/f.pdf"}])
		with patch.object(pd, "has_address", return_value=False):
			self.assertEqual(pd.missing_details(supplier, after_insert=False), [])

	def test_a_new_unsaved_master_has_none(self):
		self.assertFalse(pd.has_address(Party("Supplier", new=True)))


class TestNewSupplierNeedsAnEmail(FrappeTestCase):
	"""A new supplier carries an email as well as a mobile (the Create Supplier dialog asks for both)."""

	def test_missing_email_is_listed_after_the_insert(self):
		with patch.object(pd, "has_mobile", return_value=True), patch.object(pd, "has_email", return_value=False):
			supplier = Party("Supplier", payment_terms="Net 30", rows=[{"attachment": "/f.pdf"}])
			self.assertEqual(pd.missing_details(supplier, after_insert=True), ["Email"])
			# the very first save cannot carry a Contact, so it is let through
			self.assertEqual(pd.missing_details(supplier, after_insert=False), [])

	def test_a_customer_is_not_asked_for_it(self):
		with patch.object(pd, "has_mobile", return_value=True), patch.object(pd, "has_email", return_value=False):
			customer = Party("Customer", payment_terms="Net 30", rows=[{"attachment": "/f.pdf"}])
			self.assertEqual(pd.missing_details(customer, after_insert=True), [])


class TestSupplierPaymentTermsRequired(FrappeTestCase):
	"""Every supplier, old or new, carries Payment Terms: buying from or paying one without is refused."""

	def _gate(self, supplier, doctype="Purchase Order", **fields):
		doc = frappe._dict(doctype=doctype, supplier="P-1", **fields)
		with patch.object(pd, "rules_from", return_value=START), patch.object(frappe, "get_doc", return_value=supplier), \
				patch.object(pd, "expired_financial_rows", return_value=[]), \
				patch.object(frappe.db, "exists", side_effect=lambda dt, *a, **k: dt == "Supplier"):
			pd.validate_party_at_transaction(doc)

	def _old(self, **values):
		return Party("Supplier", created="2025-01-01", supplier_type="Individual", **values)

	def test_the_rule_is_for_suppliers_while_the_master_rules_are_on(self):
		with patch.object(pd, "rules_from", return_value=START):
			self.assertTrue(pd.payment_terms_required("Supplier"))
			self.assertFalse(pd.payment_terms_required("Customer"))
		with patch.object(pd, "rules_from", return_value=None):
			self.assertFalse(pd.payment_terms_required("Supplier"))

	def test_what_counts_as_none(self):
		with patch.object(pd, "rules_from", return_value=START):
			for blank in (None, "", "   "):
				self.assertEqual(pd.missing_payment_terms(Party("Supplier", payment_terms=blank)), ["Payment Terms"], repr(blank))
			self.assertEqual(pd.missing_payment_terms(Party("Supplier", payment_terms="Net 30")), [])
			self.assertEqual(pd.missing_payment_terms(Party("Customer")), [])
		with patch.object(pd, "rules_from", return_value=None):
			self.assertEqual(pd.missing_payment_terms(Party("Supplier")), [])

	def test_an_old_supplier_without_terms_is_refused_on_every_buying_document_and_payment(self):
		for doctype, extra in (("Purchase Order", {}), ("Purchase Receipt", {}), ("Purchase Invoice", {}),
				("Payment Entry", {"party_type": "Supplier", "payment_type": "Pay", "party": "P-1"})):
			with self.assertRaises(frappe.ValidationError, msg=doctype) as caught:
				self._gate(self._old(), doctype, **extra)
			self.assertIn("Payment Terms", str(caught.exception), doctype)

	def test_with_terms_it_goes_through(self):
		self._gate(self._old(payment_terms="Net 30"))
		self._gate(self._old(payment_terms="Net 30"), "Payment Entry", party_type="Supplier", payment_type="Pay", party="P-1")

	def test_the_message_names_what_else_is_missing_with_the_terms_first(self):
		company = Party("Supplier", created="2025-01-01", supplier_type="Company")
		with patch.object(pd, "has_document", return_value=False):
			with self.assertRaises(frappe.ValidationError) as caught:
				self._gate(company)
		text = str(caught.exception)
		self.assertIn("Payment Terms, a supporting document (Company supplier)", text)

	def test_a_new_supplier_is_not_told_twice(self):
		new = Party("Supplier", created=str(TODAY), supplier_type="Individual")
		with patch.object(pd, "has_mobile", return_value=True), patch.object(pd, "has_email", return_value=True):
			with self.assertRaises(frappe.ValidationError) as caught:
				self._gate(new)
		self.assertEqual(str(caught.exception).count("Payment Terms"), 1)

	def test_a_return_and_an_opening_invoice_stand_aside(self):
		with patch.object(pd, "rules_from", return_value=START):
			pd.validate_party_at_transaction(frappe._dict(doctype="Purchase Invoice", supplier="P-1", is_return=1))
			pd.validate_party_at_transaction(frappe._dict(doctype="Purchase Invoice", supplier="P-1", is_opening="Yes"))

	def test_money_received_from_a_supplier_is_never_refused(self):
		self._gate(self._old(), "Payment Entry", party_type="Supplier", payment_type="Receive", party="P-1")

	def test_switched_off_with_the_master_rules(self):
		doc = frappe._dict(doctype="Purchase Order", supplier="P-1")
		with patch.object(pd, "rules_from", return_value=None), patch.object(frappe, "get_doc", return_value=self._old()), \
				patch.object(pd, "expired_financial_rows", return_value=[]), \
				patch.object(frappe.db, "exists", side_effect=lambda dt, *a, **k: dt == "Supplier"):
			pd.validate_party_at_transaction(doc)

	def test_a_customer_is_not_asked_for_terms_by_this_rule(self):
		customer = Party("Customer", created="2025-01-01")
		doc = frappe._dict(doctype="Sales Invoice", customer="P-1")
		with patch.object(pd, "rules_from", return_value=START), patch.object(frappe, "get_doc", return_value=customer), \
				patch.object(pd, "expired_financial_rows", return_value=[]), \
				patch.object(frappe.db, "exists", side_effect=lambda dt, *a, **k: dt == "Customer"):
			pd.validate_party_at_transaction(doc)

	def test_the_supplier_form_is_told_and_names_the_terms_first(self):
		told = {}

		class Form(Party):
			def set_onload(self, key, value):
				told[key] = value

		old = Form("Supplier", created="2025-01-01", supplier_type="Company")
		with patch.object(pd, "rules_from", return_value=START), patch.object(pd, "expired_financial_rows", return_value=[]), \
				patch.object(pd, "has_document", return_value=False):
			pd.set_onload(old)
		rules = told["sf_master_rules"]
		self.assertTrue(rules["payment_terms_required"])
		self.assertEqual(rules["missing"], ["Payment Terms", "a supporting document (Company supplier)"])

	def test_the_form_rules_a_dialog_can_ask_for(self):
		with patch.object(pd, "rules_from", return_value=START):
			self.assertTrue(pd.get_master_rules("Supplier")["payment_terms_required"])
			self.assertFalse(pd.get_master_rules("Customer")["payment_terms_required"])
