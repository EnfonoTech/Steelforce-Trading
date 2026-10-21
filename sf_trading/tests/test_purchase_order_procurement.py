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
from types import MappingProxyType
from unittest.mock import MagicMock, patch

import frappe

from sf_trading import hooks
from sf_trading import purchase_order_procurement as pop

COMPANY = "Test Trading WLL"
SUPPLIER = "Alpha Steel"


def settings(**overrides):
	"""The rules as a company's row holds them: enforced, 500 / 3 / 2, a verbal order is exempt."""
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
	`quote_lines` [(name, parent, item, rfq, mr)]            Supplier Quotation lines
	`competing`  {(rfq, item): {suppliers}}                  who quoted what on each Request for Quotation
	"""

	def __init__(self, requests=None, quotations=None, quote_lines=None, competing=None):
		self.requests = {"MR-1": (1, "Purchase")} if requests is None else requests
		self.quotations = {"SQ-1": (1, SUPPLIER)} if quotations is None else quotations
		self.quote_lines = [("SQI-1", "SQ-1", "STEEL-1", "RFQ-1", "MR-1")] if quote_lines is None else quote_lines
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
				frappe._dict(name=n, parent=p, item_code=i, request_for_quotation=r, material_request=m)
				for n, p, i, r, m in self.quote_lines
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


class TestCompanyRules(unittest.TestCase):
	"""Each company has its own row of SF Trading Settings > Purchase Order Rules."""

	def _read(self, rows=None, error=None, company="Test Trading WLL"):
		db = MagicMock()
		if error:
			db.sql.side_effect = error
		else:
			db.sql.return_value = rows or []
		with patch.object(frappe, "db", db):
			return pop.rules(company), db

	def _row(self, **overrides):
		row = frappe._dict(
			enforce=1,
			local_quotation_above=750.5,
			local_min_quotations=4,
			import_min_quotations=3,
			verbal_skips_rules=0,
		)
		row.update(overrides)
		return row

	def test_a_company_with_no_row_is_not_enforced(self):
		rule, _db = self._read([])
		self.assertFalse(rule.enforce)

	def test_the_companys_row_is_read(self):
		rule, _db = self._read([self._row()])
		self.assertTrue(rule.enforce)
		self.assertEqual((rule.above, rule.local_min, rule.import_min, rule.verbal_skips), (750.5, 4, 3, False))

	def test_the_verbal_tick_is_read_from_the_row(self):
		rule, _db = self._read([self._row(verbal_skips_rules=1)])
		self.assertTrue(rule.verbal_skips)
		rule, _db = self._read([self._row(verbal_skips_rules=0)])
		self.assertFalse(rule.verbal_skips)

	def test_the_query_reads_the_one_verbal_tick(self):
		_rule, db = self._read([self._row()])
		self.assertIn("`verbal_skips_rules`", db.sql.call_args[0][0])
		self.assertNotIn("verbal_skips_material_request", db.sql.call_args[0][0])

	def test_a_row_with_enforce_unticked_is_not_enforced(self):
		rule, _db = self._read([self._row(enforce=0)])
		self.assertFalse(rule.enforce)

	def test_each_company_has_its_own_numbers(self):
		rows = {"Alpha": self._row(local_min_quotations=3), "Beta": self._row(local_min_quotations=2, local_quotation_above=100)}
		db = MagicMock()
		db.sql.side_effect = lambda sql, values, **kw: [rows[values[3]]] if values[3] in rows else []
		with patch.object(frappe, "db", db):
			alpha, beta, gamma = pop.rules("Alpha"), pop.rules("Beta"), pop.rules("Gamma")
		self.assertEqual((alpha.local_min, alpha.above), (3, 750.5))
		self.assertEqual((beta.local_min, beta.above), (2, 100))
		self.assertFalse(gamma.enforce)

	def test_the_row_is_looked_up_by_company_in_the_settings_table(self):
		_rule, db = self._read([], company="Alpha")
		sql, values = db.sql.call_args[0][:2]
		self.assertIn("`tabSF Purchase Order Rule`", sql)
		self.assertEqual(values, ("SF Trading Settings", "SF Trading Settings", "purchase_order_rules", "Alpha"))

	def test_a_document_with_no_company_asks_nothing(self):
		rule, db = self._read([self._row()], company=None)
		self.assertFalse(rule.enforce)
		db.sql.assert_not_called()

	def test_a_site_that_has_not_migrated_the_table_never_blocks_a_save(self):
		rule, _db = self._read(error=Exception("Table 'tabSF Purchase Order Rule' doesn't exist"))
		self.assertFalse(rule.enforce)

	def test_the_order_is_held_to_its_own_companys_row(self):
		doc = order([line(1, mr="", sq="")], company="Alpha")
		with World(), patch.object(pop, "rules", side_effect=lambda company: settings(enforce=(company == "Alpha"))):
			self.assertEqual(len(pop.problems_for(doc)), 2)
			self.assertEqual(pop.problems_for(order([line(1, mr="", sq="")], company="Beta")), [])

	def test_the_settings_form_refuses_a_rule_that_could_never_bite(self):
		from sf_trading.sf_trading.doctype.sf_trading_settings.sf_trading_settings import SFTradingSettings

		def form(*rows):
			return frappe._dict(purchase_order_rules=list(rows))

		def row(idx, company="Alpha", **kw):
			values = dict(idx=idx, company=company, enforce=1, local_quotation_above=500, local_min_quotations=3, import_min_quotations=2)
			values.update(kw)
			return frappe._dict(values)

		check = SFTradingSettings.validate_po_procurement_rules
		check(form())
		check(form(row(1, "Alpha"), row(2, "Beta")))
		check(form(row(1, enforce=0, local_min_quotations=0, import_min_quotations=0)))  # not enforced: nothing to ask of it
		for bad in (
			(row(1, "Alpha"), row(2, "Alpha")),
			(row(1, local_min_quotations=0),),
			(row(1, import_min_quotations=0),),
			(row(1, local_quotation_above=-1),),
			(row(1, enforce=0, local_quotation_above=-1),),
		):
			with self.assertRaises(frappe.ValidationError, msg=str(bad)):
				check(form(*bad))


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
		problems = self._problems(World(quote_lines=[("SQI-1", "SQ-1", "STEEL-1", None, "MR-1")]))
		self.assertIn("was not raised from a Request for Quotation", problems[0])

	def test_the_line_the_order_names_decides_which_request_for_quotation(self):
		world = World(
			quote_lines=[("SQI-0", "SQ-1", "STEEL-1", None, "MR-1"), ("SQI-1", "SQ-1", "STEEL-1", "RFQ-1", "MR-1")],
		)
		self.assertEqual(self._problems(world), [])

	def test_a_quotation_line_not_named_is_found_by_its_item(self):
		self.assertEqual(self._problems(World(), order([line(1, sqi="")])), [])

	def test_each_item_is_counted_on_its_own(self):
		world = World(
			quote_lines=[("SQI-1", "SQ-1", "STEEL-1", "RFQ-1", "MR-1"), ("SQI-2", "SQ-1", "PIPE-1", "RFQ-1", "MR-1")],
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

	def test_the_quotation_must_come_from_this_lines_material_request(self):
		world = World(quote_lines=[("SQI-1", "SQ-1", "STEEL-1", "RFQ-1", "MR-9")])
		problems = self._problems(world)
		self.assertEqual(len(problems), 1)
		self.assertIn("was not raised from Material Request", problems[0])
		self.assertIn("MR-1", problems[0])
		self.assertIn("Row 1", problems[0])

	def test_a_quotation_whose_request_for_quotation_has_no_material_request_is_not_connected(self):
		world = World(quote_lines=[("SQI-1", "SQ-1", "STEEL-1", "RFQ-1", None)])
		problems = self._problems(world)
		self.assertEqual(len(problems), 1)
		self.assertIn("was not raised from Material Request", problems[0])

	def test_a_line_with_no_material_request_is_not_also_told_the_quotation_does_not_match(self):
		world = World(quote_lines=[("SQI-1", "SQ-1", "STEEL-1", "RFQ-1", "MR-9")])
		problems = self._problems(world, order([line(1, mr="")]))
		self.assertEqual(problems, [])

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

	def test_a_local_order_up_to_the_amount_is_held_to_nothing(self):
		# no Material Request, no quotation, nothing: the chain starts above the amount
		self.assertEqual(self._problems(order([line(1, mr="", sq="", sqi="")], total=400)), [])
		self.assertEqual(self._problems(order([line(1, mr="", sq="", sqi="")], total=500)), [])
		problems = self._problems(order([line(1, mr="", sq="", sqi="")], total=500.01))
		self.assertEqual(len(problems), 2)

	def test_an_import_of_any_size_needs_the_whole_chain(self):
		problems = self._problems(order([line(1, mr="", sq="", sqi="")], currency="USD", total=5))
		self.assertEqual(len(problems), 2)
		self.assertIn("no Material Request", problems[0])
		self.assertIn("no Supplier Quotation", problems[1])

	def test_an_import_with_its_request_but_no_quotation_is_refused_for_the_quotation(self):
		problems = self._problems(order([line(1, sq="")], currency="USD", total=5))
		self.assertEqual(len(problems), 1)
		self.assertIn("no Supplier Quotation", problems[0])

	def test_a_compliant_order_passes(self):
		self.assertEqual(self._problems(order(total=900)), [])

	def test_a_verbal_order_needs_no_material_request_and_no_quotations(self):
		rule = settings()
		self.assertEqual(self._problems(order([line(1, mr="", sq="", sqi="")], verbal=1, total=10**6), rule), [])
		self.assertEqual(self._problems(order([line(1, sq="")], verbal=1), rule), [])

	def test_a_verbal_import_needs_nothing_either(self):
		self.assertEqual(self._problems(order([line(1, mr="", sq="", sqi="")], currency="USD", verbal=1)), [])

	def test_with_the_row_not_exempting_verbal_it_is_only_a_label(self):
		problems = self._problems(order([line(1, mr="", sq="", sqi="")], verbal=1), settings(verbal_skips=False))
		self.assertEqual(len(problems), 2)

	def test_the_verbal_tick_does_nothing_for_an_order_not_marked_verbal(self):
		problems = self._problems(order([line(1, mr="", sq="", sqi="")], verbal=0), settings(verbal_skips=True))
		self.assertEqual(len(problems), 2)

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
			self._validate(order([line(1, mr="", sq="", sqi="")], total=900), settings())
		message = str(caught.exception)
		self.assertIn("no Material Request", message)
		self.assertIn("no Supplier Quotation", message)
		self.assertIn("a local order above", message)
		self.assertIn("a Material Request, one Request for Quotation raised from it and 3 Supplier Quotations on it", message)

	def test_an_import_is_told_it_needs_the_chain_whatever_the_amount(self):
		with self.assertRaises(frappe.ValidationError) as caught:
			self._validate(order([line(1, sq="")], currency="USD", total=5), settings())
		self.assertIn("whatever the amount", str(caught.exception))
		self.assertIn("a Material Request, one Request for Quotation raised from it and 2 Supplier Quotations on it", str(caught.exception))

	def test_a_small_local_order_is_let_through_with_nothing(self):
		self._validate(order([line(1, mr="", sq="", sqi="")], total=100), settings())

	def test_the_verbal_way_out_is_offered_only_while_it_works(self):
		with self.assertRaises(frappe.ValidationError) as caught:
			self._validate(order([line(1, mr="")]), settings(verbal_skips=True))
		self.assertIn("ticks Verbal (No Quotation) instead", str(caught.exception))
		with self.assertRaises(frappe.ValidationError) as caught:
			self._validate(order([line(1, mr="")]), settings(verbal_skips=False))
		self.assertNotIn("Verbal (No Quotation)", str(caught.exception))

	def test_a_verbal_order_the_row_exempts_is_let_through(self):
		self._validate(order([line(1, mr="", sq="", sqi="")], verbal=1), settings())


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

	def _doctype(self, *parts):
		path = os.path.join(os.path.dirname(__file__), "..", "sf_trading", "doctype", *parts)
		return json.load(open(path))

	def test_the_rule_row_carries_the_numbers_with_their_defaults(self):
		meta = self._doctype("sf_purchase_order_rule", "sf_purchase_order_rule.json")
		self.assertEqual(meta["name"], pop.RULE_TABLE)
		self.assertEqual(meta["istable"], 1)
		fields = {f["fieldname"]: f for f in meta["fields"]}
		self.assertEqual(
			{name: fields[name].get("default") for name in ("enforce", "local_quotation_above", "local_min_quotations", "import_min_quotations", "verbal_skips_rules")},
			{"enforce": "1", "local_quotation_above": "500", "local_min_quotations": "3", "import_min_quotations": "2", "verbal_skips_rules": "1"},
		)
		self.assertEqual(fields["company"]["reqd"], 1)
		self.assertEqual(fields["local_quotation_above"]["options"], "currency")
		self.assertEqual(fields["currency"]["fetch_from"], "company.default_currency")
		# the code's fallbacks are the row's defaults
		self.assertEqual((pop.DEFAULT_ABOVE, pop.DEFAULT_LOCAL_MIN, pop.DEFAULT_IMPORT_MIN), (500.0, 3, 2))
		# the row has exactly these columns, one verbal tick among them, and no leftover of the two earlier ticks
		self.assertEqual(
			set(fields),
			{"company", "currency", "enforce", "local_quotation_above", "local_min_quotations", "import_min_quotations", "verbal_skips_rules"},
		)
		self.assertEqual(set(meta["field_order"]), set(fields))
		# the grid is ten columns wide: every column in the list view fits
		self.assertLessEqual(sum(f.get("columns", 0) for f in meta["fields"] if f.get("in_list_view")), 10)

	def test_the_settings_page_holds_the_table_and_no_longer_the_five_global_fields(self):
		meta = self._doctype("sf_trading_settings", "sf_trading_settings.json")
		fields = {f["fieldname"]: f for f in meta["fields"]}
		self.assertEqual(fields["purchase_order_rules"]["fieldtype"], "Table")
		self.assertEqual(fields["purchase_order_rules"]["options"], pop.RULE_TABLE)
		self.assertEqual(pop.RULE_FIELD, "purchase_order_rules")
		for gone in ("enforce_po_procurement", "po_local_quotation_above", "po_local_min_quotations", "po_import_min_quotations", "po_verbal_skips_quotations"):
			self.assertNotIn(gone, fields)

	def test_the_settings_page_is_in_tabs_and_nothing_is_lost(self):
		meta = self._doctype("sf_trading_settings", "sf_trading_settings.json")
		fields = {f["fieldname"]: f for f in meta["fields"]}
		order = meta["field_order"]
		self.assertEqual(len(order), len(set(order)), "a field is placed twice")
		self.assertEqual(set(order), set(fields), "a field is defined but not placed, or placed but not defined")
		self.assertEqual(fields[order[0]]["fieldtype"], "Tab Break", "the first field must open a tab")
		tabs = [fields[n]["label"] for n in order if fields[n]["fieldtype"] == "Tab Break"]
		self.assertEqual(tabs, ["Sales", "Purchasing", "Masters and Documents", "Accounting and Reports", "Support"])
		# every section sits under a tab, and the purchase rules sit under Purchasing
		tab = None
		under = {}
		for name in order:
			if fields[name]["fieldtype"] == "Tab Break":
				tab = fields[name]["label"]
			under[name] = tab
		self.assertEqual(under["purchase_order_rules"], "Purchasing")
		self.assertEqual(under["disable_purchase_receipt"], "Purchasing")
		self.assertEqual(under["restrict_sales_return"], "Sales")
		self.assertEqual(under["fast_financial_statements"], "Accounting and Reports")
		self.assertEqual(under["ticket_auto_close_days"], "Support")


class TestConversionPatch(unittest.TestCase):
	"""0.1.66 held one switch and four numbers for every company; the per-company table carries them over."""

	COMPANIES = ("Alpha", "Beta")

	def _run(self, stored, rows_in_table=0, table=True):
		from sf_trading.patches.v0_1 import po_procurement_rules_by_company as patch_module

		db = MagicMock()
		db.exists.return_value = True
		db.table_exists.return_value = table
		db.get_singles_dict.return_value = stored
		db.count.return_value = rows_in_table
		settings_doc = MagicMock()
		with (
			patch.object(frappe, "db", db),
			patch.object(frappe, "get_single", return_value=settings_doc),
			patch.object(frappe, "get_all", return_value=list(self.COMPANIES)),
		):
			patch_module.execute()
		appended = [call.args for call in settings_doc.append.call_args_list]
		return appended, settings_doc, db

	ON = MappingProxyType(
		{
			"enforce_po_procurement": "1",
			"po_local_quotation_above": "750",
			"po_local_min_quotations": "4",
			"po_import_min_quotations": "3",
			"po_verbal_skips_quotations": "0",
		}
	)

	def test_a_ticked_switch_becomes_one_enforced_row_per_company_with_its_numbers(self):
		appended, settings_doc, _db = self._run(dict(self.ON))
		self.assertEqual([args[1]["company"] for args in appended], ["Alpha", "Beta"])
		for field, row in appended:
			self.assertEqual(field, "purchase_order_rules")
			self.assertEqual(
				(
					row["enforce"],
					row["local_quotation_above"],
					row["local_min_quotations"],
					row["import_min_quotations"],
					row["verbal_skips_rules"],
				),
				(1, 750.0, 4, 3, 0),
			)
		settings_doc.save.assert_called_once()

	def test_a_value_never_stored_takes_its_default(self):
		appended, _doc, _db = self._run({"enforce_po_procurement": "1"})
		row = appended[0][1]
		self.assertEqual((row["local_quotation_above"], row["local_min_quotations"], row["import_min_quotations"], row["verbal_skips_rules"]), (500.0, 3, 2, 1))

	def test_a_switch_never_ticked_adds_no_rows_but_the_old_values_still_go(self):
		for stored in ({}, {"enforce_po_procurement": "0", "po_local_min_quotations": "4"}):
			appended, settings_doc, db = self._run(stored)
			self.assertEqual(appended, [])
			settings_doc.save.assert_not_called()
			self.assertIn("DELETE FROM `tabSingles`", db.sql.call_args[0][0])
			self.assertIn("enforce_po_procurement", db.sql.call_args[0][1][1])

	def test_a_table_that_already_has_rows_is_left_alone(self):
		appended, settings_doc, _db = self._run(dict(self.ON), rows_in_table=1)
		self.assertEqual(appended, [])
		settings_doc.save.assert_not_called()

	def test_a_site_without_the_table_or_the_settings_is_skipped(self):
		appended, settings_doc, db = self._run(dict(self.ON), table=False)
		self.assertEqual(appended, [])
		db.sql.assert_not_called()


class TestVerbalExemptPatch(unittest.TestCase):
	def _run(self, table=True, column=True):
		from sf_trading.patches.v0_1 import po_rule_verbal_exempt as patch_module

		db = MagicMock()
		db.table_exists.return_value = table
		db.has_column.return_value = column
		with patch.object(frappe, "db", db):
			patch_module.execute()
		return db

	def test_a_row_with_no_value_stored_is_ticked(self):
		db = self._run()
		sql = db.sql.call_args[0][0]
		self.assertIn("SET `verbal_skips_rules` = 1 WHERE `verbal_skips_rules` IS NULL", sql)

	def test_a_value_somebody_set_is_not_touched(self):
		self.assertNotIn("UPDATE `tabSF Purchase Order Rule` SET `verbal_skips_rules` = 1;", self._run().sql.call_args[0][0])
		self.assertIn("IS NULL", self._run().sql.call_args[0][0])

	def test_a_site_without_the_table_or_the_column_is_skipped(self):
		self._run(table=False).sql.assert_not_called()
		self._run(column=False).sql.assert_not_called()
