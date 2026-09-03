"""Reads of a long name list must not build one enormous query.

    bench --site <site> run-tests --module sf_trading.tests.test_batched_in_queries

Note on where this bites: `DatabaseQuery.validate_generated_query` -- the sqlparse pass that
refuses a statement over 10,000 tokens -- arrived in frappe 15.114. A bench still on 15.112 will
not raise, so these tests assert the SHAPE of what is sent (batch count, statement size) rather
than relying on the error, and they hold on either version.
"""

import frappe
from frappe.tests.utils import FrappeTestCase

from sf_trading.api.query_report_columns import get_data_for_custom_field, install
from sf_trading.query import IN_BATCH, fetch_in


class QueryWatcher:
	"""Records every statement frappe sends while it is open."""

	def __enter__(self):
		self.queries = []
		self._real = frappe.db.sql

		def spy(query, values=None, *args, **kwargs):
			self.queries.append(str(query))
			if values is not None:
				return self._real(query, values, *args, **kwargs)
			return self._real(query, *args, **kwargs)

		frappe.db.sql = spy
		return self

	def __exit__(self, *exc):
		frappe.db.sql = self._real

	@property
	def longest(self):
		return max((len(q) for q in self.queries), default=0)

	def naming(self, table):
		return [q for q in self.queries if table in q]


class TestBatchedInQueries(FrappeTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		cls.names = frappe.get_all("Sales Invoice", pluck="name", limit=25)
		if not cls.names:
			cls.names = []

	def a_long_list(self, real=None):
		"""Real names first, then padding, so the list is long without inventing data."""
		real = list(real if real is not None else self.names)
		return real + [f"SF-BATCH-TEST-{i:05d}" for i in range(6000)]

	def test_it_returns_the_rows_the_unbatched_read_would(self):
		if not self.names:
			self.skipTest("no Sales Invoice on this site")
		mine = fetch_in("Sales Invoice", self.names, fields=["name", "customer"])
		theirs = frappe.get_all(
			"Sales Invoice", filters={"name": ("in", self.names)}, fields=["name", "customer"],
			limit_page_length=0,
		)
		self.assertEqual(
			sorted((r["name"], r["customer"]) for r in mine),
			sorted((r["name"], r["customer"]) for r in theirs),
		)

	def test_a_long_list_is_split_and_every_statement_stays_small(self):
		values = self.a_long_list()
		with QueryWatcher() as watch:
			fetch_in("Sales Invoice", values, fields=["name", "customer"])

		reads = watch.naming("tabSales Invoice")
		self.assertGreaterEqual(len(reads), len(values) // IN_BATCH, "the list must be split")
		# 10,000 sqlparse tokens is roughly 5,000 names; one batch is 500, so every statement
		# should be an order of magnitude inside the cap
		self.assertLess(watch.longest, 60_000, "a batch built a statement far larger than expected")

	def test_the_unbatched_read_is_what_this_avoids(self):
		"""Documents the failure, without depending on the frappe version to produce it."""
		values = self.a_long_list()
		with QueryWatcher() as watch:
			try:
				frappe.get_all("Sales Invoice", filters={"name": ("in", values)},
				               fields=["name", "customer"], limit_page_length=0)
			except Exception as refused:
				self.assertIn("token", str(refused).lower())
				return
		self.assertGreater(watch.longest, 60_000,
		                   "this frappe accepted the single query; it is still one huge statement")

	def test_blanks_and_duplicates_are_dropped(self):
		if not self.names:
			self.skipTest("no Sales Invoice on this site")
		one = self.names[0]
		rows = fetch_in("Sales Invoice", [one, one, None, "", one], fields=["name"])
		self.assertEqual([r["name"] for r in rows], [one])

	def test_an_empty_list_reads_nothing(self):
		with QueryWatcher() as watch:
			self.assertEqual(fetch_in("Sales Invoice", [], fields=["name"]), [])
			self.assertEqual(fetch_in("Sales Invoice", None, fields=["name"]), [])
		self.assertEqual(watch.naming("tabSales Invoice"), [], "an empty list must not read the table")

	def test_extra_filters_apply_to_every_batch(self):
		values = self.a_long_list()
		with QueryWatcher() as watch:
			fetch_in("Sales Invoice", values, filters={"docstatus": 1}, fields=["name"])
		reads = watch.naming("tabSales Invoice")
		self.assertTrue(reads)
		self.assertTrue(all("docstatus" in q for q in reads), "a batch was sent without the filter")

	def test_pluck_returns_a_flat_list(self):
		if not self.names:
			self.skipTest("no Sales Invoice on this site")
		self.assertEqual(sorted(fetch_in("Sales Invoice", self.names, pluck="name")), sorted(self.names))


class TestCustomFieldColumn(FrappeTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		cls.names = frappe.get_all("Sales Invoice", pluck="name", limit=20)

	def test_it_answers_exactly_as_frappe_does(self):
		if not self.names:
			self.skipTest("no Sales Invoice on this site")
		mine = get_data_for_custom_field("Sales Invoice", "customer", frappe.as_json(self.names))
		expected = dict(
			frappe.get_list("Sales Invoice", filters={"name": ("in", self.names)},
			                fields=["name", "customer"], as_list=1, limit_page_length=0)
		)
		self.assertEqual(dict(mine), expected)

	def test_a_report_sized_list_is_answered_rather_than_refused(self):
		values = list(self.names) + [f"SF-BATCH-TEST-{i:05d}" for i in range(6000)]
		with QueryWatcher() as watch:
			answer = get_data_for_custom_field("Sales Invoice", "customer", frappe.as_json(values))
		self.assertLess(watch.longest, 60_000)
		for name in self.names:
			self.assertIn(name, answer, "a real invoice went missing from the answer")

	def test_names_may_be_a_json_string_or_a_list(self):
		"""The desk sends a JSON string; python callers send a list."""
		if not self.names:
			self.skipTest("no Sales Invoice on this site")
		as_string = get_data_for_custom_field("Sales Invoice", "customer", frappe.as_json(self.names))
		as_list = get_data_for_custom_field("Sales Invoice", "customer", self.names)
		self.assertEqual(dict(as_string), dict(as_list))

	def test_the_internal_door_is_patched_too(self):
		"""A saved custom report calls the module attribute, which an override does not touch."""
		from frappe.desk import query_report

		install()
		self.assertEqual(
			query_report.get_data_for_custom_field.__module__,
			"sf_trading.api.query_report_columns",
		)
		install()  # idempotent
		self.assertEqual(
			query_report.get_data_for_custom_field.__module__,
			"sf_trading.api.query_report_columns",
		)

	def test_a_user_without_read_is_refused(self):
		frappe.set_user("Guest")
		try:
			with self.assertRaises(frappe.PermissionError):
				get_data_for_custom_field("Sales Invoice", "customer", frappe.as_json(self.names or ["x"]))
		finally:
			frappe.set_user("Administrator")
