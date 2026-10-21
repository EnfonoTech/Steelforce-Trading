# sf_trading/tests/test_purchase_order_procurement.py
"""The Purchase Order procurement rules (sf_trading/purchase_order_procurement.py).

    bench --site <site> run-tests --module sf_trading.tests.test_purchase_order_procurement

No database: every lookup (the settings rows, Material Requests, Supplier Quotations, the amended order, the
competing quotations) is replaced, so what is tested is the rule itself.
"""

import importlib
import json
import os
import unittest
from unittest.mock import MagicMock, patch

import frappe

from sf_trading import hooks
from sf_trading import purchase_order_procurement as pop

COMPANY = "Test Trading WLL"
SUPPLIER = "Alpha Steel"


def settings(**overrides):
	"""The rules as the settings hold them: enforcement on, 500 / 3 / 2, a verbal order skips quotations."""
	values = dict(enforce=True, above=500.0, local_min=3, import_min=2, verbal_skips=True)
	values.update(overrides)
	return frappe._dict(values)


def line(idx, item="STEEL-1", mr="MR-1", sq="SQ-1", sqi="SQI-1"):
	return frappe._dict(idx=idx, item_code=item, material_request=mr, supplier_quotation=sq, supplier_quotation_item=sqi)


def order(items=None, currency="BHD", total=1000.0, verbal=0, **extra):
	doc = frappe._dict(
		doctype="Purchase Order",
		company=COMPANY,
		supplier=SUPPLIER,
		currency=currency,
		base_grand_total=total,
		custom_verbal=verbal,
		items=items if items is not None else [line(1)],
		flags=frappe._dict(),
	)
	doc.update(extra)
	return doc


class World:
	"""A stand-in for everything the rules read from the database.

	`requests`  {name: (docstatus, type)}                  Material Requests
	`quotations` {name: (docstatus, supplier)}             Supplier Quotations
	`quote_lines` [(name, parent, item, rfq)]               Supplier Quotation lines
	`competing`  {(rfq, item): {suppliers}}                  who quoted what on each Request for Quotation
	"""

	def __init__(self, requests=None, quotations=None, quote_lines=None, competing=None):
		self.requests = {"MR-1": (1, "Purchase")} if requests is None else requests
		self.quotations = {"SQ-1": (1, SUPPLIER)} if quotations is None else quotations
		self.quote_lines = [("SQI-1", "SQ-1", "STEEL-1", "RFQ-1")] if quote_lines is None else quote_lines
		self.competing = (
			{("RFQ-1", "STEEL-1"): {SUPPLIER, "Beta Metals", "Gamma Iron"}} if competing is None else competing
		)

	def get_all(self, doctype, filters=None, fields=None, **kwargs):
		names = (filters or {}).get("name", [None, []])[1] if isinstance(filters, dict) else []
		if doctype == "Material Request":
			return [
				frappe._dict(name=n, docstatus=self.requests[n][0], material_request_type=self.requests[n][1])
				for n in names
				if n in self.requests
			]
		if doctype == "Supplier Quotation":
			return [
				frappe._dict(name=n, docstatus=self.quotations[n][0], supplier=self.quotations[n][1])
				for n in names
				if n in self.quotations
			]
		if doctype == "Supplier Quotation Item":
			parents = filters["parent"][1]
			return [
				frappe._dict(name=n, parent=p, item_code=i, request_for_quotation=r)
				for n, p, i, r in self.quote_lines
				if p in parents
			]
		raise AssertionError(f"unexpected lookup of {doctype}")

	def __enter__(self):
		self.patches = [
			patch.object(frappe, "get_all", self.get_all),
			patch.object(frappe, "get_cached_value", return_value="BHD"),
			patch.object(pop, "competing_suppliers", lambda rfqs, items: self.competing),
		]
		for patcher in self.patches:
			patcher.start()
		return self

	def __exit__(self, *exc):
		for patcher in self.patches:
			patcher.stop()


class TestSettings(unittest.TestCase):
	def _read(self, rows=None, error=None):
		db = MagicMock()
		if error:
			db.sql.side_effect = error
		else:
			db.sql.return_value = rows or ()
		with patch.object(frappe, "db", db):
			return pop.rules()

	def test_nothing_stored_is_off_with_the_defaults(self):
		rule = self._read()
		self.assertFalse(rule.enforce)
		self.assertEqual((rule.above, rule.local_min, rule.import_min, rule.verbal_skips), (500.0, 3, 2, True))

	def test_stored_values_are_read(self):
		rule = self._read(
			(
				("enforce_po_procurement", "1"),
				("po_local_quotation_above", "750.5"),
				("po_local_min_quotations", "4"),
				("po_import_min_quotations", "3"),
				("po_verbal_skips_quotations", "0"),
			)
		)
		self.assertTrue(rule.enforce)
		self.assertEqual((rule.above, rule.local_min, rule.import_min, rule.verbal_skips), (750.5, 4, 3, False))

	def test_an_unticked_switch_is_off_and_a_blank_number_is_its_default(self):
		rule = self._read((("enforce_po_procurement", "0"), ("po_local_min_quotations", "")))
		self.assertFalse(rule.enforce)
		self.assertEqual(rule.local_min, 3)

	def test_a_settings_read_that_fails_never_blocks_a_save(self):
		self.assertFalse(self._read(error=Exception("Unknown column")).enforce)

	def test_the_settings_form_refuses_a_rule_that_could_never_bite(self):
		from sf_trading.sf_trading.doctype.sf_trading_settings.sf_trading_settings import SFTradingSettings

		check = SFTradingSettings.validate_po_procurement_rules
		check(frappe._dict(enforce_po_procurement=0, po_local_min_quotations=0, po_import_min_quotations=0, po_local_quotation_above=-5))
		ok = dict(enforce_po_procurement=1, po_local_min_quotations=3, po_import_min_quotations=2, po_local_quotation_above=500)
		check(frappe._dict(ok))
		for bad in ({"po_local_min_quotations": 0}, {"po_import_min_quotations": 0}, {"po_local_quotation_above": -1}):
			with self.assertRaises(frappe.ValidationError, msg=str(bad)):
				check(frappe._dict({**ok, **bad}))


class TestHowManyQuotations(unittest.TestCase):
	def test_an_import_needs_its_number_whatever_the_amount(self):
		with patch.object(frappe, "get_cached_value", return_value="BHD"):
			for total in (0, 1, 499, 500, 100000):
				self.assertEqual(pop.required_quotations(order(currency="USD", total=total), settings()), 2, total)

	def test_a_local_order_needs_quotations_only_above_the_amount(self):
		with patch.object(frappe, "get_cached_value", return_value="BHD"):
			self.assertEqual(pop.required_quotations(order(total=500), settings()), 0)
			self.assertEqual(pop.required_quotations(order(total=500.001), settings()), 3)
			self.assertEqual(pop.required_quotations(order(total=10), settings()), 0)

	def test_the_amount_and_the_numbers_come_from_the_settings(self):
		with patch.object(frappe, "get_cached_value", return_value="BHD"):
			rule = settings(above=1000, local_min=4, import_min=3)
			self.assertEqual(pop.required_quotations(order(total=1000), rule), 0)
			self.assertEqual(pop.required_quotations(order(total=1001), rule), 4)
			self.assertEqual(pop.required_quotations(order(currency="SAR", total=5), rule), 3)

	def test_a_foreign_currency_is_an_import_and_the_companys_own_is_not(self):
		with patch.object(frappe, "get_cached_value", return_value="BHD"):
			self.assertTrue(pop.is_import(order(currency="SAR")))
			self.assertFalse(pop.is_import(order(currency="BHD")))
			self.assertFalse(pop.is_import(order(currency=None)))


class TestMaterialRequest(unittest.TestCase):
	def test_every_line_from_a_submitted_purchase_request_passes(self):
		with World():
			self.assertEqual(pop.material_request_problems(pop.lines_of(order([line(1), line(2)]))), [])

	def test_lines_with_no_material_request_are_named_together(self):
		with World():
			doc = order([line(1), line(2, mr=""), line(3, mr="")])
			problems = pop.material_request_problems(pop.lines_of(doc))
		self.assertEqual(len(problems), 1)
		self.assertIn("Rows 2, 3", problems[0])
		self.assertIn("no Material Request", problems[0])

	def test_one_line_is_named_as_a_row(self):
		with World():
			problems = pop.material_request_problems(pop.lines_of(order([line(1, mr="")])))
		self.assertIn("Row 1", problems[0])
		self.assertNotIn("Rows", problems[0])

	def test_a_request_that_is_not_a_submitted_purchase_one_is_refused(self):
		for state in ((0, "Purchase"), (2, "Purchase"), (1, "Material Transfer"), (1, "Material Issue")):
			with World(requests={"MR-1": state}):
				problems = pop.material_request_problems(pop.lines_of(order()))
			self.assertEqual(len(problems), 1, state)
			self.assertIn("MR-1", problems[0])
			self.assertIn("not a submitted Material Request of type Purchase", problems[0], state)

	def test_a_request_that_does_not_exist_is_refused(self):
		with World(requests={}):
			problems = pop.material_request_problems(pop.lines_of(order()))
		self.assertIn("does not exist", problems[0])


class TestQuotations(unittest.TestCase):
	def _problems(self, world, doc=None, needed=3):
		with world:
			doc = doc or order()
			return pop.quotation_problems(doc, pop.lines_of(doc), needed)

	def test_enough_suppliers_quoted_the_item_on_the_request_for_quotation(self):
		self.assertEqual(self._problems(World()), [])

	def test_the_chosen_supplier_counts_among_those_who_quoted(self):
		world = World(competing={("RFQ-1", "STEEL-1"): {SUPPLIER, "Beta Metals"}})
		self.assertEqual(self._problems(world, needed=2), [])
		self.assertEqual(len(self._problems(world, needed=3)), 1)

	def test_too_few_quotations_says_how_many_there_were(self):
		world = World(competing={("RFQ-1", "STEEL-1"): {SUPPLIER, "Beta Metals"}})
		problems = self._problems(world, needed=3)
		self.assertIn("2 of the 3 Supplier Quotations needed", problems[0])
		self.assertIn("RFQ-1", problems[0])
		self.assertIn("STEEL-1", problems[0])

	def test_a_line_with_no_quotation_is_refused(self):
		problems = self._problems(World(), order([line(1, sq=""), line(2, sq="")]))
		self.assertIn("Rows 1, 2", problems[0])
		self.assertIn("no Supplier Quotation", problems[0])

	def test_a_draft_or_missing_quotation_is_refused(self):
		self.assertIn("is not submitted", self._problems(World(quotations={"SQ-1": (0, SUPPLIER)}))[0])
		self.assertIn("is not submitted", self._problems(World(quotations={"SQ-1": (2, SUPPLIER)}))[0])
		self.assertIn("does not exist", self._problems(World(quotations={}))[0])

	def test_another_suppliers_quotation_is_refused(self):
		problems = self._problems(World(quotations={"SQ-1": (1, "Beta Metals")}))
		self.assertIn("is from", problems[0])
		self.assertIn("Beta Metals", problems[0])
		self.assertIn(SUPPLIER, problems[0])

	def test_a_quotation_not_raised_from_a_request_for_quotation_does_not_count(self):
		problems = self._problems(World(quote_lines=[("SQI-1", "SQ-1", "STEEL-1", None)]))
		self.assertIn("was not raised from a Request for Quotation", problems[0])

	def test_the_line_the_order_names_decides_which_request_for_quotation(self):
		world = World(
			quote_lines=[("SQI-0", "SQ-1", "STEEL-1", None), ("SQI-1", "SQ-1", "STEEL-1", "RFQ-1")],
		)
		self.assertEqual(self._problems(world), [])

	def test_a_quotation_line_not_named_is_found_by_its_item(self):
		self.assertEqual(self._problems(World(), order([line(1, sqi="")])), [])

	def test_each_item_is_counted_on_its_own(self):
		world = World(
			quote_lines=[("SQI-1", "SQ-1", "STEEL-1", "RFQ-1"), ("SQI-2", "SQ-1", "PIPE-1", "RFQ-1")],
			competing={
				("RFQ-1", "STEEL-1"): {SUPPLIER, "Beta Metals", "Gamma Iron"},
				("RFQ-1", "PIPE-1"): {SUPPLIER},
			},
		)
		doc = order([line(1), line(2, item="PIPE-1", sqi="SQI-2")])
		problems = self._problems(world, doc)
		self.assertEqual(len(problems), 1)
		self.assertIn("PIPE-1", problems[0])
		self.assertIn("1 of the 3", problems[0])

	def test_one_bad_quotation_is_reported_once_however_many_lines_it_has(self):
		world = World(quotations={"SQ-1": (0, SUPPLIER)})
		self.assertEqual(len(self._problems(world, order([line(1), line(2), line(3)]))), 1)


class TestWhatIsChecked(unittest.TestCase):
	"""Which rule bites on which order."""

	def _problems(self, doc, rule=None, world=None):
		with (world or World()):
			return pop.problems_for(doc, rule or settings())

	def test_off_checks_nothing(self):
		doc = order([line(1, mr="", sq="")], total=10**6)
		self.assertEqual(self._problems(doc, settings(enforce=False)), [])

	def test_an_order_typed_in_directly_is_refused_for_both(self):
		problems = self._problems(order([line(1, mr="", sq="")]))
		self.assertEqual(len(problems), 2)
		self.assertIn("no Material Request", problems[0])
		self.assertIn("no Supplier Quotation", problems[1])

	def test_a_small_local_order_needs_a_material_request_but_no_quotations(self):
		problems = self._problems(order([line(1, sq="")], total=400))
		self.assertEqual(len(problems), 0)
		problems = self._problems(order([line(1, mr="", sq="")], total=400))
		self.assertEqual(len(problems), 1)
		self.assertIn("no Material Request", problems[0])

	def test_an_import_of_any_size_needs_quotations(self):
		problems = self._problems(order([line(1, sq="")], currency="USD", total=5))
		self.assertEqual(len(problems), 1)
		self.assertIn("no Supplier Quotation", problems[0])

	def test_a_compliant_order_passes(self):
		self.assertEqual(self._problems(order(total=900)), [])

	def test_a_verbal_order_skips_the_quotations_but_not_the_material_request(self):
		self.assertEqual(self._problems(order([line(1, sq="")], verbal=1)), [])
		problems = self._problems(order([line(1, mr="", sq="")], verbal=1))
		self.assertEqual(len(problems), 1)
		self.assertIn("no Material Request", problems[0])

	def test_a_verbal_import_skips_them_too(self):
		self.assertEqual(self._problems(order([line(1, sq="")], currency="USD", verbal=1)), [])

	def test_with_the_setting_off_verbal_is_only_a_label(self):
		problems = self._problems(order([line(1, sq="")], verbal=1), settings(verbal_skips=False))
		self.assertEqual(len(problems), 1)
		self.assertIn("no Supplier Quotation", problems[0])

	def test_an_internal_suppliers_order_is_not_held_to_them(self):
		self.assertEqual(self._problems(order([line(1, mr="", sq="")], is_internal_supplier=1)), [])

	def test_a_data_fix_can_flag_an_order_out(self):
		doc = order([line(1, mr="", sq="")])
		doc.flags.ignore_procurement_rules = True
		self.assertEqual(self._problems(doc), [])


class TestDuplicateAndAmend(unittest.TestCase):
	"""The desk's Duplicate drops the no_copy links and its Amend keeps them (frappe.model.copy_doc, from_amend)."""

	def _bare(self, idx, item="STEEL-1"):
		return line(idx, item=item, mr="", sq="", sqi="")

	def test_a_duplicate_carries_no_links_and_is_refused_like_an_order_typed_in(self):
		with World():
			problems = pop.problems_for(order([self._bare(1)]), settings())
		self.assertEqual(len(problems), 2)
		self.assertIn("no Material Request", problems[0])
		self.assertIn("no Supplier Quotation", problems[1])

	def test_an_amended_order_that_kept_its_links_passes(self):
		with World():
			self.assertEqual(pop.problems_for(order(amended_from="PO-OLD"), settings()), [])

	def test_an_amended_order_that_lost_them_is_not_forgiven(self):
		with World():
			problems = pop.problems_for(order([self._bare(1)], amended_from="PO-OLD"), settings())
		self.assertEqual(len(problems), 2)

	def test_the_links_are_read_as_they_stand_on_each_line(self):
		with World():
			lines = pop.lines_of(order([line(1), self._bare(2)]))
		self.assertEqual([l.material_request for l in lines], ["MR-1", ""])
		self.assertEqual([l.supplier_quotation_item for l in lines], ["SQI-1", ""])


class TestTheRefusal(unittest.TestCase):
	def _validate(self, doc, rule):
		with World(), patch.object(pop, "rules", return_value=rule):
			pop.validate(doc)

	def test_a_compliant_order_is_let_through(self):
		self._validate(order(), settings())

	def test_off_lets_anything_through(self):
		self._validate(order([line(1, mr="", sq="")]), settings(enforce=False))

	def test_the_refusal_names_the_rule_the_problems_and_the_way_to_comply(self):
		with self.assertRaises(frappe.ValidationError) as caught:
			self._validate(order([line(1, mr="", sq="")], total=900), settings())
		message = str(caught.exception)
		self.assertIn("no Material Request", message)
		self.assertIn("no Supplier Quotation", message)
		self.assertIn("a local order above", message)
		self.assertIn("3 Supplier Quotations", message)
		self.assertIn("Request for Quotation", message)

	def test_an_import_is_told_it_needs_them_whatever_the_amount(self):
		with self.assertRaises(frappe.ValidationError) as caught:
			self._validate(order([line(1, sq="")], currency="USD", total=5), settings())
		self.assertIn("whatever the amount", str(caught.exception))
		self.assertIn("2 Supplier Quotations", str(caught.exception))

	def test_a_small_order_is_told_only_about_the_material_request(self):
		with self.assertRaises(frappe.ValidationError) as caught:
			self._validate(order([line(1, mr="")], total=100), settings())
		self.assertIn("needs no quotations", str(caught.exception))

	def test_the_verbal_way_out_is_offered_only_while_it_works(self):
		with self.assertRaises(frappe.ValidationError) as caught:
			self._validate(order([line(1, mr="")]), settings(verbal_skips=True))
		self.assertIn("Verbal (No Quotation)", str(caught.exception))
		with self.assertRaises(frappe.ValidationError) as caught:
			self._validate(order([line(1, mr="")]), settings(verbal_skips=False))
		self.assertNotIn("Verbal (No Quotation)", str(caught.exception))


class TestCompetingQuotationsQuery(unittest.TestCase):
	def test_nothing_to_look_up_asks_nothing(self):
		with patch.object(frappe, "qb", MagicMock()) as qb:
			self.assertEqual(pop.competing_suppliers(set(), {"A"}), {})
			self.assertEqual(pop.competing_suppliers({"R"}, set()), {})
		qb.from_.assert_not_called()


class TestWiring(unittest.TestCase):
	def test_the_rule_runs_on_every_purchase_order_validate(self):
		handlers = hooks.doc_events["Purchase Order"]["validate"]
		self.assertIn("sf_trading.purchase_order_procurement.validate", handlers)

	def test_the_verbal_fields_are_ensured_after_migrate(self):
		self.assertIn("sf_trading.purchase_order_procurement.ensure_custom_fields", hooks.after_migrate)

	def test_every_registered_path_imports(self):
		for path in ("sf_trading.purchase_order_procurement.validate", "sf_trading.purchase_order_procurement.ensure_custom_fields"):
			module, _sep, attr = path.rpartition(".")
			self.assertTrue(callable(getattr(importlib.import_module(module), attr)), path)

	def test_the_verbal_fields_keep_the_definition_production_already_has(self):
		with patch.object(pop, "create_custom_fields") as create:
			pop.ensure_custom_fields()
		fields = {f["fieldname"]: f for f in create.call_args[0][0]["Purchase Order"]}
		self.assertEqual(set(fields), {"custom_verbal", "custom_verbal_comment"})
		self.assertEqual(fields["custom_verbal"]["fieldtype"], "Check")
		self.assertEqual(fields["custom_verbal"]["label"], "Verbal (No Quotation)")
		self.assertEqual(fields["custom_verbal_comment"]["mandatory_depends_on"], "eval:doc.custom_verbal")

	def test_the_settings_doctype_carries_the_five_fields_with_their_defaults(self):
		path = os.path.join(
			os.path.dirname(__file__), "..", "sf_trading", "doctype", "sf_trading_settings", "sf_trading_settings.json"
		)
		meta = json.load(open(path))
		fields = {f["fieldname"]: f for f in meta["fields"]}
		for name, default in (
			("enforce_po_procurement", "0"),
			("po_local_quotation_above", "500"),
			("po_local_min_quotations", "3"),
			("po_import_min_quotations", "2"),
			("po_verbal_skips_quotations", "1"),
		):
			self.assertIn(name, meta["field_order"], name)
			self.assertEqual(fields[name]["default"], default, name)
		# the code's fallbacks are the form's defaults
		self.assertEqual({k: str(v) for k, v in pop.DEFAULTS.items()}, {k: fields[k]["default"] for k in pop.DEFAULTS})


class TestSeedPatch(unittest.TestCase):
	def _run(self, stored):
		from sf_trading.patches.v0_1 import seed_po_procurement_settings as patch_module

		db = MagicMock()
		db.exists.return_value = True
		db.get_singles_dict.return_value = stored
		with patch.object(frappe, "db", db):
			patch_module.execute()
		return {call.args[1]: call.args[2] for call in db.set_single_value.call_args_list}

	def test_a_settings_page_that_never_stored_them_is_filled_with_the_defaults(self):
		self.assertEqual(
			self._run({}),
			{"po_local_quotation_above": 500, "po_local_min_quotations": 3, "po_import_min_quotations": 2, "po_verbal_skips_quotations": 1},
		)

	def test_a_value_somebody_set_is_left_alone(self):
		filled = self._run({"po_local_min_quotations": "4", "po_verbal_skips_quotations": "0"})
		self.assertNotIn("po_local_min_quotations", filled)
		self.assertNotIn("po_verbal_skips_quotations", filled)
		self.assertIn("po_import_min_quotations", filled)

	def test_enforcement_is_never_switched_on_by_it(self):
		self.assertNotIn("enforce_po_procurement", self._run({}))

	def test_a_site_without_the_settings_is_skipped(self):
		from sf_trading.patches.v0_1 import seed_po_procurement_settings as patch_module

		db = MagicMock()
		db.exists.return_value = False
		with patch.object(frappe, "db", db):
			patch_module.execute()
		db.set_single_value.assert_not_called()
