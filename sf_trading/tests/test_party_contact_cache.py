"""Tests for the cached Customer/Supplier phone field (GS Issue 11).

    bench --site <scratch-site> run-tests --module sf_trading.tests.test_party_contact_cache
"""

from unittest.mock import patch

from frappe.tests.utils import FrappeTestCase

from sf_trading import party_contact_cache as pcc

GET_ALL = "sf_trading.party_contact_cache.frappe.get_all"


class StubDoc:
	def __init__(self, doctype, **fields):
		self.doctype = doctype
		self.name = fields.pop("name", None)
		self.__dict__.update(fields)

	def get(self, key, default=None):
		return self.__dict__.get(key, default)


def _fake_get_all(contacts=None, contact_phones=None, addresses=None, address_phones=None):
	"""Route a mocked frappe.get_all call by which table it's reading -- Contact and Address need
	independent answers for the priority/fallback tests, not just a fixed call-order list."""
	contacts = contacts or []
	contact_phones = contact_phones or []
	addresses = addresses or []
	address_phones = address_phones or []

	def fake(doctype, filters=None, pluck=None, **kwargs):
		filters = filters or {}
		if doctype == "Dynamic Link":
			return contacts if filters.get("parenttype") == "Contact" else addresses
		if doctype == "Contact Phone":
			return contact_phones
		if doctype == "Address":
			return address_phones
		raise AssertionError(f"unexpected doctype {doctype}")

	return fake


class TestPartyPhoneNumbers(FrappeTestCase):
	def test_no_links_at_all_returns_empty(self):
		with patch(GET_ALL, side_effect=_fake_get_all()):
			self.assertEqual(pcc.party_phone_numbers("Customer", "CUST-0001"), [])

	def test_no_party_name_returns_empty_without_a_query(self):
		with patch(GET_ALL) as get_all:
			self.assertEqual(pcc.party_phone_numbers("Customer", None), [])
		get_all.assert_not_called()

	def test_falls_back_to_address_when_there_is_no_contact(self):
		"""The real sft-uat shape found 2026-09-23: 0 Contacts, phone sits on the Address instead."""
		fake = _fake_get_all(addresses=["ADDR-0001"], address_phones=["33641181"])
		with patch(GET_ALL, side_effect=fake):
			numbers = pcc.party_phone_numbers("Customer", "CUST-0001")
		self.assertEqual(numbers, ["33641181"])

	def test_contact_numbers_come_before_address_numbers(self):
		fake = _fake_get_all(
			contacts=["CONTACT-0001"],
			contact_phones=["33445566"],
			addresses=["ADDR-0001"],
			address_phones=["99887766"],
		)
		with patch(GET_ALL, side_effect=fake):
			numbers = pcc.party_phone_numbers("Customer", "CUST-0001")
		self.assertEqual(numbers, ["33445566", "99887766"])

	def test_numbers_are_deduplicated_in_order(self):
		fake = _fake_get_all(
			contacts=["CONTACT-0001"],
			contact_phones=["33445566", "99887766"],
			addresses=["ADDR-0001"],
			address_phones=["33445566"],
		)
		with patch(GET_ALL, side_effect=fake):
			numbers = pcc.party_phone_numbers("Customer", "CUST-0001")
		self.assertEqual(numbers, ["33445566", "99887766"])


class TestSyncFromContact(FrappeTestCase):
	def test_refreshes_every_linked_party(self):
		links = [
			{"link_doctype": "Customer", "link_name": "CUST-0001"},
			{"link_doctype": "Supplier", "link_name": "SUP-0001"},
		]
		with patch(GET_ALL, return_value=links):
			with patch.object(pcc, "_refresh_cache") as refresh:
				pcc.sync_from_contact(StubDoc("Contact", name="CONTACT-0001"))
		self.assertEqual(refresh.call_count, 2)
		refresh.assert_any_call("Customer", "CUST-0001")
		refresh.assert_any_call("Supplier", "SUP-0001")


class TestSyncFromAddress(FrappeTestCase):
	def test_refreshes_every_linked_party(self):
		links = [{"link_doctype": "Customer", "link_name": "CUST-0001"}]
		with patch(GET_ALL, return_value=links):
			with patch.object(pcc, "_refresh_cache") as refresh:
				pcc.sync_from_address(StubDoc("Address", name="ADDR-0001"))
		refresh.assert_called_once_with("Customer", "CUST-0001")


class TestClearOnTrash(FrappeTestCase):
	def test_contact_trash_excludes_itself_from_the_recompute(self):
		links = [{"link_doctype": "Customer", "link_name": "CUST-0001"}]
		with patch(GET_ALL, return_value=links):
			with patch.object(pcc, "_refresh_cache") as refresh:
				pcc.clear_on_contact_trash(StubDoc("Contact", name="CONTACT-0001"))
		refresh.assert_called_once_with("Customer", "CUST-0001", exclude_contact="CONTACT-0001")

	def test_address_trash_excludes_itself_from_the_recompute(self):
		links = [{"link_doctype": "Supplier", "link_name": "SUP-0001"}]
		with patch(GET_ALL, return_value=links):
			with patch.object(pcc, "_refresh_cache") as refresh:
				pcc.clear_on_address_trash(StubDoc("Address", name="ADDR-0001"))
		refresh.assert_called_once_with("Supplier", "SUP-0001", exclude_address="ADDR-0001")
