# sf_trading/tests/test_report_numbering.py
"""Every Report carries a number (sf_trading/report_numbering.py).

The database is patched at the module's own seams, so nothing is created or read:

    bench --site <scratch-site> run-tests --module sf_trading.tests.test_report_numbering
"""

import json
import os
import re
import shutil
import subprocess
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


class TestTheBootCarriesTheNumbers(FrappeTestCase):
	def boot(self, **extra):
		return frappe._dict(user=frappe._dict(all_reports={
			"Stock Ledger": {"ref_doctype": "Stock Ledger Entry", "report_type": "Script Report"},
			"Mine": {"ref_doctype": "Item", "report_type": "Report Builder"},
			"Unnumbered": {"ref_doctype": "Item", "report_type": "Report Builder"},
		}), **extra)

	def test_each_report_the_user_may_open_carries_its_number(self):
		boot = self.boot()
		with Register() as reg, patch.object(rn.frappe, "get_all", MagicMock(return_value=[("Stock Ledger", "RPT-0007"), ("Mine", "RPT-0310"), ("Not Allowed", "RPT-0001")]), create=True) as get_all:
			rn.boot_numbers(boot)
		reports = boot.user.all_reports
		self.assertEqual(reports["Stock Ledger"]["report_no"], "RPT-0007")
		self.assertEqual(reports["Mine"]["report_no"], "RPT-0310")
		self.assertNotIn("report_no", reports["Unnumbered"])
		self.assertNotIn("Not Allowed", reports, "a report the user may not open is not added")
		self.assertEqual(get_all.call_args.kwargs["as_list"], True)

	def test_nothing_is_asked_before_the_column_exists_or_with_no_reports(self):
		boot = self.boot()
		with Register(ready=False), patch.object(rn.frappe, "get_all", MagicMock(side_effect=AssertionError("no column yet")), create=True):
			rn.boot_numbers(boot)
		with Register(), patch.object(rn.frappe, "get_all", MagicMock(side_effect=AssertionError("nothing to number")), create=True):
			rn.boot_numbers(frappe._dict(user=frappe._dict(all_reports={})))
			rn.boot_numbers(frappe._dict())

	def test_the_hook_and_the_script_are_registered(self):
		text = read("hooks.py")
		self.assertIn("sf_trading.report_numbering.boot_numbers", text[text.index("boot_session"):])
		self.assertIn("/assets/sf_trading/js/report_number_search.js", text)
		self.assertTrue(os.path.exists(os.path.join(ROOT, "public", "js", "report_number_search.js")))


JS_RUNNER = r"""
const fs = require("fs"), vm = require("vm");
const calls = [];
const sandbox = {
	__: (s, args) => (args || []).reduce((t, a, i) => t.replace("{" + i + "}", a), s),
	$: () => ({ on() {} }),
	document: {},
	console,
};
sandbox.frappe = {
	utils: { escape_html: (s) => String(s).replace(/</g, "&lt;") },
	boot: { user: { all_reports: JSON.parse(process.argv[3]) } },
	search: { utils: { get_reports: (kw) => (kw === "stock" || kw === "42" ? [{ type: "Report", index: 5, route: ["query-report", "Stock Ledger"], label: "by name" }] : []) } },
};
vm.createContext(sandbox);
vm.runInContext(fs.readFileSync(process.argv[2], "utf8"), sandbox);
const f = sandbox.frappe;
const out = { score: {}, results: {} };
for (const kw of JSON.parse(process.argv[4])) {
	out.score[kw] = f.sf_report_number_score(kw, "RPT-0042");
	out.results[kw] = f.search.utils.get_reports(kw).map((d) => ({ label: d.label, route: d.route, index: d.index }));
}
out.installedOnce = (() => { const before = f.search.utils.get_reports; f.sf_install_report_number_search(); return before === f.search.utils.get_reports; })();
console.log(JSON.stringify(out));
"""


class TestTheSearchBarFindsByNumber(FrappeTestCase):
	REPORTS = {
		"Stock Ledger": {"ref_doctype": "Stock Ledger Entry", "report_type": "Script Report", "report_no": "RPT-0042"},
		"Item Sheet": {"ref_doctype": "Item", "report_type": "Report Builder", "report_no": "RPT-0043"},
		"Other": {"ref_doctype": "Item", "report_type": "Script Report", "report_no": "RPT-0100"},
		"Plain": {"ref_doctype": "Item", "report_type": "Script Report"},
		"Four": {"ref_doctype": "Item", "report_type": "Script Report", "report_no": "RPT-0004"},
	}
	REPORTS.update({"Bulk %d" % n: {"ref_doctype": "Item", "report_type": "Script Report", "report_no": "RPT-%04d" % n} for n in range(201, 213)})
	TYPED = ["RPT-0042", "rpt 42", "rpt42", "42", "0042", "4", "04", "rpt-004", "004", "rpt-0", "rpt", "stock", "20020000049",
		"rpt-0042x", "", "Rpt-0100", "rpt-02", "020"]

	def run_js(self):
		node = shutil.which("node")
		if not node:
			self.skipTest("node is not installed")
		done = subprocess.run([node, "-e", JS_RUNNER, "x", os.path.join(ROOT, "public", "js", "report_number_search.js"),
			json.dumps(self.REPORTS), json.dumps(self.TYPED)], capture_output=True, text=True, timeout=60)
		self.assertEqual(done.returncode, 0, done.stderr)
		return json.loads(done.stdout)

	def test_a_number_is_matched_however_it_is_typed(self):
		out = self.run_js()
		for typed in ("RPT-0042", "rpt 42", "rpt42", "42", "0042"):
			self.assertEqual(out["score"][typed], 1e6, typed)

	def test_what_is_not_a_number_matches_nothing(self):
		out = self.run_js()
		for typed in ("4", "rpt", "stock", "20020000049", "rpt-0042x", "", "rpt-0"):
			self.assertEqual(out["score"][typed], 0, typed)

	def test_a_prefix_lists_the_neighbours_only_when_rpt_is_typed(self):
		out = self.run_js()
		self.assertEqual(out["score"]["rpt-004"], 1e3)
		routes = [r["route"] for r in out["results"]["rpt-004"]]
		# RPT-0004 is the exact number "004" names, then the forties follow
		self.assertEqual(routes, [["query-report", "Four"], ["query-report", "Stock Ledger"], ["List", "Item", "Report", "Item Sheet"]])
		self.assertEqual(out["results"]["rpt-0"], [])

	def test_the_report_is_offered_with_its_number_and_the_right_route(self):
		out = self.run_js()
		hit = out["results"]["RPT-0042"]
		self.assertEqual(len(hit), 1)
		self.assertEqual(hit[0]["route"], ["query-report", "Stock Ledger"])
		self.assertIn("<mark>RPT-0042</mark> Stock Ledger", hit[0]["label"])
		self.assertEqual(out["results"]["Rpt-0100"][0]["route"], ["query-report", "Other"])

	def test_a_report_found_by_name_and_by_number_is_listed_once(self):
		out = self.run_js()
		self.assertEqual([r["route"] for r in out["results"]["stock"]], [["query-report", "Stock Ledger"]])
		self.assertEqual(out["results"]["stock"][0]["label"], "by name", "a name match with no number typed is left as core made it")

	def test_a_report_with_no_number_is_never_offered_by_number(self):
		out = self.run_js()
		for typed, results in out["results"].items():
			self.assertNotIn(["query-report", "Plain"], [r["route"] for r in results], typed)

	def test_a_single_digit_or_digits_without_rpt_never_list_a_prefix(self):
		out = self.run_js()
		self.assertEqual(out["results"]["4"], [], "a bare 4 is too common to mean RPT-0004")
		self.assertEqual([r["route"] for r in out["results"]["04"]], [["query-report", "Four"]], "but two digits name it exactly")
		self.assertEqual([r["route"] for r in out["results"]["004"]], [["query-report", "Four"]], "digits alone are the exact number")
		self.assertEqual(out["results"]["020"], [], "no RPT typed: no prefix browsing of the 0201 to 0209")

	def test_a_prefix_lists_ten_at_most_in_number_order(self):
		out = self.run_js()
		names = [r["route"][1] for r in out["results"]["rpt-02"]]
		self.assertEqual(names, ["Bulk %d" % n for n in range(201, 211)])

	def test_a_report_found_by_name_and_number_is_listed_once_with_its_number(self):
		out = self.run_js()
		hits = [r for r in out["results"]["42"] if r["route"] == ["query-report", "Stock Ledger"]]
		self.assertEqual(len(hits), 1)
		self.assertIn("RPT-0042", hits[0]["label"])

	def test_it_is_installed_once(self):
		self.assertTrue(self.run_js()["installedOnce"])
