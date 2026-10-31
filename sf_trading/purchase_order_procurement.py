# sf_trading/purchase_order_procurement.py
"""Purchase Order procurement rules: a Purchase Order above the line is the end of one Material Request -> Request for
Quotation -> Supplier Quotations chain.

For a company with a row (Enforce ticked) in SF Trading Settings > Purchasing > Purchase Order Rules, a Purchase Order is
held to the chain when it is

  * an import (an order in a foreign currency): whatever the amount, with the row's `import_min_quotations`, or
  * a local order (in the company's own currency) whose Grand Total is above the row's `local_quotation_above`, with
    `local_min_quotations`;

a local order up to that amount is held to nothing. Held to the chain, an order cannot be saved or submitted unless every
line

  * comes from a submitted Material Request of type Purchase, and
  * from a submitted Supplier Quotation of THIS supplier that was raised from a Request for Quotation, which in turn was
    raised from that same Material Request (the quotation line carries the Material Request core copied from the request),
    and
  * that many different suppliers quoted the same item on that same Request for Quotation, the chosen one included.

An order marked Verbal (`custom_verbal`) is held to nothing when the company's row ticks `verbal_skips_rules`: it needs no
Material Request and no quotations.

Each company has its own row, so each has its own amount, its own numbers and its own verbal switch. A company with no
row, or a row with Enforce unticked, is not held to the rules at all.

The chain is read from the links core writes on each line (`material_request`, `supplier_quotation`,
`supplier_quotation_item`, and on the quotation line `request_for_quotation`). Core marks the first three no_copy, so a
Duplicate of an order carries none of them and is refused like any order typed in by hand: duplicating is no way round
the rule. An Amend keeps them (the desk copies no_copy fields when amending), so cancelling an order and amending it
passes for as long as its lines keep their links.

An order to an internal (inter-company) supplier is not a purchase from the market and is not held to the rules.

The row is read straight from its table, and a site that has not migrated the table yet reads as "no row", so a pull
before `bench migrate` cannot break a Purchase Order save.

Unless SF Trading Settings > Purchasing > Procurement Flow ticks `Allow a Purchase Order Without the Whole Chain`, no
order escapes the chain: a company with no rule, a rule switched off and a local order at or under its amount are all held
to a Material Request, a Request for Quotation raised from it and at least one Supplier Quotation on it (more where the
rule asks for more). A verbal order and an internal supplier stay exempt as before. The two steps before the order, the
Request for Quotation and the Supplier Quotation, are held by procurement_flow.py.
"""

from __future__ import annotations

import frappe
from frappe import _
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields
from frappe.utils import cint, cstr, escape_html, flt, fmt_money

SETTINGS = "SF Trading Settings"
RULE_TABLE = "SF Purchase Order Rule"
RULE_FIELD = "purchase_order_rules"
VERBAL = "custom_verbal"
VERBAL_COMMENT = "custom_verbal_comment"
ALLOW_DIRECT = "allow_direct_purchase_order"

#: what a new row of the table starts with (its field defaults), and what a message shows for a company with no row
DEFAULT_ABOVE = 500.0
DEFAULT_LOCAL_MIN = 3
DEFAULT_IMPORT_MIN = 2


def ensure_custom_fields():
	"""after_migrate: the Verbal tick and its comment on Purchase Order. They were first made by hand on production,
	so these are that definition; a site that has them is left as it is by the update, one that has not gets them."""
	create_custom_fields(
		{
			"Purchase Order": [
				{
					"fieldname": VERBAL,
					"label": "Verbal (No Quotation)",
					"fieldtype": "Check",
					"insert_after": "company",
					"default": "0",
					"description": "Tick if the PO is based on a verbal quote. A comment is required and no attachment is needed.",
				},
				{
					"fieldname": VERBAL_COMMENT,
					"label": "Verbal Comment",
					"fieldtype": "Small Text",
					"insert_after": VERBAL,
					"depends_on": f"eval:doc.{VERBAL}",
					"mandatory_depends_on": f"eval:doc.{VERBAL}",
					"description": "Details of the verbal quote/approval.",
				},
			]
		},
		ignore_validate=True,
		update=True,
	)


# ── The company's rules ─────────────────────────────────────────────────────────────────────────────────────


def rules(company) -> frappe._dict:
	"""This company's row of SF Trading Settings > Purchase Order Rules. No row, a row with Enforce unticked, or a site that
	has not migrated the table yet: not enforced."""
	row = None
	if company:
		try:
			rows = frappe.db.sql(
				"SELECT `enforce`, `local_quotation_above`, `local_min_quotations`, `import_min_quotations`, "
				"`verbal_skips_rules` FROM `tabSF Purchase Order Rule` "
				"WHERE `parent` = %s AND `parenttype` = %s AND `parentfield` = %s AND `company` = %s "
				"ORDER BY `idx` LIMIT 1",
				(SETTINGS, SETTINGS, RULE_FIELD, company),
				as_dict=True,
			)
			row = rows[0] if rows else None
		except Exception:
			row = None
	if not row:
		return frappe._dict(
			enforce=False,
			above=DEFAULT_ABOVE,
			local_min=DEFAULT_LOCAL_MIN,
			import_min=DEFAULT_IMPORT_MIN,
			verbal_skips=True,
		)
	return frappe._dict(
		enforce=bool(cint(row.enforce)),
		above=flt(row.local_quotation_above),
		local_min=cint(row.local_min_quotations),
		import_min=cint(row.import_min_quotations),
		verbal_skips=bool(cint(row.verbal_skips_rules)),
	)


def is_exempt(doc) -> bool:
	"""An order to an internal supplier (inter-company), or one a data fix flagged."""
	return bool(cint(doc.get("is_internal_supplier")) or doc.flags.get("ignore_procurement_rules"))


def is_import(doc) -> bool:
	"""An order in a currency other than the company's own, as the Purchase Invoice rules decide it."""
	currency = doc.get("currency")
	company = doc.get("company")
	if not (currency and company):
		return False
	return currency != frappe.get_cached_value("Company", company, "default_currency")


def required_quotations(doc, rule) -> int:
	"""How many quotations this order needs, and so whether it is held to the chain at all: the import number whatever
	the amount, the local one above the amount, none (not held) at or under it."""
	if is_import(doc):
		return rule.import_min
	if flt(doc.get("base_grand_total")) > rule.above:
		return rule.local_min
	return 0


def skips_rules(doc, rule) -> bool:
	"""A verbal order needs no Material Request and no quotations, when the company's row says so."""
	return bool(rule.verbal_skips and cint(doc.get(VERBAL)))


def direct_orders_allowed() -> bool:
	"""SF Trading Settings > Purchasing > Procurement Flow: a Purchase Order may be raised without the whole chain. Off
	unless someone ticked it, and off on a site that has not migrated the tick yet."""
	try:
		return bool(cint(frappe.db.get_single_value(SETTINGS, ALLOW_DIRECT)))
	except Exception:
		return False


def quotations_needed(doc, rule) -> int:
	"""How many quotations this order needs, and so whether it is held to the chain at all. The company's rule decides
	first (an import always, a local order above its amount); unless direct orders are allowed every order then needs at
	least one, so the chain is never skipped, whatever the amount and whether or not the company has a rule."""
	needed = required_quotations(doc, rule) if rule.enforce else 0
	if not direct_orders_allowed():
		needed = max(needed, 1)
	return needed


# ── The lines, with the links that make the case for each ─────────────────────────────────────────────────


def lines_of(doc) -> list:
	"""Each line of the order with its Material Request and Supplier Quotation links."""
	return [
		frappe._dict(
			idx=row.idx,
			item=cstr(row.get("item_code")),
			material_request=cstr(row.get("material_request")).strip(),
			supplier_quotation=cstr(row.get("supplier_quotation")).strip(),
			supplier_quotation_item=cstr(row.get("supplier_quotation_item")).strip(),
		)
		for row in doc.get("items") or []
	]


# ── The checks (each returns plain sentences; nothing is thrown here) ──────────────────────────────────────


def _rows(indexes) -> str:
	indexes = sorted(indexes)
	label = _("Row {0}") if len(indexes) == 1 else _("Rows {0}")
	return label.format(", ".join(str(i) for i in indexes))


def _name(value) -> str:
	return frappe.bold(escape_html(cstr(value)))


def material_request_problems(lines) -> list:
	"""Every line from a submitted Material Request of type Purchase."""
	problems = []
	without = [line.idx for line in lines if not line.material_request]
	if without:
		problems.append(_("{0}: no Material Request.").format(_rows(without)))

	named = sorted({line.material_request for line in lines if line.material_request})
	found = {}
	if named:
		found = {
			row.name: row
			for row in frappe.get_all(
				"Material Request",
				filters={"name": ["in", named]},
				fields=["name", "material_request_type", "docstatus"],
			)
		}
	for name in named:
		request = found.get(name)
		where = [line.idx for line in lines if line.material_request == name]
		if not request:
			problems.append(_("{0}: Material Request {1} does not exist.").format(_rows(where), _name(name)))
		elif request.docstatus != 1 or request.material_request_type != "Purchase":
			problems.append(
				_("{0}: Material Request {1} is not a submitted Material Request of type Purchase.").format(
					_rows(where), _name(name)
				)
			)
	return problems


def _quotation_items(names) -> list:
	return frappe.get_all(
		"Supplier Quotation Item",
		filters={"parent": ["in", sorted(names)], "parenttype": "Supplier Quotation"},
		fields=["name", "parent", "item_code", "request_for_quotation", "material_request"],
	)


def _evidence(line, items):
	"""The quotation line that backs a line of the order: the one it names, else one of the same item on that
	quotation (preferring one raised from a Request for Quotation)."""
	named = next((i for i in items if i.name == line.supplier_quotation_item and i.parent == line.supplier_quotation), None)
	if named:
		return named
	same = [i for i in items if i.parent == line.supplier_quotation and i.item_code == line.item]
	return next((i for i in same if i.request_for_quotation), same[0] if same else None)


def competing_suppliers(rfqs, items) -> dict:
	"""{(Request for Quotation, item): the suppliers with a submitted Supplier Quotation for it}."""
	if not (rfqs and items):
		return {}
	quotation = frappe.qb.DocType("Supplier Quotation")
	line = frappe.qb.DocType("Supplier Quotation Item")
	rows = (
		frappe.qb.from_(line)
		.join(quotation)
		.on(quotation.name == line.parent)
		.select(line.request_for_quotation, line.item_code, quotation.supplier)
		.where(quotation.docstatus == 1)
		.where(line.request_for_quotation.isin(sorted(rfqs)))
		.where(line.item_code.isin(sorted(items)))
		.run()
	)
	suppliers = {}
	for rfq, item, supplier in rows:
		suppliers.setdefault((rfq, item), set()).add(supplier)
	return suppliers


def quotation_problems(doc, lines, needed) -> list:
	"""Every line from a submitted quotation of this supplier, raised from a Request for Quotation that was raised from
	the line's own Material Request, and that `needed` different suppliers quoted that item on."""
	problems = []
	without = [line.idx for line in lines if not line.supplier_quotation]
	if without:
		problems.append(_("{0}: no Supplier Quotation.").format(_rows(without)))

	named = {line.supplier_quotation for line in lines if line.supplier_quotation}
	if not named:
		return problems
	quotations = {
		row.name: row
		for row in frappe.get_all(
			"Supplier Quotation", filters={"name": ["in", sorted(named)]}, fields=["name", "supplier", "docstatus"]
		)
	}
	items = _quotation_items(named)

	reported = set()
	groups = {}
	for line in lines:
		name = line.supplier_quotation
		if not name or name in reported:
			continue
		quotation = quotations.get(name)
		if not quotation:
			reported.add(name)
			problems.append(_("Supplier Quotation {0} does not exist.").format(_name(name)))
			continue
		if quotation.docstatus != 1:
			reported.add(name)
			problems.append(_("Supplier Quotation {0} is not submitted.").format(_name(name)))
			continue
		if quotation.supplier != doc.get("supplier"):
			reported.add(name)
			problems.append(
				_("Supplier Quotation {0} is from {1}, but this order is to {2}.").format(
					_name(name), _name(quotation.supplier), _name(doc.get("supplier"))
				)
			)
			continue
		evidence = _evidence(line, items)
		rfq = cstr(evidence.request_for_quotation).strip() if evidence else ""
		if not rfq:
			reported.add(name)
			problems.append(_("Supplier Quotation {0} was not raised from a Request for Quotation.").format(_name(name)))
			continue
		# one chain: the request the quotation was raised from is the request this line comes from
		if line.material_request and cstr(evidence.material_request).strip() != line.material_request:
			reported.add(name)
			problems.append(
				_("{0}: Supplier Quotation {1} was not raised from Material Request {2}.").format(
					_rows([x.idx for x in lines if x.supplier_quotation == name]), _name(name), _name(line.material_request)
				)
			)
			continue
		groups.setdefault((rfq, line.item), []).append(line.idx)

	competing = competing_suppliers({rfq for rfq, _item in groups}, {item for _rfq, item in groups})
	for (rfq, item), where in sorted(groups.items(), key=lambda pair: min(pair[1])):
		got = len(competing.get((rfq, item), ()))
		if got < needed:
			problems.append(
				_("{0}: item {1} has {2} of the {3} Supplier Quotations needed on Request for Quotation {4}.").format(
					_rows(where), _name(item), got, needed, _name(rfq)
				)
			)
	return problems


def problems_for(doc, rule=None) -> list:
	"""Everything that keeps this order from being saved, as sentences; empty when it meets the rules, is not held to
	them (rules off, a local order up to the amount, a verbal order the row excuses) or they are off."""
	rule = rule or rules(doc.get("company"))
	if is_exempt(doc):
		return []
	needed = quotations_needed(doc, rule)
	if not needed or skips_rules(doc, rule):
		return []
	lines = lines_of(doc)
	return material_request_problems(lines) + quotation_problems(doc, lines, needed)


# ── The hook ────────────────────────────────────────────────────────────────────────────────────────────────


def _rule_sentence(doc, rule) -> str:
	needed = quotations_needed(doc, rule)
	if is_import(doc):
		return _(
			"an import needs a Material Request, one Request for Quotation raised from it and {0} Supplier Quotations on it, "
			"whatever the amount"
		).format(needed)
	if not (rule.enforce and flt(doc.get("base_grand_total")) > rule.above):
		return (
			_("an order needs a Material Request, one Request for Quotation raised from it and %s Supplier Quotation(s) on it")
			% needed
		)
	above = fmt_money(rule.above, currency=frappe.get_cached_value("Company", doc.company, "default_currency"))
	return _(
		"a local order above {0} needs a Material Request, one Request for Quotation raised from it and {1} Supplier "
		"Quotations on it"
	).format(above, needed)


def validate(doc, _method=None):
	"""Purchase Order validate (on save and on submit): refuse an order above the line that skips the Material Request ->
	Request for Quotation -> Supplier Quotation chain."""
	rule = rules(doc.get("company"))
	problems = problems_for(doc, rule)
	if not problems:
		return

	steps = _(
		"Raise a Material Request of type Purchase, send one Request for Quotation from it to the suppliers, enter each "
		"supplier's Supplier Quotation against that request, then create the Purchase Order from the chosen quotation."
	)
	if rule.verbal_skips:
		steps += " " + _("An order based on a verbal quote ticks Verbal (No Quotation) instead.")
	if not direct_orders_allowed():
		steps += " " + _(
			"An order without this chain can be allowed in SF Trading Settings > Purchasing > Procurement Flow."
		)
	frappe.throw(
		"<p>{}</p><ul>{}</ul><p>{}</p>".format(
			_("This Purchase Order does not meet the procurement rules ({0}):").format(_rule_sentence(doc, rule)),
			"".join(f"<li>{problem}</li>" for problem in problems),
			steps,
		),
		title=_("Procurement Rules Not Met"),
	)
