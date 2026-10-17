# sf_trading/tests/test_item_case.py
"""The Item master is kept in CAPITALS (sf_trading/item_case.py).

The switch is patched and the Item is a stand-in, so nothing is created:

    bench --site <site> run-tests --module sf_trading.tests.test_item_case
"""

from unittest.mock import patch

from frappe.tests.utils import FrappeTestCase

from sf_trading import item_case


class FakeItem:
	"""Just enough of a Document: get, attribute access and is_new."""

	def __init__(self, new=True, **values):
		self._new = new
		self.__dict__.update(values)

	def get(self, key, default=None):
		return getattr(self, key, default)

	def is_new(self):
		return self._new


class TestItemCase(FrappeTestCase):
	def run_hook(self, item, switch=True):
		with patch.object(item_case.switches, "is_on", return_value=switch):
			item_case.capitalise_item(item)
		return item

	def test_capitals_strips_and_upper_cases_only(self):
		self.assertEqual(item_case.capitals("  Office Expenses "), "OFFICE EXPENSES")
		self.assertEqual(item_case.capitals("Customs Duty "), "CUSTOMS DUTY")
		# nothing inside the text is reshaped: double spaces, symbols and non-breaking spaces stay
		self.assertEqual(item_case.capitals("20v  drill\xa0bit m12x260"), "20V  DRILL\xa0BIT M12X260")
		self.assertEqual(item_case.capitals("SS ELBOW 90° 25MM"), "SS ELBOW 90° 25MM")

	def test_blank_and_non_text_are_left_alone(self):
		self.assertIsNone(item_case.capitals(None))
		self.assertEqual(item_case.capitals(""), "")
		self.assertEqual(item_case.capitals(12), 12)

	def test_a_new_item_gets_a_capital_code_and_name(self):
		item = self.run_hook(FakeItem(new=True, item_code="Gypsum Board 16mm", item_name="Gypsum board 16mm"))
		self.assertEqual((item.item_code, item.item_name), ("GYPSUM BOARD 16MM", "GYPSUM BOARD 16MM"))

	def test_an_existing_item_keeps_its_code_and_gets_a_capital_name(self):
		item = self.run_hook(FakeItem(new=False, item_code="Office Expenses", item_name="Office Expenses"))
		self.assertEqual(item.item_code, "Office Expenses")  # the record's name: a rename, never a save
		self.assertEqual(item.item_name, "OFFICE EXPENSES")

	def test_an_item_already_in_capitals_is_unchanged(self):
		item = self.run_hook(FakeItem(new=True, item_code="110201100143", item_name="GYPSUM BOARD 122x244 16MM"))
		self.assertEqual(item.item_code, "110201100143")
		self.assertEqual(item.item_name, "GYPSUM BOARD 122X244 16MM")

	def test_no_name_yet_is_not_invented(self):
		item = self.run_hook(FakeItem(new=True, item_code="Brass Tap", item_name=None))
		self.assertEqual(item.item_code, "BRASS TAP")
		self.assertIsNone(item.item_name)

	def test_the_switch_turns_it_off(self):
		item = self.run_hook(FakeItem(new=True, item_code="Brass Tap", item_name="Brass tap"), switch=False)
		self.assertEqual((item.item_code, item.item_name), ("Brass Tap", "Brass tap"))
