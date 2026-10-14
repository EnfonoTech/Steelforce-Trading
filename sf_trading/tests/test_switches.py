"""The on/off switches of SF Trading Settings, read the way a hook needs them (sf_trading/switches.py).

    bench --site <site> run-tests --module sf_trading.tests.test_switches
"""

import unittest
from unittest.mock import MagicMock, patch

import frappe
from frappe.tests.utils import FrappeTestCase

from sf_trading import switches


def stored(value):
	"""What `SELECT value FROM tabSingles ...` returns for a switch whose row holds `value` (None: no row at all)."""
	return patch.object(switches.frappe.db, "sql", return_value=() if value is None else ((value,),))


class TestWhatIsStored(FrappeTestCase):
	def test_a_switch_nobody_stored_is_the_default_which_is_on(self):
		with stored(None):
			self.assertTrue(switches.is_on("title_case_masters"))

	def test_the_default_can_be_off(self):
		with stored(None):
			self.assertFalse(switches.is_on("some_switch", default=False))

	def test_a_ticked_switch_is_on_and_an_unticked_one_is_off(self):
		for value, expected in (("1", True), (1, True), ("0", False), (0, False)):
			with stored(value):
				self.assertEqual(switches.is_on("title_case_masters"), expected, value)

	def test_an_unticked_switch_stays_off_whatever_the_default(self):
		with stored("0"):
			self.assertFalse(switches.is_on("title_case_masters", default=True))
		with stored("1"):
			self.assertTrue(switches.is_on("some_switch", default=False))

	def test_an_empty_value_is_not_a_choice(self):
		for value in ("", None):
			with patch.object(switches.frappe.db, "sql", return_value=((value,),)):
				self.assertTrue(switches.is_on("title_case_masters"))

	def test_a_read_that_fails_is_the_default_and_never_stops_a_save(self):
		with patch.object(switches.frappe.db, "sql", side_effect=RuntimeError("boom")):
			self.assertTrue(switches.is_on("title_case_masters"))
			self.assertFalse(switches.is_on("some_switch", default=False))

	def test_the_stored_row_is_what_is_asked_for(self):
		with stored("1") as sql:
			switches.is_on("number_new_accounts")
		query, params = sql.call_args.args
		self.assertIn("tabSingles", query)
		self.assertEqual(params, ("SF Trading Settings", "number_new_accounts"))

	def test_neither_the_cast_reader_nor_the_meta_is_used(self):
		# get_single_value casts a missing Check to 0 (OFF) and raises for a field not migrated yet
		with stored(None), patch.object(switches.frappe.db, "get_single_value") as cast, \
				patch.object(switches.frappe, "get_meta") as meta:
			switches.is_on("title_case_masters")
		cast.assert_not_called()
		meta.assert_not_called()


@unittest.skipIf(isinstance(getattr(frappe.local, "db", None), MagicMock), "needs a real database")
class TestOnTheRealTable(FrappeTestCase):
	"""The three states in the real Singles table (rolled back with the class)."""

	FIELD = "zz_switch_probe"

	def setUp(self):
		frappe.db.sql("DELETE FROM `tabSingles` WHERE `doctype` = %s AND `field` = %s", ("SF Trading Settings", self.FIELD))

	def put(self, value):
		frappe.db.sql("INSERT INTO `tabSingles` (`doctype`, `field`, `value`) VALUES (%s, %s, %s)",
			("SF Trading Settings", self.FIELD, value))

	def test_no_row_one_zero(self):
		self.assertTrue(switches.is_on(self.FIELD))
		self.put("0")
		self.assertFalse(switches.is_on(self.FIELD))
		frappe.db.sql("UPDATE `tabSingles` SET `value` = '1' WHERE `doctype` = %s AND `field` = %s", ("SF Trading Settings", self.FIELD))
		self.assertTrue(switches.is_on(self.FIELD))

	def test_a_field_the_doctype_does_not_have_does_not_raise(self):
		self.assertTrue(switches.is_on("a_field_that_is_not_in_the_doctype"))
