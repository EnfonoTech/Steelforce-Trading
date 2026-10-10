"""Tests for post-dated cheques: partial banking and returns, received and issued.

    bench --site <scratch-site> run-tests --module sf_trading.tests.test_pdc_cheques
"""

from unittest.mock import MagicMock, patch

import frappe
from frappe.tests.utils import FrappeTestCase

from sf_trading import pdc_transfer as pdc

HAS_PERMISSION = "sf_trading.pdc_transfer.frappe.has_permission"
GET_DOC = "sf_trading.pdc_transfer.frappe.get_doc"
NEW_DOC = "sf_trading.pdc_transfer.frappe.new_doc"
CHEQUE_MODES = "sf_trading.pdc_transfer.cheque_modes"
TRANSFER_ROWS = "sf_trading.pdc_transfer.transfer_rows"
RETURN_ROWS = "sf_trading.pdc_transfer.return_rows"
CACHED_VALUE = "sf_trading.pdc_transfer.frappe.get_cached_value"
GET_VALUE = "sf_trading.pdc_transfer.frappe.db.get_value"


def _cheque(**values):
	"""A submitted cheque received from a customer, worth 100, unless told otherwise."""
	data = frappe._dict(
		name="ACC-PAY-2026-00099",
		docstatus=1,
		payment_type="Receive",
		mode_of_payment="Cheque",
		company="SF",
		party_type="Customer",
		party="CUST-1",
		party_name="Customer One",
		paid_from="Debtors - SF",
		paid_to="PDC Account - SF",
		paid_amount=100,
		received_amount=100,
		unallocated_amount=0,
		posting_date="2026-09-01",
		reference_no="CHQ-1",
		reference_date="2026-09-15",
		cost_center="Main - SF",
		branch="SFSS",
		clearance_date=None,
		references=[],
	)
	data.update(values)
	pe = MagicMock()
	for key, value in data.items():
		setattr(pe, key, value)
	pe.get = lambda field, default=None: data.get(field, default)
	return pe


def _issued(**values):
	defaults = dict(
		payment_type="Pay", party_type="Supplier", party="SUP-1", party_name="Supplier One",
		paid_from="PDC Account - SF", paid_to="Creditors - SF",
	)
	defaults.update(values)
	return _cheque(**defaults)


def _transfer(amount, docstatus=1, name="ACC-PAY-2026-00150", posting_date="2026-09-20"):
	return frappe._dict(name=name, docstatus=docstatus, paid_amount=amount, posting_date=posting_date)


def _return(amount, docstatus=1, name="ACC-JV-2026-00010", posting_date="2026-09-25"):
	return frappe._dict(name=name, docstatus=docstatus, amount=amount, posting_date=posting_date)


class Doc(frappe._dict):
	"""Enough of a new document to build a transfer or a journal on."""

	def __init__(self, doctype):
		super().__init__(doctype=doctype, accounts=[])
		self.flags = frappe._dict()
		self.meta = MagicMock()
		self.meta.has_field = lambda field: field == "branch"
		self.inserted = self.submitted = False

	def set(self, key, value):
		self[key] = value

	def append(self, table, row):
		self.setdefault(table, []).append(frappe._dict(row))

	def insert(self):
		self.inserted = True
		self.name = self.name or "NEW-1"
		return self

	def submit(self):
		self.submitted = True


class TestPosition(FrappeTestCase):
	def test_untouched_cheque_is_pending(self):
		pos = pdc.position_of(_cheque(), transfers=[], returns=[])
		self.assertEqual((pos.status, pos.cleared, pos.remaining), (pdc.PENDING, 0, 100))

	def test_part_banked_cheque_is_partly_cleared(self):
		pos = pdc.position_of(_cheque(), transfers=[_transfer(40)], returns=[])
		self.assertEqual((pos.status, pos.cleared, pos.remaining), (pdc.PARTLY_CLEARED, 40, 60))

	def test_banked_in_two_parts_is_cleared(self):
		transfers = [_transfer(40), _transfer(60, name="ACC-PAY-2026-00151", posting_date="2026-09-28")]
		pos = pdc.position_of(_cheque(), transfers=transfers, returns=[])
		self.assertEqual((pos.status, pos.remaining), (pdc.CLEARED, 0))
		self.assertEqual(str(pos.last_cleared_on), "2026-09-28")

	def test_draft_transfer_counts_for_nothing_but_is_reported(self):
		pos = pdc.position_of(_cheque(), transfers=[_transfer(100, docstatus=0)], returns=[])
		self.assertEqual((pos.status, pos.remaining), (pdc.PENDING, 100))
		self.assertEqual(pos.drafts, ["ACC-PAY-2026-00150"])

	def test_returned_whole(self):
		pos = pdc.position_of(_cheque(), transfers=[], returns=[_return(100)])
		self.assertEqual((pos.status, pos.returned, pos.remaining), (pdc.RETURNED, 100, 0))

	def test_part_banked_then_returned(self):
		pos = pdc.position_of(_cheque(), transfers=[_transfer(30)], returns=[_return(70)])
		self.assertEqual((pos.status, pos.cleared, pos.returned, pos.remaining), (pdc.PARTLY_RETURNED, 30, 70, 0))

	def test_issued_cheque_reads_paid_amount(self):
		pos = pdc.position_of(_issued(paid_amount=250, received_amount=0), transfers=[_transfer(50)], returns=[])
		self.assertEqual((pos.amount, pos.remaining, pos.status), (250, 200, pdc.PARTLY_CLEARED))

	def test_legacy_clearance_without_transfer_reads_cleared(self):
		pos = pdc.position_of(_cheque(clearance_date="2026-09-10"), transfers=[], returns=[])
		self.assertEqual((pos.status, pos.remaining), (pdc.CLEARED, 0))
		self.assertTrue(pos.legacy_cleared)

	def test_legacy_rejection_without_journal_reads_returned_and_can_still_be_reversed(self):
		pos = pdc.position_of(_cheque(custom_pdc_rejection_date="2026-09-10"), transfers=[], returns=[])
		self.assertEqual((pos.status, pos.remaining), (pdc.RETURNED, 100))
		self.assertTrue(pos.legacy_returned)


class TestBanking(FrappeTestCase):
	def setUp(self):
		patch(HAS_PERMISSION, return_value=True).start()
		patch(CHEQUE_MODES, return_value=["Cheque"]).start()
		patch(CACHED_VALUE, return_value=frappe._dict(company="SF", is_group=0)).start()
		self.addCleanup(patch.stopall)

	def _bank(self, cheque, transfers=(), amount=None, to_account="Bank - SF"):
		created = Doc("Payment Entry")
		with patch(GET_DOC, return_value=cheque), patch(NEW_DOC, return_value=created), patch(
			TRANSFER_ROWS, return_value={cheque.name: list(transfers)}
		), patch(RETURN_ROWS, return_value={}):
			pdc.create_internal_transfer(cheque.name, to_account, "2026-09-20", submit=1, amount=amount)
		return created

	def test_received_cheque_moves_holding_to_bank_for_the_rest(self):
		transfer = self._bank(_cheque(), transfers=[_transfer(40)])
		self.assertEqual((transfer.paid_from, transfer.paid_to), ("PDC Account - SF", "Bank - SF"))
		self.assertEqual((transfer.paid_amount, transfer.received_amount), (60, 60))
		self.assertEqual(transfer[pdc.SOURCE_FIELD], "ACC-PAY-2026-00099")
		self.assertEqual(transfer.branch, "SFSS")
		self.assertTrue(transfer.submitted)

	def test_issued_cheque_moves_bank_to_holding(self):
		transfer = self._bank(_issued(), amount=30)
		self.assertEqual((transfer.paid_from, transfer.paid_to), ("Bank - SF", "PDC Account - SF"))
		self.assertEqual(transfer.paid_amount, 30)
		self.assertIn("part", transfer.remarks)

	def test_refuses_more_than_is_left(self):
		with self.assertRaises(frappe.ValidationError):
			self._bank(_cheque(), transfers=[_transfer(40)], amount=61)

	def test_refuses_while_a_draft_waits(self):
		with self.assertRaises(frappe.ValidationError):
			self._bank(_cheque(), transfers=[_transfer(40, docstatus=0)], amount=10)

	def test_refuses_a_cheque_already_banked(self):
		with self.assertRaises(frappe.ValidationError):
			self._bank(_cheque(), transfers=[_transfer(100)])

	def test_refuses_the_holding_account_as_the_bank(self):
		with self.assertRaises(frappe.ValidationError):
			self._bank(_cheque(), to_account="PDC Account - SF")

	def test_refuses_a_non_cheque(self):
		with self.assertRaises(frappe.ValidationError):
			self._bank(_cheque(mode_of_payment="Cash"))


class TestTransferValidate(FrappeTestCase):
	def setUp(self):
		patch(CHEQUE_MODES, return_value=["Cheque"]).start()
		patch(RETURN_ROWS, return_value={}).start()
		self.addCleanup(patch.stopall)

	def _validate(self, cheque, doc, others=()):
		with patch(GET_DOC, return_value=cheque), patch(TRANSFER_ROWS, return_value={cheque.name: list(others)}):
			pdc.validate(doc)

	def _doc(self, **values):
		data = dict(name="ACC-PAY-2026-00200", payment_type="Internal Transfer", paid_amount=50,
			paid_from="PDC Account - SF", paid_to="Bank - SF")
		data[pdc.SOURCE_FIELD] = "ACC-PAY-2026-00099"
		data.update(values)
		return frappe._dict(data)

	def test_hand_built_partial_transfer_passes(self):
		self._validate(_cheque(), self._doc(), others=[_transfer(50)])

	def test_its_own_saved_copy_is_not_counted_twice(self):
		self._validate(_cheque(), self._doc(paid_amount=100), others=[_transfer(100, docstatus=0, name="ACC-PAY-2026-00200")])

	def test_over_the_cheque_is_refused(self):
		with self.assertRaises(frappe.ValidationError):
			self._validate(_cheque(), self._doc(paid_amount=60), others=[_transfer(50)])

	def test_received_cheque_must_leave_the_holding_account(self):
		with self.assertRaises(frappe.ValidationError):
			self._validate(_cheque(), self._doc(paid_from="Bank - SF", paid_to="Other Bank - SF"))

	def test_issued_cheque_must_reach_the_holding_account(self):
		with self.assertRaises(frappe.ValidationError):
			self._validate(_issued(), self._doc(paid_from="Bank - SF", paid_to="Other Bank - SF"))
		self._validate(_issued(), self._doc(paid_from="Bank - SF", paid_to="PDC Account - SF"))


class TestReturn(FrappeTestCase):
	def setUp(self):
		patch(HAS_PERMISSION, return_value=True).start()
		patch(CHEQUE_MODES, return_value=["Cheque"]).start()
		# company currency and every account's currency
		patch(CACHED_VALUE, return_value="BHD").start()
		self.addCleanup(patch.stopall)

	def _refs(self, *rows):
		return [frappe._dict(idx=i + 1, reference_doctype=d, reference_name=n, allocated_amount=a)
			for i, (d, n, a) in enumerate(rows)]

	def test_received_return_reopens_the_invoices_most_recent_first(self):
		cheque = _cheque(references=self._refs(("Sales Invoice", "SI-1", 60), ("Sales Invoice", "SI-2", 40)))
		pos = pdc.position_of(cheque, transfers=[_transfer(30)], returns=[])
		with patch(NEW_DOC, side_effect=Doc), patch(GET_VALUE, return_value="Debtors - SF"):
			je = pdc._return_journal(cheque, pos, "2026-09-25", "insufficient funds")
		party = [r for r in je.accounts if r.account == "Debtors - SF"]
		holding = [r for r in je.accounts if r.account == "PDC Account - SF"]
		# 70 still held: all 40 of SI-2 (the later row) first, then 30 of SI-1
		self.assertEqual([(r.reference_name, r.debit_in_account_currency) for r in party], [("SI-2", 40), ("SI-1", 30)])
		self.assertEqual(holding[0].credit_in_account_currency, 70)
		self.assertTrue(all(r.party == "CUST-1" and r.branch == "SFSS" for r in party))
		self.assertEqual(je[pdc.RETURN_AMOUNT_FIELD], 70)
		self.assertEqual(je[pdc.SOURCE_FIELD], "ACC-PAY-2026-00099")
		self.assertEqual(je.is_system_generated, 1)
		self.assertIn("insufficient funds", je.user_remark)

	def test_advance_goes_back_unreferenced_first(self):
		cheque = _cheque(unallocated_amount=25, references=self._refs(("Sales Invoice", "SI-1", 75)))
		pos = pdc.position_of(cheque, transfers=[], returns=[])
		with patch(NEW_DOC, side_effect=Doc), patch(GET_VALUE, return_value="Debtors - SF"):
			je = pdc._return_journal(cheque, pos, "2026-09-25", "")
		party = [(r.get("reference_name"), r.debit_in_account_currency) for r in je.accounts if r.account == "Debtors - SF"]
		self.assertEqual(party, [(None, 25), ("SI-1", 75)])

	def test_issued_return_credits_the_supplier_against_the_bill(self):
		cheque = _issued(references=self._refs(("Purchase Invoice", "PI-1", 100)))
		pos = pdc.position_of(cheque, transfers=[], returns=[])
		with patch(NEW_DOC, side_effect=Doc), patch(GET_VALUE, return_value="Creditors - SF"):
			je = pdc._return_journal(cheque, pos, "2026-09-25", "")
		party = [r for r in je.accounts if r.account == "Creditors - SF"]
		holding = [r for r in je.accounts if r.account == "PDC Account - SF"]
		self.assertEqual((party[0].reference_name, party[0].credit_in_account_currency), ("PI-1", 100))
		self.assertEqual(holding[0].debit_in_account_currency, 100)

	def test_invoice_on_another_account_is_not_referenced(self):
		cheque = _cheque(references=self._refs(("Sales Invoice", "SI-1", 100)))
		pos = pdc.position_of(cheque, transfers=[], returns=[])
		with patch(NEW_DOC, side_effect=Doc), patch(GET_VALUE, return_value="Other Debtors - SF"):
			je = pdc._return_journal(cheque, pos, "2026-09-25", "")
		self.assertFalse([r for r in je.accounts if r.get("reference_name")])

	def _return_pdc(self, cheque, transfers=(), returns=(), date="2026-09-25"):
		with patch(GET_DOC, return_value=cheque), patch(
			TRANSFER_ROWS, return_value={cheque.name: list(transfers)}
		), patch(RETURN_ROWS, return_value={cheque.name: list(returns)}):
			return pdc.return_pdc(cheque.name, date, "bounced")

	def test_refuses_a_future_date(self):
		with self.assertRaises(frappe.ValidationError):
			self._return_pdc(_cheque(), date="2999-01-01")

	def test_refuses_before_the_cheque_existed(self):
		with self.assertRaises(frappe.ValidationError):
			self._return_pdc(_cheque(), date="2026-08-01")

	def test_refuses_when_nothing_is_left(self):
		with self.assertRaises(frappe.ValidationError):
			self._return_pdc(_cheque(), transfers=[_transfer(100)])

	def test_refuses_a_second_return_while_one_is_in_draft(self):
		with self.assertRaises(frappe.ValidationError):
			self._return_pdc(_cheque(), returns=[_return(100, docstatus=0)])

	def test_journal_validate_caps_a_return_at_what_is_left(self):
		doc = frappe._dict(name="NEW-JV", meta=MagicMock())
		doc.meta.has_field = lambda field: True
		doc[pdc.SOURCE_FIELD] = "ACC-PAY-2026-00099"
		doc[pdc.RETURN_AMOUNT_FIELD] = 80
		with patch(GET_DOC, return_value=_cheque()), patch(
			TRANSFER_ROWS, return_value={"ACC-PAY-2026-00099": [_transfer(30)]}
		), patch(RETURN_ROWS, return_value={}):
			with self.assertRaises(frappe.ValidationError):
				pdc.journal_validate(doc)
			doc[pdc.RETURN_AMOUNT_FIELD] = 70
			pdc.journal_validate(doc)
