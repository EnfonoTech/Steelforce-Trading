"""Tests for reject_pdc (GS Issue 25 -- a bounced cheque gets its own status).

    bench --site <scratch-site> run-tests --module sf_trading.tests.test_pdc_reject
"""

from unittest.mock import MagicMock, patch

import frappe
from frappe.tests.utils import FrappeTestCase

from sf_trading import pdc_transfer as pdc

HAS_PERMISSION = "sf_trading.pdc_transfer.frappe.has_permission"
GET_DOC = "sf_trading.pdc_transfer.frappe.get_doc"
CHEQUE_MODES = "sf_trading.pdc_transfer.cheque_modes"
TRANSFERS_FOR = "sf_trading.pdc_transfer.transfers_for"


def _cheque(**overrides):
	pe = MagicMock()
	pe.name = overrides.pop("name", "ACC-PAY-2026-00099")
	pe.docstatus = overrides.pop("docstatus", 1)
	pe.payment_type = overrides.pop("payment_type", "Receive")
	pe.mode_of_payment = overrides.pop("mode_of_payment", "Cheque")
	pe.clearance_date = overrides.pop("clearance_date", None)
	pe.get = lambda field, default=None: overrides.get(field, default)
	return pe


class TestRejectPdc(FrappeTestCase):
	def setUp(self):
		self._perm = patch(HAS_PERMISSION, return_value=True).start()
		self._modes = patch(CHEQUE_MODES, return_value=["Cheque"]).start()
		self.addCleanup(patch.stopall)

	def test_refuses_a_draft_cheque(self):
		with patch(GET_DOC, return_value=_cheque(docstatus=0)):
			with self.assertRaises(frappe.ValidationError):
				pdc.reject_pdc("ACC-PAY-2026-00099")

	def test_refuses_a_non_cheque_payment(self):
		with patch(GET_DOC, return_value=_cheque(mode_of_payment="Cash")):
			with self.assertRaises(frappe.ValidationError):
				pdc.reject_pdc("ACC-PAY-2026-00099")

	def test_refuses_an_already_cleared_cheque(self):
		with patch(GET_DOC, return_value=_cheque(clearance_date="2026-09-01")):
			with self.assertRaises(frappe.ValidationError):
				pdc.reject_pdc("ACC-PAY-2026-00099")

	def test_refuses_a_cheque_that_already_has_a_transfer(self):
		transfer = frappe._dict({"name": "ACC-PAY-2026-00150", "docstatus": 1})
		with patch(GET_DOC, return_value=_cheque()):
			with patch(TRANSFERS_FOR, return_value={"ACC-PAY-2026-00099": transfer}):
				with self.assertRaises(frappe.ValidationError):
					pdc.reject_pdc("ACC-PAY-2026-00099")

	def test_refuses_a_cheque_already_marked_rejected(self):
		with patch(GET_DOC, return_value=_cheque(custom_pdc_rejection_date="2026-09-10")):
			with patch(TRANSFERS_FOR, return_value={}):
				with self.assertRaises(frappe.ValidationError):
					pdc.reject_pdc("ACC-PAY-2026-00099")

	def test_a_clean_cheque_gets_marked_rejected(self):
		with patch(GET_DOC, return_value=_cheque()):
			with patch(TRANSFERS_FOR, return_value={}):
				with patch("sf_trading.pdc_transfer.frappe.db.set_value") as set_value:
					result = pdc.reject_pdc("ACC-PAY-2026-00099", rejection_date="2026-09-23")
		set_value.assert_called_once()
		self.assertEqual(result["name"], "ACC-PAY-2026-00099")
		self.assertEqual(result[pdc.REJECTION_FIELD], "2026-09-23")
