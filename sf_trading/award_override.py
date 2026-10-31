# sf_trading/award_override.py
"""A Purchase Order that does not follow the recommendation of Supplier Quotation Comparison Extended says why.

For a Purchase Order whose lines come from Supplier Quotations raised from a Request for Quotation, each line is
checked against the comparison of that request, run the way the report runs by default: net price per stock unit in
company currency plus the landed charges on each quotation, lowest cost first. A line is a departure when its
quotation is not one the comparison awards any quantity to: a dearer quotation, an expired or stopped one, a supplier
that was not recommended.

A departure needs the buyer's reason, in `Reason for Not Following the Recommendation`, before the order can be saved.
What was departed from is written, row by row, in `Not the Recommended Quotation`, so the approver reads it on the
order. An order that follows the recommendation carries neither.

NOT CHECKED, because there is nothing to compare: a verbal order (it needs no quotation), an order to an internal
supplier, a line with no quotation, a quotation raised from no Request for Quotation, and a request the user cannot read
quotations of (the comparison comes back empty, and an empty answer is not a departure). A data fix can set
`doc.flags.ignore_award_override`.

The check always uses Lowest Cost. A buyer who chose by weighted score and picked its winner has departed from the
lowest cost and gives that as the reason.
"""

from __future__ import annotations

import frappe
from frappe import _
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields
from frappe.utils import cint, cstr, escape_html, flt, nowdate

from sf_trading.query import fetch_in
from sf_trading.sf_trading.report.supplier_quotation_comparison_extended import (
	supplier_quotation_comparison_extended as comparison_report,
)

ITEMS_FIELD = "custom_not_recommended_items"
REASON_FIELD = "custom_award_override_reason"
VERBAL = "custom_verbal"


def ensure_custom_fields():
	"""after_migrate: the two Purchase Order fields. The reason is always on the form, because a reason that appeared
	only once the server had refused the save could never be reached."""
	create_custom_fields(
		{
			"Purchase Order": [
				{
					"fieldname": ITEMS_FIELD,
					"label": "Not the Recommended Quotation",
					"fieldtype": "Small Text",
					"insert_after": "custom_verbal_comment",
					"read_only": 1,
					"no_copy": 1,
					"print_hide": 1,
					"depends_on": "eval:doc." + ITEMS_FIELD,
					"description": "Lines bought from a quotation the Supplier Quotation Comparison Extended did not recommend.",
				},
				{
					"fieldname": REASON_FIELD,
					"label": "Reason for Not Following the Recommendation",
					"fieldtype": "Small Text",
					"insert_after": ITEMS_FIELD,
					"no_copy": 1,
					"description": "Needed when a line is bought from a quotation other than the one the Supplier Quotation "
					"Comparison Extended recommends for the Request for Quotation.",
				},
			]
		},
		ignore_validate=True,
		update=True,
	)


def quotation_lines(doc) -> dict:
	"""{quotation line name: its Request for Quotation and item}, for the lines of the order that name one."""
	names = [row.get("supplier_quotation_item") for row in doc.get("items") or [] if row.get("supplier_quotation_item")]
	rows = fetch_in("Supplier Quotation Item", names, fields=["name", "request_for_quotation", "item_code"])
	return {row.name: row for row in rows}


def comparison_for(company, rfq):
	"""The report's own comparison of one Request for Quotation, over every date, quotations submitted."""
	return comparison_report.compare(
		frappe._dict(
			company=company,
			from_date="2000-01-01",
			to_date=nowdate(),
			request_for_quotation=rfq,
			status=comparison_report.STATUS_SUBMITTED,
		)
	)


def describe(row, chosen, best) -> str:
	"""One line saying what this row bought and what was recommended instead."""
	if chosen is None:
		bought = _("its quotation is not in the comparison")
	else:
		bought = _("bought from %s at %s a unit") % (cstr(chosen.supplier_name or chosen.supplier), flt(chosen.landed_unit_cost, 3))
	if best is None:
		instead = _("no quotation is in the running")
	else:
		instead = _("recommended: %s at %s a unit") % (cstr(best.supplier_name or best.supplier), flt(best.landed_unit_cost, 3))
	return _("Row %s, %s: %s; %s") % (row.idx, cstr(row.get("item_code")), bought, instead)


def departures(doc) -> list:
	"""One sentence per line of the order that is not bought from a quotation the comparison awards a quantity to."""
	lines = quotation_lines(doc)
	if not lines:
		return []

	comparisons, found = {}, []
	for row in doc.get("items") or []:
		line = lines.get(row.get("supplier_quotation_item"))
		if not (line and line.request_for_quotation):
			continue
		rfq = line.request_for_quotation
		if rfq not in comparisons:
			comparisons[rfq] = comparison_for(doc.get("company"), rfq)
		comparison = comparisons[rfq]
		if not comparison.quotes:
			continue
		group = comparison.groups.get((rfq, line.item_code))
		if not group:
			continue
		chosen = next((quote for quote in group if quote.quotation_item == row.supplier_quotation_item), None)
		if chosen is not None and chosen.award_qty > 0:
			continue
		found.append(describe(row, chosen, next((quote for quote in group if quote.recommended), None)))
	return found


def validate(doc, method=None):
	"""Purchase Order validate: record the departures from the recommendation, and ask for a reason when there are any."""
	if not (doc.meta.has_field(ITEMS_FIELD) and doc.meta.has_field(REASON_FIELD)):
		return
	skipped = (
		doc.docstatus == 2
		or cint(doc.get(VERBAL))
		or cint(doc.get("is_internal_supplier"))
		or doc.flags.get("ignore_award_override")
	)
	found = [] if skipped else departures(doc)
	doc.set(ITEMS_FIELD, "\n".join(found))
	if found and not cstr(doc.get(REASON_FIELD)).strip():
		frappe.throw(
			"<p>%s</p><ul>%s</ul><p>%s</p>"
			% (
				_("This Purchase Order does not follow the recommendation of the quotation comparison:"),
				"".join("<li>" + escape_html(line) + "</li>" for line in found),
				_("Write the reason in Reason for Not Following the Recommendation, then save again."),
			),
			title=_("Reason Required"),
		)
