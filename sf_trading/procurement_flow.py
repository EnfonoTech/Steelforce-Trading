# sf_trading/procurement_flow.py
"""The buying chain is Material Request -> Request for Quotation -> Supplier Quotation -> Purchase Order, and it is not skipped.

Purchase Order Procurement Rules (`purchase_order_procurement.py`) already hold a Purchase Order to the chain. This module
holds the two steps before it:

  * a Request for Quotation must come from a submitted Material Request of type Purchase: every line names its
    `material_request`;
  * a Supplier Quotation must answer a submitted Request for Quotation: every line names its `request_for_quotation`.

A Supplier Quotation made straight from a Material Request (core's Create > Supplier Quotation) or typed in by hand names
no Request for Quotation and is refused, the same as a Request for Quotation typed in with no Material Request.

Each gap is closed unless SF Trading Settings > Purchasing > Procurement Flow says otherwise:

    Allow a Request for Quotation Without a Material Request
    Allow a Supplier Quotation Without a Request for Quotation
    Allow a Purchase Order Without the Whole Chain          (read by purchase_order_procurement.py)

A tick that was never saved reads as off, so the chain is enforced until someone opens a gap on purpose. The documents
already in the system are not touched: only a save is checked.

`rfq_dashboard` adds the Material Request to the Connections of a Request for Quotation, which core lists only for the
Supplier Quotations made from it.
"""

from __future__ import annotations

import frappe
from frappe import _
from frappe.utils import cint, cstr, escape_html

SETTINGS = "SF Trading Settings"
ALLOW_RFQ = "allow_rfq_without_material_request"
ALLOW_QUOTATION = "allow_quotation_without_rfq"


def allowed(fieldname) -> bool:
	"""A tick of SF Trading Settings. Off unless someone ticked it, and off on a site that has not migrated it yet."""
	try:
		return bool(cint(frappe.db.get_single_value(SETTINGS, fieldname)))
	except Exception:
		return False


def _rows(indexes) -> str:
	indexes = sorted(indexes)
	label = _("Row %s") if len(indexes) == 1 else _("Rows %s")
	return label % ", ".join(str(i) for i in indexes)


def _name(value) -> str:
	return frappe.bold(escape_html(cstr(value)))


def _refuse(problems, intro, steps):
	frappe.throw(
		"<p>%s</p><ul>%s</ul><p>%s</p>"
		% (intro, "".join("<li>" + problem + "</li>" for problem in problems), steps),
		title=_("Procurement Flow"),
	)


def _lines_naming(doc, fieldname):
	"""{name: [row index]} of the documents the lines name in `fieldname`, and the indexes of the lines that name none."""
	named, without = {}, []
	for row in doc.get("items") or []:
		name = cstr(row.get(fieldname)).strip()
		if name:
			named.setdefault(name, []).append(row.idx)
		else:
			without.append(row.idx)
	return named, without


def validate_request_for_quotation(doc, method=None):
	"""Request for Quotation validate: every line comes from a submitted Material Request of type Purchase."""
	if doc.docstatus == 2 or allowed(ALLOW_RFQ):
		return
	named, without = _lines_naming(doc, "material_request")
	problems = []
	if without:
		problems.append(_("%s: no Material Request.") % _rows(without))
	if named:
		found = {
			row.name: row
			for row in frappe.get_all(
				"Material Request",
				filters={"name": ["in", sorted(named)]},
				fields=["name", "docstatus", "material_request_type"],
			)
		}
		for name in sorted(named):
			request = found.get(name)
			if not request:
				problems.append(_("%s: Material Request %s does not exist.") % (_rows(named[name]), _name(name)))
			elif request.docstatus != 1 or request.material_request_type != "Purchase":
				problems.append(
					_("%s: Material Request %s is not a submitted Material Request of type Purchase.")
					% (_rows(named[name]), _name(name))
				)
	if problems:
		_refuse(
			problems,
			_("A Request for Quotation is raised from a Material Request:"),
			_(
				"Raise a Material Request of type Purchase, submit it, then create the Request for Quotation from it. "
				"A Request for Quotation without one can be allowed in SF Trading Settings > Purchasing > Procurement Flow."
			),
		)


def validate_supplier_quotation(doc, method=None):
	"""Supplier Quotation validate: every line answers a submitted Request for Quotation."""
	if doc.docstatus == 2 or allowed(ALLOW_QUOTATION):
		return
	named, without = _lines_naming(doc, "request_for_quotation")
	problems = []
	if without:
		problems.append(_("%s: no Request for Quotation.") % _rows(without))
	if named:
		found = {
			row.name: row
			for row in frappe.get_all(
				"Request for Quotation", filters={"name": ["in", sorted(named)]}, fields=["name", "docstatus"]
			)
		}
		for name in sorted(named):
			request = found.get(name)
			if not request:
				problems.append(_("%s: Request for Quotation %s does not exist.") % (_rows(named[name]), _name(name)))
			elif request.docstatus != 1:
				problems.append(_("%s: Request for Quotation %s is not submitted.") % (_rows(named[name]), _name(name)))
	if problems:
		_refuse(
			problems,
			_("A Supplier Quotation answers a Request for Quotation:"),
			_(
				"Create it from the submitted Request for Quotation (Create > Supplier Quotation). A Supplier Quotation "
				"without one can be allowed in SF Trading Settings > Purchasing > Procurement Flow."
			),
		)


def rfq_dashboard(data):
	"""override_doctype_dashboards: the Material Request in the Connections of a Request for Quotation.

	The request names its Material Request on each line, so the link is an internal one through the items, as the
	Supplier Quotation's own Connections do it for the request and the Material Request."""
	data = frappe._dict(data or {})
	data.setdefault("internal_links", {})["Material Request"] = ["items", "material_request"]
	transactions = data.setdefault("transactions", [])
	if not any("Material Request" in group.get("items", []) for group in transactions):
		transactions.append({"label": _("Reference"), "items": ["Material Request"]})
	return data
