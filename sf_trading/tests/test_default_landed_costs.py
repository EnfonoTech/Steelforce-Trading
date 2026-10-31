# sf_trading/tests/test_default_landed_costs.py
"""SF Trading Settings > Default Landed Cost Charges: what the settings validation accepts.

Runs on a bench only (it imports the Settings controller); nothing is read or written:

    bench --site <scratch-site> run-tests --module sf_trading.tests.test_default_landed_costs
"""

import frappe
from frappe.tests.utils import FrappeTestCase

from sf_trading.sf_trading.doctype.sf_trading_settings.sf_trading_settings import SFTradingSettings


class Row(frappe._dict):
	pass


class FakeSettings:
	"""Just enough of the Settings doc for the one validation, called unbound."""

	def __init__(self, rows):
		self.rows = rows

	def get(self, fieldname):
		return self.rows if fieldname == "default_landed_costs" else []


def row(idx, company="SFB", charge="Freight", applies_to="Import", value=3.0):
	return Row(idx=idx, company=company, charge=charge, applies_to=applies_to, basis="% of Price", value=value)


def check(*rows):
	SFTradingSettings.validate_default_landed_costs(FakeSettings(list(rows)))


class TestDefaultLandedCostValidation(FrappeTestCase):
	def test_distinct_charges_pass(self):
		check(row(1), row(2, charge="Customs Duty"), row(3, company="Other"), row(4, applies_to="Local"))

	def test_a_charge_is_not_listed_twice_for_the_same_company_and_half(self):
		with self.assertRaises(frappe.ValidationError):
			check(row(1), row(2, charge="freight "))

	def test_import_and_local_clashes_with_either_half(self):
		with self.assertRaises(frappe.ValidationError):
			check(row(1, applies_to="Local"), row(2, applies_to="Import and Local"))
		with self.assertRaises(frappe.ValidationError):
			check(row(1, applies_to="Import and Local"), row(2, applies_to="Import"))

	def test_a_negative_value_is_refused(self):
		with self.assertRaises(frappe.ValidationError):
			check(row(1, value=-1))

	def test_zero_is_allowed(self):
		check(row(1, value=0))
