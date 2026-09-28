"""Tests for company-restricted Sales Person selection on the Sales Team child table.

    bench --site <scratch-site> run-tests --module sf_trading.tests.test_salesperson_by_company
"""

import frappe
from frappe.tests.utils import FrappeTestCase

from sf_trading.salesperson_by_company import get_salesperson_query

COMPANY_A = "_Test Company"
COMPANY_B = "_Test Company 1"
UNRESTRICTED = "_Test SP Unrestricted"
RESTRICTED_A = "_Test SP Restricted A"


def _make_salesperson(name, companies=None):
	if frappe.db.exists("Sales Person", name):
		frappe.delete_doc("Sales Person", name, force=True, ignore_permissions=True)
	doc = frappe.get_doc({
		"doctype": "Sales Person",
		"sales_person_name": name,
		"enabled": 1,
		"is_group": 0,
	})
	for company in companies or []:
		doc.append("custom_companies", {"company": company})
	doc.insert(ignore_permissions=True)
	return doc


class TestSalespersonByCompany(FrappeTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		_make_salesperson(UNRESTRICTED)
		_make_salesperson(RESTRICTED_A, companies=[COMPANY_A])

	@staticmethod
	def _names(company):
		rows = get_salesperson_query(
			"Sales Person", "_Test SP", "name", 0, 100, {"company": company}
		)
		return {row[0] for row in rows}

	def test_unrestricted_salesperson_appears_for_any_company(self):
		"""No custom_companies rows at all -- available everywhere, both before and after this
		feature: existing Salesperson records must not vanish from an invoice's picker."""
		self.assertIn(UNRESTRICTED, self._names(COMPANY_A))
		self.assertIn(UNRESTRICTED, self._names(COMPANY_B))

	def test_restricted_salesperson_appears_only_for_its_company(self):
		self.assertIn(RESTRICTED_A, self._names(COMPANY_A))
		self.assertNotIn(RESTRICTED_A, self._names(COMPANY_B))

	def test_blank_company_returns_everyone(self):
		"""An unsaved document with no Company chosen yet must not empty the picker."""
		names = self._names("")
		self.assertIn(UNRESTRICTED, names)
		self.assertIn(RESTRICTED_A, names)
