# sf_trading/tests/test_alternative_cost_center.py
"""Alternative Cost Center (sf_trading/alternative_cost_center.py).

The database is patched at the module's own seams, so nothing is created or read:

    bench --site <scratch-site> run-tests --module sf_trading.tests.test_alternative_cost_center
"""

import json
import os
import shutil
import subprocess
import sys
import types
from unittest.mock import MagicMock, patch

import frappe
from frappe.tests.utils import FrappeTestCase

from sf_trading import alternative_cost_center as acc

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OWN, OTHER = "Steel Force Trading WLL", "STEEL FORCE INTERNATIONAL SPC"


def read(*parts):
	with open(os.path.join(ROOT, *parts), encoding="utf-8") as f:
		return f.read()


class Doc(frappe._dict):
	def get_doc_before_save(self):
		return None


def cc(name, company, is_group=0, disabled=0):
	return frappe._dict(name=name, doctype="Cost Center", cost_center_name=name.split(" - ")[0], company=company, is_group=is_group, disabled=disabled)


class Fields:
	"""Which doctypes have the alternative cost centre field, and which tables they have."""

	def __init__(self, header=("Purchase Invoice", "POS Invoice"), tables=None):
		self.header = set(header)
		self.tables = tables if tables is not None else {
			"Journal Entry": [("accounts", "Journal Entry Account", "Accounting Entries")],
			"Purchase Invoice": [("items", "Purchase Invoice Item", "Items")],
			"POS Invoice": [("items", "POS Invoice Item", "Items")],
		}

	def meta(self, doctype):
		tables = self.tables.get(doctype, [])
		fields = {acc.FIELD} if doctype in self.header or doctype in {c for _f, c, _l in sum(self.tables.values(), [])} else set()
		return types.SimpleNamespace(
			has_field=lambda name: name in fields,
			get_table_fields=lambda: [types.SimpleNamespace(fieldname=f, options=c, label=l) for f, c, l in tables],
		)


class Seams:
	def __init__(self, mirrors=(), fields=None, carriers=("Journal Entry", "Purchase Invoice", "POS Invoice")):
		self.mirrors = {m["name"]: m for m in mirrors}
		self.fields = fields or Fields()
		self.carriers = list(carriers)
		self.patches = []

	def __enter__(self):
		for target, attr, value in [
			(acc, "carriers", MagicMock(return_value=self.carriers)),
			(acc.frappe, "get_meta", MagicMock(side_effect=self.fields.meta)),
			(acc.frappe, "get_all", MagicMock(side_effect=lambda doctype, **k: [frappe._dict(m) for n, m in self.mirrors.items() if n in k["filters"]["name"][1]])),
		]:
			p = patch.object(target, attr, value, create=True)
			p.start()
			self.patches.append(p)
		return self

	def __exit__(self, *exc):
		for p in reversed(self.patches):
			p.stop()


def mirror(name, company, disabled=0):
	return {"name": name, "cc_company": company, "disabled": disabled}


def voucher(doctype="Journal Entry", company=OWN, header=None, **tables):
	doc = Doc(doctype=doctype, company=company, **tables)
	if header:
		doc[acc.FIELD] = header
	return doc


def row(idx, alt):
	return frappe._dict(idx=idx, **{acc.FIELD: alt})


class TestWhatAVoucherMaySay(FrappeTestCase):
	def refused(self, doc, text, mirrors):
		with Seams(mirrors), self.assertRaises(frappe.ValidationError) as caught:
			acc.validate_voucher(doc)
		self.assertIn(text, str(caught.exception))

	def test_a_cost_centre_of_another_company_on_a_row_is_accepted(self):
		doc = voucher(accounts=[row(1, "Main - SFTO")])
		with Seams([mirror("Main - SFTO", OTHER)]):
			acc.validate_voucher(doc)

	def test_a_header_value_and_row_values_are_all_checked(self):
		doc = voucher("Purchase Invoice", header="Main - SFTO", items=[row(1, "WH - SFTO"), row(2, "")])
		with Seams([mirror("Main - SFTO", OTHER), mirror("WH - SFTO", OTHER)]):
			acc.validate_voucher(doc)

	def test_the_vouchers_own_company_is_refused_and_says_to_use_the_ordinary_cost_centre(self):
		self.refused(voucher(accounts=[row(1, "Main - SFB")]), "ordinary Cost Center", [mirror("Main - SFB", OWN)])

	def test_the_refusal_names_the_row(self):
		self.refused(voucher(accounts=[row(1, "Main - SFTO"), row(2, "Main - SFB")]), "Row 2", [mirror("Main - SFTO", OTHER), mirror("Main - SFB", OWN)])

	def test_a_disabled_one_or_a_group_is_refused(self):
		self.refused(voucher(accounts=[row(1, "Old - SFTO")]), "disabled or a group", [mirror("Old - SFTO", OTHER, disabled=1)])

	def test_a_name_that_is_not_a_mirror_is_refused(self):
		self.refused(voucher(accounts=[row(1, "Nothing - XX")]), "is not an Alternative Cost Center", [])

	def test_a_voucher_naming_none_costs_nothing(self):
		with Seams([]), patch.object(acc.frappe, "get_all", MagicMock(side_effect=AssertionError("names none: nothing to look up")), create=True):
			acc.validate_voucher(voucher(accounts=[row(1, ""), row(2, None)]))

	def test_a_document_that_is_not_a_voucher_is_left_alone_without_a_query(self):
		with patch.object(acc, "carriers", return_value=["Journal Entry"]), \
				patch.object(acc.frappe, "get_meta", MagicMock(side_effect=AssertionError("not a carrier")), create=True):
			acc.validate_voucher(Doc(doctype="Customer", company=OWN))
			acc.validate_voucher(Doc(doctype="Journal Entry", company=None))

	def test_a_doctype_without_the_field_is_not_checked(self):
		with Seams([], Fields(header=(), tables={})):
			acc.validate_voucher(voucher("Sales Invoice"))

	def test_a_document_type_that_carries_the_field_is_checked_whatever_it_is(self):
		# not only the first eight vouchers: a POS Invoice is refused its own company's cost centre too
		doc = Doc(doctype="POS Invoice", company=OWN, items=[row(1, "Main - SFB")])
		with Seams([mirror("Main - SFB", OWN)]), self.assertRaises(frappe.ValidationError) as caught:
			acc.validate_voucher(doc)
		self.assertIn("Row 1 of Items", str(caught.exception))
		ok = Doc(doctype="POS Invoice", company=OWN, items=[row(1, "Main - SFTO")])
		with Seams([mirror("Main - SFTO", OTHER)]):
			acc.validate_voucher(ok)

	def test_a_document_type_that_does_not_carry_the_field_costs_nothing(self):
		with Seams(carriers=("Journal Entry",)), patch.object(acc.frappe, "get_all", MagicMock(side_effect=AssertionError("not a carrier")), create=True):
			acc.validate_voucher(Doc(doctype="Customer", company=OWN))
			acc.validate_voucher(Doc(doctype="Purchase Invoice", company=OWN, items=[row(1, "Main - SFB")]))

	def test_a_document_with_no_company_is_not_checked_there_is_nothing_to_compare_with(self):
		with Seams(), patch.object(acc.frappe, "get_all", MagicMock(side_effect=AssertionError("no company")), create=True):
			acc.validate_voucher(Doc(doctype="POS Invoice", company=None, items=[row(1, "Main - SFB")]))


class TestTheMirror(FrappeTestCase):
	def seams(self, existing=()):
		db = MagicMock()
		db.table_exists.return_value = True
		db.exists.side_effect = lambda doctype, name=None: name in existing
		inserted = []

		def get_doc(values):
			doc = MagicMock()
			doc.insert.side_effect = lambda **k: inserted.append(values)
			return doc

		return db, inserted, get_doc

	def test_a_new_cost_centre_gets_a_mirror(self):
		db, inserted, get_doc = self.seams()
		with patch.object(acc.frappe, "db", db, create=True), patch.object(acc.frappe, "get_doc", side_effect=get_doc, create=True):
			acc.sync_cost_center(cc("Main - SFTO", OTHER))
		self.assertEqual(inserted, [{"doctype": "Alternative Cost Center", "cost_center": "Main - SFTO", "cost_center_name": "Main", "cc_company": OTHER, "disabled": 0}])

	def test_a_group_or_a_disabled_cost_centre_is_mirrored_as_disabled(self):
		db, inserted, get_doc = self.seams()
		with patch.object(acc.frappe, "db", db, create=True), patch.object(acc.frappe, "get_doc", side_effect=get_doc, create=True):
			acc.sync_cost_center(cc("Group - SFTO", OTHER, is_group=1))
			acc.sync_cost_center(cc("Shut - SFTO", OTHER, disabled=1))
		self.assertEqual([i["disabled"] for i in inserted], [1, 1])

	def test_a_change_updates_the_mirror_without_touching_its_modified_stamp(self):
		db, inserted, get_doc = self.seams(existing=("Main - SFTO",))
		with patch.object(acc.frappe, "db", db, create=True), patch.object(acc.frappe, "get_doc", side_effect=get_doc, create=True):
			acc.sync_cost_center(cc("Main - SFTO", OTHER, disabled=1))
		self.assertEqual(inserted, [])
		args, kwargs = db.set_value.call_args
		self.assertEqual((args[0], args[1], args[2]["disabled"]), ("Alternative Cost Center", "Main - SFTO", 1))
		self.assertIs(kwargs["update_modified"], False)

	def test_other_doctypes_and_a_site_without_the_table_are_left_alone(self):
		db, inserted, get_doc = self.seams()
		db.table_exists.return_value = False
		with patch.object(acc.frappe, "db", db, create=True), patch.object(acc.frappe, "get_doc", side_effect=get_doc, create=True):
			acc.sync_cost_center(cc("Main - SFTO", OTHER))
			acc.sync_cost_center(Doc(doctype="Account", name="x"))
		self.assertEqual(inserted, [])

	def test_deleting_a_cost_centre_deletes_its_mirror_or_disables_it_if_a_voucher_names_it(self):
		db, _i, _g = self.seams(existing=("Main - SFTO",))
		delete = MagicMock()
		with patch.object(acc.frappe, "db", db, create=True), patch.object(acc.frappe, "delete_doc", delete, create=True):
			acc.drop_cost_center(cc("Main - SFTO", OTHER))
		delete.assert_called_once()
		db.reset_mock()
		db.table_exists.return_value = True
		db.exists.side_effect = lambda doctype, name=None: True
		with patch.object(acc.frappe, "db", db, create=True), patch.object(acc.frappe, "delete_doc", MagicMock(side_effect=frappe.LinkExistsError), create=True):
			acc.drop_cost_center(cc("Main - SFTO", OTHER))
		self.assertEqual(db.set_value.call_args.args[1:4], ("Main - SFTO", "disabled", 1))

	def test_a_rename_renames_the_mirror_and_not_on_a_merge_or_a_clash(self):
		renamed = []
		fake = types.ModuleType("frappe.model.rename_doc")
		fake.rename_doc = lambda doctype, old, new, force=False, ignore_permissions=False, show_alert=True: renamed.append((old, new))
		saved = sys.modules.get("frappe.model.rename_doc")
		sys.modules["frappe.model.rename_doc"] = fake
		try:
			db, _i, _g = self.seams(existing=("Old - SFTO",))
			with patch.object(acc.frappe, "db", db, create=True):
				acc.follow_rename(cc("New - SFTO", OTHER), None, "Old - SFTO", "New - SFTO", False)
				acc.follow_rename(cc("New - SFTO", OTHER), None, "Old - SFTO", "New - SFTO", True)
			self.assertEqual(renamed, [("Old - SFTO", "New - SFTO")])
		finally:
			if saved is None:
				sys.modules.pop("frappe.model.rename_doc", None)
			else:
				sys.modules["frappe.model.rename_doc"] = saved

	def test_the_backfill_makes_the_missing_and_brings_the_stale_up_to_date_and_counts_them(self):
		inserted = []
		costs = [cc("A - SFTO", OTHER), cc("B - SFTO", OTHER, is_group=1), cc("C - SFB", OWN)]
		mirrors = [frappe._dict(name="A - SFTO", cost_center_name="A", cc_company=OTHER, disabled=0), frappe._dict(name="B - SFTO", cost_center_name="B", cc_company=OTHER, disabled=0)]
		db = MagicMock()
		db.table_exists.return_value = True

		def get_all(doctype, **k):
			return mirrors if doctype == "Alternative Cost Center" else costs

		def get_doc(values):
			doc = MagicMock()
			doc.insert.side_effect = lambda **k: inserted.append(values["cost_center"])
			return doc

		with patch.object(acc.frappe, "db", db, create=True), patch.object(acc.frappe, "get_all", side_effect=get_all, create=True), \
				patch.object(acc.frappe, "get_doc", side_effect=get_doc, create=True):
			touched = acc.backfill()
		self.assertEqual(inserted, ["C - SFB"])
		self.assertEqual(touched, 2, "C is made and B (now a group) is brought up to date; A is already right")
		self.assertEqual(db.set_value.call_args.args[1], "B - SFTO")


class TestTheDimension(FrappeTestCase):
	def run_ensure(self, existing_dimension):
		self.cache = MagicMock()
		made = []
		module = types.ModuleType("erpnext.accounts.doctype.accounting_dimension.accounting_dimension")
		module.make_dimension_in_accounting_doctypes = lambda doc: made.append(doc)
		chain = ["erpnext", "erpnext.accounts", "erpnext.accounts.doctype", "erpnext.accounts.doctype.accounting_dimension"]
		saved = {n: sys.modules.get(n) for n in chain + [module.__name__]}
		for name in chain:
			sys.modules.setdefault(name, types.ModuleType(name))
		sys.modules[module.__name__] = module
		db = MagicMock()
		db.table_exists.return_value = True
		db.get_value.return_value = "Alternative Cost Center" if existing_dimension else None
		db.has_column.return_value = True
		doc = MagicMock()
		try:
			with patch.object(acc.frappe, "db", db, create=True), patch.object(acc, "backfill", return_value=0) as backfill, \
					patch.object(acc.frappe, "get_doc", MagicMock(return_value=doc), create=True) as get_doc, patch.object(acc.frappe, "flags", types.SimpleNamespace(), create=True), \
					patch.object(acc.frappe, "cache", self.cache, create=True), patch.object(acc.frappe, "local", types.SimpleNamespace(), create=True):
				acc.ensure_dimension()
		finally:
			for name, old in saved.items():
				if old is None:
					sys.modules.pop(name, None)
				else:
					sys.modules[name] = old
		return made, db, doc, backfill, get_doc

	def test_a_fresh_site_gets_the_dimension_made_now_and_the_ledger_column_indexed(self):
		made, db, doc, backfill, get_doc = self.run_ensure(False)
		backfill.assert_called_once()
		self.assertEqual(get_doc.call_args.args[0], {"doctype": "Accounting Dimension", "document_type": "Alternative Cost Center", "label": "Alternative Cost Center", "fieldname": "alternative_cost_center"})
		doc.insert.assert_called_once()
		self.assertEqual(made, [doc], "ERPNext's own field maker runs now, not in a worker after the commit")
		db.add_index.assert_called_once_with("GL Entry", ["alternative_cost_center"])

	def test_making_the_dimension_forgets_the_old_list_of_carriers(self):
		self.run_ensure(False)
		self.cache.delete_value.assert_called_with(acc.CARRIERS_KEY)

	def test_a_site_that_has_it_makes_nothing_twice_but_still_makes_sure_of_the_fields(self):
		made, db, doc, _b, get_doc = self.run_ensure(True)
		doc.insert.assert_not_called()
		self.assertEqual(get_doc.call_args.args[:2], ("Accounting Dimension", "Alternative Cost Center"))
		self.assertEqual(made, [doc])

	def test_the_first_versions_company_column_is_dropped_once_and_only_when_it_is_there(self):
		_made, db, _doc, _b, _g = self.run_ensure(True)
		db.has_column.side_effect = lambda doctype, column: column == "company"
		_made, db2, _doc2, _b2, _g2 = self.run_ensure_with(db)
		self.assertIn("drop column `company`", db2.sql_ddl.call_args.args[0])

	def run_ensure_with(self, db):
		made = []
		module = types.ModuleType("erpnext.accounts.doctype.accounting_dimension.accounting_dimension")
		module.make_dimension_in_accounting_doctypes = lambda doc: made.append(doc)
		chain = ["erpnext", "erpnext.accounts", "erpnext.accounts.doctype", "erpnext.accounts.doctype.accounting_dimension"]
		saved = {n: sys.modules.get(n) for n in chain + [module.__name__]}
		for name in chain:
			sys.modules.setdefault(name, types.ModuleType(name))
		sys.modules[module.__name__] = module
		db.table_exists.return_value = True
		db.get_value.return_value = "Alternative Cost Center"
		try:
			with patch.object(acc.frappe, "db", db, create=True), patch.object(acc, "backfill", return_value=0), \
					patch.object(acc.frappe, "get_doc", MagicMock(return_value=MagicMock()), create=True), patch.object(acc.frappe, "flags", types.SimpleNamespace(), create=True), \
					patch.object(acc.frappe, "cache", MagicMock(), create=True), patch.object(acc.frappe, "local", types.SimpleNamespace(), create=True):
				acc.ensure_dimension()
		finally:
			for name, old in saved.items():
				if old is None:
					sys.modules.pop(name, None)
				else:
					sys.modules[name] = old
		return made, db, None, None, None

	def test_a_site_without_the_mirror_table_does_nothing(self):
		db = MagicMock()
		db.table_exists.return_value = False
		with patch.object(acc.frappe, "db", db, create=True), patch.object(acc, "backfill", side_effect=AssertionError("no table")):
			acc.ensure_dimension()


JS_RUNNER = r"""
const fs = require("fs"), vm = require("vm");
const queries = [];
const sandbox = { console };
const registered = {};
sandbox.frappe = {
	provide(path) { path.split(".").reduce((o, k) => (o[k] = o[k] || {}, o[k]), sandbox); },
	ui: { form: { on: (dt, handlers) => (registered[dt] = handlers) } },
	get_meta: (dt) => ({ fields: [{ fieldname: "alternative_cost_center" }] }),
};
const boot = JSON.parse(process.argv[3]);
if (boot !== null) sandbox.frappe.boot = { sf_alt_cc_doctypes: boot };
else sandbox.frappe.boot = {};
sandbox.sf_trading = {};
vm.createContext(sandbox);
vm.runInContext(fs.readFileSync(process.argv[2], "utf8"), sandbox);
const make = (company, fields) => ({
	doc: { company },
	meta: { fields },
	set_query(...args) { queries.push(args.length === 3 ? { field: args[0], table: args[1], filters: args[2]().filters } : { field: args[0], filters: args[1]().filters }); },
});
const header_and_table = make("Steel Force Trading WLL", [{ fieldname: "alternative_cost_center" }, { fieldname: "accounts", fieldtype: "Table", options: "Journal Entry Account" }, { fieldname: "remarks", fieldtype: "Text" }]);
const taxes = make("Steel Force Trading WLL", [{ fieldname: "taxes", fieldtype: "Table", options: "Sales Taxes and Charges" }]);
const no_company = make("", [{ fieldname: "alternative_cost_center" }]);
const nothing = { doc: { company: "X" }, meta: { fields: [{ fieldname: "remarks" }] }, set_query() { throw new Error("no field, no query"); } };
const first = Object.keys(registered)[0];
registered[first].setup(header_and_table);
const afterFirst = queries.splice(0);
registered[first].setup(taxes);
const afterTaxes = queries.splice(0);
registered[first].setup(no_company);
const afterNoCompany = queries.splice(0);
registered[first].setup(nothing);
console.log(JSON.stringify({ doctypes: Object.keys(registered), header_and_table: afterFirst, taxes: afterTaxes, no_company: afterNoCompany }));
"""


class TestTheForm(FrappeTestCase):
	def run_js(self, boot):
		node = shutil.which("node")
		if not node:
			self.skipTest("node is not installed")
		done = subprocess.run([node, "-e", JS_RUNNER, "x", os.path.join(ROOT, "public", "js", "alternative_cost_center.js"), json.dumps(boot)],
			capture_output=True, text=True, encoding="utf-8", timeout=60)
		self.assertEqual(done.returncode, 0, done.stderr)
		return json.loads(done.stdout)

	def test_the_forms_are_the_ones_the_boot_names_every_one_of_them(self):
		boot = ["Journal Entry", "POS Invoice", "Landed Cost Voucher", "Sales Order", "Sales Taxes and Charges Template", "Asset Repair"]
		self.assertEqual(self.run_js(boot)["doctypes"], boot)

	def test_a_stale_boot_falls_back_to_the_first_eight_vouchers(self):
		for stale in (None, []):
			out = self.run_js(stale)
			self.assertEqual(sorted(out["doctypes"]), sorted(["Journal Entry", "Sales Invoice", "Purchase Invoice", "Payment Entry", "Expense Claim", "Stock Entry", "Purchase Receipt", "Delivery Note"]))

	def test_the_field_is_offered_only_cost_centres_of_other_enabled_companies_on_the_header_and_the_rows(self):
		out = self.run_js(["Journal Entry"])
		expected = {"cc_company": ["!=", "Steel Force Trading WLL"], "disabled": 0}
		self.assertEqual(out["header_and_table"], [{"field": "alternative_cost_center", "filters": expected},
			{"field": "alternative_cost_center", "table": "accounts", "filters": expected}])

	def test_a_tax_table_of_a_document_is_filtered_too(self):
		out = self.run_js(["Sales Invoice"])
		self.assertEqual(out["taxes"], [{"field": "alternative_cost_center", "table": "taxes", "filters": {"cc_company": ["!=", "Steel Force Trading WLL"], "disabled": 0}}])

	def test_a_form_with_no_company_yet_is_offered_every_enabled_cost_centre(self):
		out = self.run_js(["Journal Entry"])
		self.assertEqual(out["no_company"][0]["filters"], {"cc_company": ["!=", ""], "disabled": 0})


class TestWhichDocumentTypesCarryIt(FrappeTestCase):
	def run_carriers(self, have, istable, parents_by_doctype):
		"""have: Custom Field dts; istable: {dt: 0/1}; parents_by_doctype: {"DocField": [parent...], "Custom Field": [dt...]}."""
		def get_all(doctype, filters=None, fields=None, pluck=None, **k):
			if doctype == "Custom Field" and pluck:
				return list(have)
			if doctype == "DocType":
				return [frappe._dict(name=n, istable=istable[n]) for n in filters["name"][1] if n in istable]
			if doctype in parents_by_doctype:
				return [frappe._dict({fields[0]: p}) for p in parents_by_doctype[doctype]]
			return []
		with patch.object(acc.frappe, "get_all", side_effect=get_all, create=True):
			return acc._carriers()

	def test_headers_and_the_parents_of_carrying_tables_are_in_and_the_ledgers_are_out(self):
		have = ["Sales Invoice", "Sales Invoice Item", "Journal Entry Account", "GL Entry", "Payment Ledger Entry", "Account Closing Balance", "Budget", "Sales Taxes and Charges"]
		istable = {"Sales Invoice": 0, "Sales Invoice Item": 1, "Journal Entry Account": 1, "GL Entry": 0, "Payment Ledger Entry": 0, "Account Closing Balance": 0, "Budget": 0, "Sales Taxes and Charges": 1}
		parents = {"DocField": ["Sales Invoice", "Journal Entry", "Quotation", "Sales Taxes and Charges Template"], "Custom Field": ["Sales Invoice"]}
		self.assertEqual(self.run_carriers(have, istable, parents),
			["Budget", "Journal Entry", "Quotation", "Sales Invoice", "Sales Taxes and Charges Template"])

	def test_a_site_with_no_field_has_no_carriers_and_asks_nothing_more(self):
		with patch.object(acc.frappe, "get_all", MagicMock(return_value=[]), create=True) as get_all:
			self.assertEqual(acc._carriers(), [])
		self.assertEqual(get_all.call_count, 1)

	def test_the_list_is_cached_in_redis_and_in_the_request(self):
		cache = MagicMock()
		cache.get_value.return_value = None
		local = types.SimpleNamespace()
		with patch.object(acc.frappe, "cache", cache, create=True), patch.object(acc.frappe, "local", local, create=True), patch.object(acc, "_carriers", return_value=["A", "B"]) as compute:
			self.assertEqual(acc.carriers(), ["A", "B"])
			self.assertEqual(acc.carriers(), ["A", "B"])
		self.assertEqual(compute.call_count, 1)
		cache.set_value.assert_called_once_with(acc.CARRIERS_KEY, ["A", "B"])

	def test_a_list_already_in_redis_is_used_without_looking_at_the_tables(self):
		cache = MagicMock()
		cache.get_value.return_value = ["X"]
		with patch.object(acc.frappe, "cache", cache, create=True), patch.object(acc.frappe, "local", types.SimpleNamespace(), create=True), \
				patch.object(acc, "_carriers", side_effect=AssertionError("cached")):
			self.assertEqual(acc.carriers(), ["X"])

	def test_the_boot_names_the_carriers_and_a_site_without_the_master_names_none(self):
		boot = {}
		db = MagicMock()
		db.table_exists.return_value = True
		with patch.object(acc.frappe, "db", db, create=True), patch.object(acc, "carriers", return_value=["A", "B"]):
			acc.boot_carriers(boot)
		self.assertEqual(boot, {"sf_alt_cc_doctypes": ["A", "B"]})
		db.table_exists.return_value = False
		empty = {}
		with patch.object(acc.frappe, "db", db, create=True), patch.object(acc, "carriers", side_effect=AssertionError("no master")):
			acc.boot_carriers(empty)
		self.assertEqual(empty, {})


class TestWhatTheSystemShips(FrappeTestCase):
	def test_the_hooks_are_registered(self):
		text = read("hooks.py")
		self.assertIn('"validate": "sf_trading.alternative_cost_center.validate_voucher"', text)
		for hook in ("sync_cost_center", "drop_cost_center", "follow_rename", "ensure_dimension"):
			self.assertIn("sf_trading.alternative_cost_center." + hook, text)
		self.assertIn("/assets/sf_trading/js/alternative_cost_center.js", text)
		self.assertIn("sf_trading.alternative_cost_center.boot_carriers", text[text.index("boot_session"):])

	def test_the_wildcard_entry_of_doc_events_exists_once_and_holds_the_validation(self):
		# a second "*" key in the same dict literal silently replaces the first: the validation was lost that way on sft-uat
		text = read("hooks.py")
		self.assertEqual(text.count('	"*": {'), 1)
		block = text[text.index('	"*": {'):]
		block = block[: block.index("},")]
		self.assertIn("sf_trading.alternative_cost_center.validate_voucher", block)
		self.assertIn("sf_trading.supporting_documents.remind_on_submit", block)

	def test_every_hook_named_exists(self):
		import ast
		import re

		defined = {n.name for n in ast.parse(read("alternative_cost_center.py")).body if isinstance(n, ast.FunctionDef)}
		for name in re.findall(r"sf_trading\.alternative_cost_center\.(\w+)", read("hooks.py")):
			self.assertIn(name, defined)

	def test_the_master_is_named_after_the_cost_centre_and_ignores_user_permissions_so_other_companies_can_be_chosen(self):
		doctype = json.loads(read("sf_trading", "doctype", "alternative_cost_center", "alternative_cost_center.json"))
		self.assertEqual((doctype["name"], doctype["autoname"]), ("Alternative Cost Center", "field:cost_center"))
		fields = {f["fieldname"]: f for f in doctype["fields"]}
		self.assertEqual(fields["cost_center"]["unique"], 1)
		self.assertEqual(fields["cc_company"]["ignore_user_permissions"], 1)
		self.assertNotIn("company", fields, "ERPNext refuses a dimension value of another company when the dimension has a field called company")
		self.assertEqual(fields["cost_center"]["ignore_user_permissions"], 1)
		roles = {p["role"] for p in doctype["permissions"]}
		self.assertTrue({"System Manager", "Accounts Manager", "Accounts User", "Purchase User", "Sales User"} <= roles)

	def test_the_dimension_fieldname_in_code_is_the_one_the_reports_read(self):
		from sf_trading import mis_profit_loss as mis

		self.assertEqual(acc.FIELD, "alternative_cost_center")
		self.assertEqual(mis.ALT_FIELD, acc.FIELD)
