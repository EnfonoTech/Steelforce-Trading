"""Tests for the one-call customer context of the selling forms (sales_invoice_override.get_customer_selling_context).

    bench --site <scratch-site> run-tests --module sf_trading.tests.test_customer_selling_context

The database reads are replaced, so only the rules run: which limit applies, what is added up, what is asked.
"""

from unittest.mock import MagicMock, patch

import frappe
from frappe.tests.utils import FrappeTestCase

from sf_trading.api import sales_invoice_override as so

COMPANY = "Steel Force Trading WLL"


def limits(*rows):
	return [frappe._dict(company=c, credit_limit=v) for c, v in rows]


def context(customer="C1", company=COMPANY, credit_rows=(), team=(), used=0, overdue=None):
	db = MagicMock()
	db.sql.return_value = [[used]]
	with patch.object(so.frappe, "has_permission", return_value=True), \
		patch.object(so.frappe, "db", db), \
		patch.object(so.frappe, "get_all", side_effect=[limits(*credit_rows), list(team)]), \
		patch.object(so, "_get_overdue_invoice", return_value=overdue) as overdue_check:
		result = so.get_customer_selling_context(customer, company)
	return result, db, overdue_check


class TestCustomerSellingContext(FrappeTestCase):
	def test_no_customer_is_nothing_to_ask(self):
		self.assertEqual(so.get_customer_selling_context("", COMPANY), {})

	def test_the_limit_of_this_company_applies(self):
		result, db, overdue_check = context(credit_rows=[("Other Co", 900), (COMPANY, 5000)], used=1250.5)
		self.assertEqual(result["credit_limit"], 5000)
		self.assertEqual(result["credit_limit_any"], 5000)
		self.assertEqual(result["credit_used"], 1250.5)
		overdue_check.assert_called_once_with("C1", COMPANY)

	def test_a_customer_with_a_limit_in_another_company_only_has_no_limit_here(self):
		# the credit gate reads the company's own row (0 here); the "available credit" field has always shown the
		# first row when this company has none
		result, db, overdue_check = context(credit_rows=[("Other Co", 900)], used=100)
		self.assertEqual(result["credit_limit"], 0)
		self.assertEqual(result["credit_limit_any"], 900)
		self.assertEqual(result["credit_used"], 100)
		overdue_check.assert_not_called()  # no limit here, nothing to be overdue against
		self.assertIsNone(result["overdue"])

	def test_a_customer_with_no_limit_costs_no_invoice_query(self):
		result, db, overdue_check = context(credit_rows=[], used=999)
		self.assertEqual((result["credit_limit"], result["credit_limit_any"], result["credit_used"]), (0, 0, 0))
		db.sql.assert_not_called()
		overdue_check.assert_not_called()

	def test_the_overdue_invoice_and_the_sales_team_come_back_with_it(self):
		team = [frappe._dict(sales_person="Ali", allocated_percentage=60), frappe._dict(sales_person="Sara", allocated_percentage=40)]
		late = frappe._dict(name="SINV-1", posting_date="2026-08-01", outstanding_amount=75)
		result, db, overdue_check = context(credit_rows=[(COMPANY, 1000)], team=team, overdue=late)
		self.assertEqual(result["overdue"], late)
		self.assertEqual([r.sales_person for r in result["sales_team"]], ["Ali", "Sara"])
