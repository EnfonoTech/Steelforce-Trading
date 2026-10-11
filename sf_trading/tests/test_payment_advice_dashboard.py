"""Payment Advice and Payment Entry list each other in their Connections.

    bench --site <site> run-tests --module sf_trading.tests.test_payment_advice_dashboard
"""

import frappe
from frappe.tests.utils import FrappeTestCase

from sf_trading.api import payment_advice_dashboard as dash


def items(data):
	return [item for group in data.transactions for item in group.get("items", [])]


class TestPaymentEntryListsItsAdvice(FrappeTestCase):
	def test_the_advice_is_listed_and_read_from_the_entrys_own_field(self):
		data = dash.get_payment_entry_dashboard_data(
			{"transactions": [{"label": "Reference", "items": ["Journal Entry"]}]}
		)
		self.assertIn("Payment Advice", items(data))
		self.assertIn("Journal Entry", items(data))  # what was there stays
		self.assertEqual(data.internal_links["Payment Advice"], "custom_payment_advice")
		# the advice's own stamp is the fallback while the entry carries no advice
		self.assertEqual(data.non_standard_fieldnames["Payment Advice"], "payment_entry")

	def test_it_joins_an_existing_payment_group_and_is_not_listed_twice(self):
		start = {"transactions": [{"label": "Payment", "items": ["Journal Entry"]}]}
		data = dash.get_payment_entry_dashboard_data(start)
		self.assertEqual(data.transactions[0]["items"], ["Journal Entry", "Payment Advice"])
		again = dash.get_payment_entry_dashboard_data(data)
		self.assertEqual(items(again).count("Payment Advice"), 1)

	def test_an_empty_dashboard_gets_a_group_of_its_own(self):
		data = dash.get_payment_entry_dashboard_data(None)
		self.assertEqual(items(data), ["Payment Advice"])


class TestPaymentAdviceListsItsEntries(FrappeTestCase):
	def test_the_entries_are_found_by_their_advice_field(self):
		data = dash.get_payment_advice_dashboard_data({})
		self.assertEqual(items(data), ["Payment Entry"])
		self.assertEqual(data.fieldname, "custom_payment_advice")
		self.assertEqual(data.non_standard_fieldnames["Payment Entry"], "custom_payment_advice")
		# external only: an advice paid in parts has several entries, its own field remembers one
		self.assertNotIn("Payment Entry", data.get("internal_links", {}))

	def test_a_fieldname_someone_else_set_still_finds_the_entries(self):
		data = dash.get_payment_advice_dashboard_data({"fieldname": "other"})
		self.assertEqual(data.fieldname, "other")
		self.assertEqual(data.non_standard_fieldnames["Payment Entry"], "custom_payment_advice")


class TestWiredIntoTheMeta(FrappeTestCase):
	def test_both_forms_carry_the_other_in_their_connections(self):
		entry = frappe.get_meta("Payment Entry").get_dashboard_data()
		advice = frappe.get_meta("Payment Advice").get_dashboard_data()
		self.assertIn("Payment Advice", items(entry))
		self.assertIn("Payment Entry", items(advice))
