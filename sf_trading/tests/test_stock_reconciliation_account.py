"""Tests for the Company-fixed Stock Reconciliation Difference Account
(stock_reconciliation_account.py).

    bench --site <scratch-site> run-tests --module sf_trading.tests.test_stock_reconciliation_account
"""

import frappe
from frappe.tests.utils import FrappeTestCase

from sf_trading.stock_reconciliation_account import (
	ACCOUNT_FIELD,
	get_difference_account,
	set_difference_account,
	validate_company_account,
	validate_difference_account,
)


class TestStockReconciliationAccount(FrappeTestCase):
	def setUp(self):
		self.company = frappe.db.get_value("Company", {}, "name")
		ledgers = frappe.get_all(
			"Account", filters={"company": self.company, "is_group": 0}, pluck="name", limit=2
		)
		self.account, self.other_account = ledgers[0], ledgers[1]

	def _set_company_account(self, account):
		frappe.db.set_value("Company", self.company, ACCOUNT_FIELD, account)
		frappe.clear_cache(doctype="Company")

	def _reco(self, purpose="Stock Reconciliation", expense_account=None):
		return frappe._dict(
			doctype="Stock Reconciliation",
			company=self.company,
			purpose=purpose,
			expense_account=expense_account,
		)

	def test_refused_when_company_has_no_account(self):
		self._set_company_account(None)
		with self.assertRaises(frappe.ValidationError):
			validate_difference_account(self._reco(expense_account=self.account))

	def test_refused_with_any_other_account(self):
		self._set_company_account(self.account)
		with self.assertRaises(frappe.ValidationError):
			validate_difference_account(self._reco(expense_account=self.other_account))

	def test_passes_with_the_company_account(self):
		self._set_company_account(self.account)
		validate_difference_account(self._reco(expense_account=self.account))  # must not raise

	def test_blank_account_is_filled_from_the_company(self):
		self._set_company_account(self.account)
		doc = self._reco()
		set_difference_account(doc)
		self.assertEqual(doc.expense_account, self.account)
		validate_difference_account(doc)  # must not raise

	def test_opening_stock_is_not_checked(self):
		self._set_company_account(None)
		validate_difference_account(self._reco("Opening Stock", self.other_account))  # must not raise
		doc = self._reco("Opening Stock")
		set_difference_account(doc)
		self.assertFalse(doc.expense_account)

	def test_form_gets_the_company_account(self):
		self._set_company_account(self.account)
		self.assertEqual(get_difference_account("Stock Reconciliation", self.company), self.account)

	def test_form_still_gets_core_account_for_opening_stock(self):
		from erpnext.stock.doctype.stock_reconciliation.stock_reconciliation import (
			get_difference_account as core,
		)

		self._set_company_account(self.account)
		self.assertEqual(
			get_difference_account("Opening Stock", self.company), core("Opening Stock", self.company)
		)

	def test_company_refuses_another_companys_account(self):
		other_company = frappe.db.get_value("Company", {"name": ("!=", self.company)}, "name")
		if not other_company:
			self.skipTest("needs a second Company (a single-company site such as production has none)")
		foreign = frappe.db.get_value("Account", {"company": other_company, "is_group": 0}, "name")
		if not foreign:
			self.skipTest("the second Company has no ledger account to offer")
		doc = frappe._dict(name=self.company)
		doc[ACCOUNT_FIELD] = foreign
		with self.assertRaises(frappe.ValidationError):
			validate_company_account(doc)

	def test_company_refuses_a_group_account(self):
		group = frappe.db.get_value("Account", {"company": self.company, "is_group": 1}, "name")
		doc = frappe._dict(name=self.company)
		doc[ACCOUNT_FIELD] = group
		with self.assertRaises(frappe.ValidationError):
			validate_company_account(doc)

	def test_company_accepts_its_own_ledger(self):
		doc = frappe._dict(name=self.company)
		doc[ACCOUNT_FIELD] = self.account
		validate_company_account(doc)  # must not raise
