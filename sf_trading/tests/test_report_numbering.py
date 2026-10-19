# sf_trading/tests/test_report_numbering.py
"""Every Report carries a number (sf_trading/report_numbering.py).

The database is patched at the module's own seams, so nothing is created or read:

    bench --site <scratch-site> run-tests --module sf_trading.tests.test_report_numbering
"""

import json
import os
import re
import sys
import types
from unittest.mock import MagicMock, patch

import frappe
from frappe.tests.utils import FrappeTestCase

from sf_trading import report_numbering as rn

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def read(*parts):
	with open(os.path.join(ROOT, *parts), encoding="utf-8") as f:
		return f.read()


class Register:
	"""An in-memory stand-in for the register and the series, behind the module's own seams."""

	def __init__(self, held=None, ready=True, series_start=0):
		self.held = dict(held or {})  # report name -> number
		self.ready = ready
		self.series = series_start
		self.inserted = []
		self.renamed = []
		self.deleted = []
		self.log = []
		self.patches = []

	def get_value(self, doctype, name, field=None, *a, **k):
		assert doctype == rn.REGISTER
		return self.held.get(name)

	def exists(self, doctype, filters=None):
		assert doctype == rn.REGISTER
		if isinstance(filters, dict):
			return any(n == filters["report_no"] for n in self.held.values())
		return filters in self.held

	def getseries(self, key, digits):
		assert key == rn.SERIES
		self.series += 1
		return ("%0" + str(digits) + "d") % self.series

	def get_doc(self, values):
		reg = self

		class _Doc:
			def insert(self_, **k):
				reg.held[values["report"]] = values["report_no"]
				reg.inserted.append(dict(values))

		return _Doc()

	def __enter__(self):
		naming = types.ModuleType("frappe.model.naming")
		naming.getseries = self.getseries
		renamer = types.ModuleType("frappe.model.rename_doc")

		# core's own signature: an argument it does not take raises, as it does in production
		def rename_doc(doctype=None, old=None, new=None, force=False, merge=False, ignore_permissions=False,
				ignore_if_exists=False, show_alert=True, rebuild_search=True, doc=None, validate=True):
			self.renamed.append((old, new))
			self.held[new] = self.held.pop(old)

		renamer.rename_doc = rename_doc
		self.saved_modules = {k: sys.modules.get(k) for k in ("frappe.model", "frappe.model.naming", "frappe.model.rename_doc")}
		sys.modules.setdefault("frappe.model", types.ModuleType("frappe.model"))
		sys.modules["frappe.model.naming"] = naming
		sys.modules["frappe.model.rename_doc"] = renamer
		db = MagicMock()
		db.table_exists.side_effect = lambda dt: self.ready
		db.has_column.side_effect = lambda dt, col: self.ready
		db.get_value.side_effect = self.get_value
		db.exists.side_effect = self.exists
		for target, attr, value in [
			(rn.frappe, "db", db),
			(rn.frappe, "get_doc", MagicMock(side_effect=self.get_doc)),
			(rn.frappe, "log_error", MagicMock(side_effect=lambda **k: self.log.append(k))),
			(rn.frappe, "get_traceback", MagicMock(return_value="tb")),
			(rn.frappe, "delete_doc", MagicMock(side_effect=lambda dt, name, **k: (self.deleted.append(name), self.held.pop(name, None)))),
		]:
			p = patch.object(target, attr, value, create=True)
			p.start()
			self.patches.append(p)
		self.db = db
		return self

	def __exit__(self, *exc):
		for p in reversed(self.patches):
			p.stop()
		for key, module in self.saved_modules.items():
			if module is None:
				sys.modules.pop(key, None)
			else:
				sys.modules[key] = module


class Doc(frappe._dict):
	"""Just enough of a Document: attribute access, get(), and set()."""

	def set(self, key, value):
		self[key] = value


def report(name="Item Prices", **values):
	base = dict(doctype="Report", report_name=name, name=None)
	base.update(values)
	return Doc(base)


class TestNumberingANewReport(FrappeTestCase):
	def test_a_new_report_gets_the_next_number_and_it_is_registered(self):
		with Register() as reg:
			doc = report("Alpha")
			rn.number_new_report(doc)
		self.assertEqual(doc[rn.FIELD], "RPT-0001")
		self.assertEqual(reg.held, {"Alpha": "RPT-0001"})

	def test_numbers_run_on_one_after_another(self):
		with Register() as reg:
			numbers = []
			for name in ("A", "B", "C"):
				doc = report(name)
				rn.number_new_report(doc)
				numbers.append(doc[rn.FIELD])
		self.assertEqual(numbers, ["RPT-0001", "RPT-0002", "RPT-0003"])

	def test_a_report_that_comes_back_under_its_name_gets_its_old_number(self):
		# bench migrate deletes a standard report whose JSON changed and inserts it again
		with Register(held={"Import Purchases Without LCV": "RPT-0042"}, series_start=300) as reg:
			doc = report("Import Purchases Without LCV")
			rn.number_new_report(doc)
		self.assertEqual(doc[rn.FIELD], "RPT-0042")
		self.assertEqual(reg.series, 300, "no new number was used up")
		self.assertEqual(reg.inserted, [])

	def test_a_number_is_never_handed_out_twice_even_if_the_series_lags(self):
		with Register(held={"Old": "RPT-0001", "Older": "RPT-0002"}, series_start=0) as reg:
			doc = report("New")
			rn.number_new_report(doc)
		self.assertEqual(doc[rn.FIELD], "RPT-0003")

	def test_a_report_that_already_has_a_number_keeps_it(self):
		with Register() as reg:
			doc = report("Alpha", **{rn.FIELD: "RPT-0777"})
			rn.number_new_report(doc)
		self.assertEqual(doc[rn.FIELD], "RPT-0777")
		self.assertEqual(reg.held, {})

	def test_nothing_happens_before_the_column_and_the_register_exist(self):
		# the first migrate after this lands syncs reports before after_migrate has made the column
		with Register(ready=False) as reg:
			doc = report("Alpha")
			rn.number_new_report(doc)
		self.assertNotIn(rn.FIELD, doc)
		self.assertEqual(reg.held, {})

	def test_other_doctypes_are_left_alone(self):
		with Register() as reg:
			doc = Doc(doctype="Account", report_name="x")
			rn.number_new_report(doc)
		self.assertNotIn(rn.FIELD, doc)

	def test_a_failure_is_logged_and_the_report_is_made_without_a_number(self):
		with Register() as reg:
			with patch.object(rn, "number_for", side_effect=RuntimeError("boom")):
				doc = report("Alpha")
				rn.number_new_report(doc)
		self.assertNotIn(rn.FIELD, doc)
		self.assertEqual(len(reg.log), 1)


class TestNumberingTheReportsThatExist(FrappeTestCase):
	def run_backfill(self, reg, todo):
		reg.db.set_value = MagicMock()
		with patch.object(rn.frappe, "get_all", MagicMock(return_value=[frappe._dict(name=n) for n in todo]), create=True) as get_all:
			count = rn.number_existing_reports()
		return count, get_all

	def test_every_report_without_a_number_gets_one_oldest_first(self):
		with Register() as reg:
			count, get_all = self.run_backfill(reg, ["Oldest", "Middle", "Newest"])
		self.assertEqual(count, 3)
		self.assertEqual(get_all.call_args.kwargs["order_by"], "creation asc, name asc")
		self.assertEqual(get_all.call_args.kwargs["filters"], {rn.FIELD: ["is", "not set"]})
		written = [(c.args[1], c.args[3]) for c in reg.db.set_value.call_args_list]
		self.assertEqual(written, [("Oldest", "RPT-0001"), ("Middle", "RPT-0002"), ("Newest", "RPT-0003")])

	def test_it_does_not_touch_the_modified_stamp(self):
		with Register() as reg:
			self.run_backfill(reg, ["A"])
		self.assertIs(reg.db.set_value.call_args.kwargs["update_modified"], False)

	def test_a_report_the_register_already_knows_gets_its_registered_number(self):
		with Register(held={"Middle": "RPT-0099"}, series_start=99) as reg:
			self.run_backfill(reg, ["Middle", "New"])
		written = dict((c.args[1], c.args[3]) for c in reg.db.set_value.call_args_list)
		self.assertEqual(written, {"Middle": "RPT-0099", "New": "RPT-0100"})

	def test_it_is_a_no_op_before_the_column_exists(self):
		with Register(ready=False) as reg:
			count, get_all = self.run_backfill(reg, ["A"])
		self.assertEqual(count, 0)
		get_all.assert_not_called()


class TestARenameKeepsTheNumber(FrappeTestCase):
	def test_the_number_moves_to_the_new_name(self):
		with Register(held={"Old Name": "RPT-0005"}) as reg:
			rn.follow_rename(report("New Name"), None, "Old Name", "New Name", False)
		self.assertEqual(reg.renamed, [("Old Name", "New Name")])
		self.assertEqual(reg.held, {"New Name": "RPT-0005"})

	def test_a_merge_leaves_the_survivor_its_own_number(self):
		with Register(held={"Old Name": "RPT-0005", "New Name": "RPT-0006"}) as reg:
			rn.follow_rename(report("New Name"), None, "Old Name", "New Name", True)
		self.assertEqual(reg.held, {"New Name": "RPT-0006"})
		self.assertEqual(reg.renamed, [])

	def test_a_rename_failure_is_logged_not_raised(self):
		with Register(held={"Old Name": "RPT-0005"}) as reg:
			with patch.object(rn.frappe, "delete_doc", side_effect=RuntimeError("boom")):
				rn.follow_rename(report("New Name"), None, "Old Name", "New Name", True)
		self.assertEqual(len(reg.log), 1)

	def test_the_rename_goes_through_the_model_function_that_takes_ignore_permissions(self):
		# frappe.rename_doc is a thin wrapper without ignore_permissions: calling it raises TypeError on a live site
		code = read("report_numbering.py")
		self.assertNotIn("frappe.rename_doc(", code)
		self.assertIn("from frappe.model.rename_doc import rename_doc", code)

	def test_a_report_with_no_number_yet_is_left_alone(self):
		with Register() as reg:
			rn.follow_rename(report("New Name"), None, "Old Name", "New Name", False)
		self.assertEqual(reg.renamed, [])

	def test_a_register_row_already_under_the_new_name_is_not_overwritten(self):
		with Register(held={"Old Name": "RPT-0005", "New Name": "RPT-0006"}) as reg:
			rn.follow_rename(report("New Name"), None, "Old Name", "New Name", False)
		self.assertEqual(reg.held, {"New Name": "RPT-0006"})


class TestWhatTheSystemShips(FrappeTestCase):
	"""The wiring is part of the feature: a hook dropped from hooks.py takes it away without a test of the logic failing."""

	def test_the_hooks_are_registered(self):
		text = read("hooks.py")
		block = text[text.index('\t"Report": {'):]
		block = block[: block.index("},") + 2]
		self.assertIn("sf_trading.report_numbering.number_new_report", block)
		self.assertIn("sf_trading.report_numbering.follow_rename", block)
		self.assertIn("sf_trading.report_numbering.ensure_custom_fields", text)

	def test_every_hook_named_exists(self):
		import ast

		defined = {n.name for n in ast.parse(read("report_numbering.py")).body if isinstance(n, ast.FunctionDef)}
		for name in re.findall(r"sf_trading\.report_numbering\.(\w+)", read("hooks.py")):
			self.assertIn(name, defined)

	def test_the_column_is_shown_in_the_list_and_offered_as_a_filter_and_cannot_repeat(self):
		captured = {}

		def grab(fields, **k):
			captured.update(fields)

		with patch.object(rn, "create_custom_fields", side_effect=grab), patch.object(rn, "number_existing_reports") as backfill:
			rn.ensure_custom_fields()
		backfill.assert_called_once()
		field = captured["Report"][0]
		self.assertEqual(field["fieldname"], "custom_report_no")
		for flag in ("in_list_view", "in_standard_filter", "unique", "read_only", "no_copy"):
			self.assertEqual(field[flag], 1, flag)

	def test_the_register_doctype_is_named_after_the_report_and_numbers_cannot_repeat(self):
		doctype = json.loads(read("sf_trading", "doctype", "sf_report_number", "sf_report_number.json"))
		self.assertEqual(doctype["name"], rn.REGISTER)
		self.assertEqual(doctype["autoname"], "field:report")
		self.assertEqual(doctype["allow_rename"], 1)
		fields = {f["fieldname"]: f for f in doctype["fields"]}
		self.assertEqual(fields["report"]["unique"], 1)
		self.assertEqual(fields["report_no"]["unique"], 1)
