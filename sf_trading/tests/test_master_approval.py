# sf_trading/tests/test_master_approval.py
"""The Customer / Supplier / Item approval (sf_trading/master_approval.py).

    bench --site <site> run-tests --module sf_trading.tests.test_master_approval
"""

from datetime import datetime
from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase

from sf_trading import master_approval as ma

START = datetime(2026, 10, 28, 0, 0)
OLD = datetime(2026, 1, 1, 0, 0)
NEW = datetime(2026, 11, 1, 0, 0)


def state(**fields):
	return frappe._dict({"name": "M-1", "creation": NEW, ma.STATUS_FIELD: None, ma.APPROVED_ON: None, ma.BYPASSED: 0,
		**fields})


class Policy(FrappeTestCase):
	"""Who the approval covers, and what counts as approved."""

	def setUp(self):
		self.patches = [
			patch.object(ma, "is_enabled", return_value=True),
			patch.object(ma, "has_fields", return_value=True),
			patch.object(ma, "rules_from", return_value=START),
			patch.object(ma, "is_strict", return_value=False),
			patch.object(ma, "has_credit_limit", return_value=True),
		]
		for p in self.patches:
			p.start()

	def tearDown(self):
		for p in self.patches:
			p.stop()

	def test_a_new_master_is_covered_until_approved(self):
		self.assertTrue(ma.is_covered("Supplier", state(**{ma.STATUS_FIELD: ma.DRAFT})))
		self.assertEqual(ma.refusal_status("Supplier", state(**{ma.STATUS_FIELD: ma.DRAFT})), "Draft")

	def test_approved_needs_its_stamp(self):
		stamped = state(**{ma.STATUS_FIELD: ma.APPROVED, ma.APPROVED_ON: NEW})
		self.assertIsNone(ma.refusal_status("Supplier", stamped))
		unstamped = state(**{ma.STATUS_FIELD: ma.APPROVED})
		self.assertTrue(ma.refusal_status("Supplier", unstamped))

	def test_an_older_master_stays_usable_without_strict(self):
		for status in (None, ma.DRAFT, ma.APPROVED):
			self.assertIsNone(ma.refusal_status("Customer", state(creation=OLD, **{ma.STATUS_FIELD: status})), status)

	def test_an_older_master_waiting_or_rejected_is_held(self):
		for status in ma.HELD:
			self.assertTrue(ma.refusal_status("Customer", state(creation=OLD, **{ma.STATUS_FIELD: status})), status)

	def test_strict_holds_every_older_master_until_approved(self):
		with patch.object(ma, "is_strict", return_value=True):
			self.assertEqual(ma.refusal_status("Item", state(creation=OLD)), "not sent for approval")
			# the earlier credit approval's Approved carries no stamp: under Strict it needs an approval
			self.assertTrue(ma.refusal_status("Customer", state(creation=OLD, **{ma.STATUS_FIELD: ma.APPROVED})))
			self.assertIsNone(ma.refusal_status("Customer", state(creation=OLD,
				**{ma.STATUS_FIELD: ma.APPROVED, ma.APPROVED_ON: NEW})))

	def test_a_bypassed_master_is_never_held(self):
		with patch.object(ma, "is_strict", return_value=True):
			self.assertIsNone(ma.refusal_status("Supplier", state(**{ma.BYPASSED: 1, ma.STATUS_FIELD: ma.REJECTED})))

	def test_switched_off_covers_nothing(self):
		with patch.object(ma, "is_enabled", return_value=False):
			self.assertIsNone(ma.refusal_status("Item", state(**{ma.STATUS_FIELD: ma.DRAFT})))

	def test_workflow_applies_only_where_the_approval_covers(self):
		self.assertEqual(ma.workflow_applicability("Item"), {"conditional": True})
		self.assertTrue(ma.workflow_applicability("Item", state(**{ma.STATUS_FIELD: ma.DRAFT}))["applies"])
		self.assertFalse(ma.workflow_applicability("Item", state(creation=OLD))["applies"])
		self.assertIsNone(ma.workflow_applicability("Sales Order", state()))


class CreditScope(FrappeTestCase):
	"""For now the Customer approval holds credit customers only."""

	def setUp(self):
		self.patches = [
			patch.object(ma, "is_enabled", return_value=True),
			patch.object(ma, "has_fields", return_value=True),
			patch.object(ma, "rules_from", return_value=START),
			patch.object(ma, "is_strict", return_value=False),
		]
		for p in self.patches:
			p.start()

	def tearDown(self):
		for p in self.patches:
			p.stop()

	def test_a_customer_without_a_credit_limit_is_not_covered(self):
		with patch.object(ma, "has_credit_limit", return_value=False):
			draft = state(**{ma.STATUS_FIELD: ma.DRAFT})
			self.assertFalse(ma.is_covered("Customer", draft))
			self.assertIsNone(ma.refusal_status("Customer", draft))
			self.assertFalse(ma.workflow_applicability("Customer", draft)["applies"])
			# not even under Strict Validation, nor while held
			with patch.object(ma, "is_strict", return_value=True):
				self.assertFalse(ma.is_covered("Customer", state(creation=OLD)))
			self.assertFalse(ma.is_covered("Customer", state(creation=OLD, **{ma.STATUS_FIELD: ma.PENDING})))

	def test_a_credit_customer_is_covered(self):
		with patch.object(ma, "has_credit_limit", return_value=True):
			draft = state(**{ma.STATUS_FIELD: ma.DRAFT})
			self.assertTrue(ma.is_covered("Customer", draft))
			self.assertEqual(ma.refusal_status("Customer", draft), "Draft")

	def test_a_supplier_and_an_item_are_not_narrowed(self):
		with patch.object(ma, "has_credit_limit", side_effect=AssertionError("asked")):
			for doctype in ("Supplier", "Item"):
				self.assertTrue(ma.is_covered(doctype, state(**{ma.STATUS_FIELD: ma.DRAFT})), doctype)

	def test_the_limit_is_read_from_the_docs_own_table(self):
		with_limit = frappe._dict(name="C", credit_limits=[frappe._dict(credit_limit=0), frappe._dict(credit_limit=500)])
		zero = frappe._dict(name="C", credit_limits=[frappe._dict(credit_limit=0)])
		empty = frappe._dict(name="C", credit_limits=[])
		self.assertTrue(ma.has_credit_limit(with_limit))
		self.assertFalse(ma.has_credit_limit(zero))
		self.assertFalse(ma.has_credit_limit(empty))

	def test_without_a_table_the_limit_is_read_from_the_database(self):
		row = frappe._dict(name="C")
		with patch("frappe.db.exists", return_value="CCL-1") as exists:
			self.assertTrue(ma.has_credit_limit(row))
		self.assertEqual(exists.call_args[0][1]["credit_limit"], [">", 0])
		with patch("frappe.db.exists", return_value=None):
			self.assertFalse(ma.has_credit_limit(row))


class StubVoucher(frappe._dict):
	pass


class Gate(FrappeTestCase):
	def voucher(self, doctype, **fields):
		return StubVoucher(doctype=doctype, **fields)

	def test_a_return_and_an_opening_entry_stand_aside(self):
		with patch.object(ma, "party_refusal", side_effect=AssertionError("asked")):
			ma.validate_master_approval_at_transaction(self.voucher("Sales Invoice", customer="C", is_return=1))
			ma.validate_master_approval_at_transaction(self.voucher("Sales Invoice", customer="C", is_opening="Yes"))

	def test_an_unapproved_customer_is_refused(self):
		with patch.object(ma, "party_refusal", return_value="Customer C is not approved"), \
				patch.object(ma, "unapproved_items", return_value=[]):
			with self.assertRaises(frappe.ValidationError):
				ma.validate_master_approval_at_transaction(self.voucher("Sales Order", customer="C", items=[]))

	def test_unapproved_items_are_named(self):
		with patch.object(ma, "party_refusal", return_value=None), \
				patch.object(ma, "unapproved_items", return_value=["ITEM-A"]):
			with self.assertRaises(frappe.ValidationError) as caught:
				ma.validate_master_approval_at_transaction(self.voucher("Purchase Order", supplier="S",
					items=[{"item_code": "ITEM-A"}]))
		self.assertIn("ITEM-A", str(caught.exception))

	def test_only_paying_a_supplier_is_held(self):
		self.assertEqual(ma._party(self.voucher("Payment Entry", party_type="Supplier", payment_type="Pay", party="S")),
			("Supplier", "S"))
		self.assertEqual(ma._party(self.voucher("Payment Entry", party_type="Customer", payment_type="Receive",
			party="C")), (None, None))
		self.assertEqual(ma._party(self.voucher("Delivery Note", customer="C")), ("Customer", "C"))

	def test_a_quotation_is_not_held(self):
		with patch.object(ma, "party_refusal", side_effect=AssertionError("asked")), \
				patch.object(ma, "unapproved_items", side_effect=AssertionError("asked")):
			ma.validate_master_approval_at_transaction(self.voucher("Quotation", party_name="C", items=[]))


class Workflow(FrappeTestCase):
	def test_the_definition(self):
		wf = ma.workflow_definition("Customer")
		self.assertEqual(wf["workflow_state_field"], ma.STATUS_FIELD)
		self.assertEqual({s["state"] for s in wf["states"]}, {ma.DRAFT, ma.PENDING, ma.APPROVED, ma.REJECTED})
		approvers = {t["allowed"] for t in wf["transitions"] if t["action"] == ma.APPROVE}
		self.assertEqual(approvers, {"Customer Approver", "Credit Approval Officer"})
		reject = [t for t in wf["transitions"] if t["action"] == ma.REJECT]
		self.assertTrue(all(t.get("require_comment") for t in reject))
		# only a master the earlier approval marked Approved, without a stamp, may be sent again
		resend = [t for t in wf["transitions"] if t["state"] == ma.APPROVED]
		self.assertEqual([t.get("condition") for t in resend], [ma.LEGACY_APPROVED_CONDITION])
		# PM's inbox entry follows the NEXT state: only Approved opens one, so only approvers get it
		self.assertEqual([s["state"] for s in wf["states"] if not s["is_optional_state"]], [ma.APPROVED])

	def test_each_master_has_its_own_approver(self):
		self.assertIn("Supplier Approver", ma.workflow_approver_roles("Supplier"))
		self.assertIn("Item Master Approver", ma.workflow_approver_roles("Item"))
		self.assertNotIn("System Manager", ma.workflow_approver_roles("Item"))


class Requirements(FrappeTestCase):
	def test_an_item_needs_approval_only(self):
		self.assertEqual(ma.missing_requirements(frappe._dict(doctype="Item", name="X")), [])

	def test_a_company_supplier_without_tax_id_is_not_complete(self):
		doc = frappe.get_doc({"doctype": "Supplier", "supplier_name": "MA Test", "supplier_type": "Company",
			"supplier_group": frappe.db.get_value("Supplier Group", {"is_group": 0}, "name")})
		with patch("sf_trading.party_documents.missing_details", return_value=[]), \
				patch("sf_trading.party_documents.completeness_exempt", return_value=False), \
				patch("sf_trading.party_documents.expired_financial_rows", return_value=[]):
			self.assertIn("Tax ID", ma.missing_requirements(doc))
