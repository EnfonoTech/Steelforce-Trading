"""Tests for the cached Customer/Supplier phone field (GS Issue 11).

    bench --site <scratch-site> run-tests --module sf_trading.tests.test_party_contact_cache
"""

from unittest.mock import patch

from frappe.tests.utils import FrappeTestCase

from sf_trading import party_contact_cache as pcc

DYNAMIC_LINK_GET_ALL = "sf_trading.party_contact_cache.frappe.get_all"


class StubDoc:
	def __init__(self, doctype, **fields):
		self.doctype = doctype
		self.name = fields.pop("name", None)
		self.__dict__.update(fields)

	def get(self, key, default=None):
		return self.__dict__.get(key, default)


class TestPartyPhoneNumbers(FrappeTestCase):
	def test_no_linked_contact_returns_empty(self):
		with patch(DYNAMIC_LINK_GET_ALL, return_value=[]):
			self.assertEqual(pcc.party_phone_numbers("Customer", "CUST-0001"), [])

	def test_no_party_name_returns_empty_without_a_query(self):
		with patch(DYNAMIC_LINK_GET_ALL) as get_all:
			self.assertEqual(pcc.party_phone_numbers("Customer", None), [])
		get_all.assert_not_called()

	def test_numbers_are_deduplicated_in_order(self):
		calls = [["contact-a", "contact-b"], ["33445566", "99887766", "33445566"]]

		def fake_get_all(doctype, filters=None, pluck=None, **kwargs):
			return calls.pop(0)

		with patch("sf_trading.party_contact_cache.frappe.get_all", side_effect=fake_get_all):
			numbers = pcc.party_phone_numbers("Customer", "CUST-0001")
		self.assertEqual(numbers, ["33445566", "99887766"])


class TestSyncFromContact(FrappeTestCase):
	def test_refreshes_every_linked_party(self):
		links = [
			{"link_doctype": "Customer", "link_name": "CUST-0001"},
			{"link_doctype": "Supplier", "link_name": "SUP-0001"},
		]
		with patch("sf_trading.party_contact_cache.frappe.get_all", return_value=links):
			with patch.object(pcc, "_refresh_cache") as refresh:
				pcc.sync_from_contact(StubDoc("Contact", name="CONTACT-0001"))
		self.assertEqual(refresh.call_count, 2)
		refresh.assert_any_call("Customer", "CUST-0001")
		refresh.assert_any_call("Supplier", "SUP-0001")
