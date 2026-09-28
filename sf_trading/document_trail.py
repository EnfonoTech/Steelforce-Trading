# sf_trading/document_trail.py
"""The whole Purchase or Sales chain, from any one link in it.

Frappe's own Connections tab is one hop aware: it reads the mapped-doc reference fields stored
ON THE DOCUMENT YOU ARE LOOKING AT, so a Purchase Invoice shows the Purchase Order and the
Purchase Receipt it names on its own item rows -- but a Purchase Order shows neither the Receipt
nor the Invoice raised two steps downstream, and a Purchase Receipt viewed on its own does not
surface the Invoice that was eventually billed against it. Accountants chasing "has this order
been received AND billed yet" end up opening three documents by hand, in whichever order the
Connections tab happens to expose them.

The two chains are symmetric (Purchase Order -> Purchase Receipt -> Purchase Invoice, Sales Order
-> Delivery Note -> Sales Invoice) and each hop's reference lives on a CHILD TABLE row, not the
parent: `purchase_order` / `purchase_receipt` on Purchase Invoice Item, `against_sales_order` on
Delivery Note Item, and so on. `_CHAIN` below is the one place that mapping is written down; the
walk itself does not care which chain it is running, only which fields point where.

One field is genuinely two-directional and easy to miss: core's own `update_billed_amount_based_on_po`
(see sf_trading/open_items.py's `po_bridge_maps` for the fuller story) fills Purchase Receipt
Item.purchase_invoice when an order is billed directly and the amount is later spread across a
separate receipt -- a receipt and an invoice that never named each other on a mapped-doc row, only
on this backfilled field. `_CHAIN` lists it as an ordinary outbound link on Purchase Receipt so it
is picked up the same way as every other reference, with no special-cased query.

The walk is a bounded breadth-first search, not open recursion: with three nodes per chain, two
hops from either end reaches the far end (order -> receipt is hop one, receipt -> invoice is hop
two), so `_MAX_HOPS = 2` is a real bound, not a guess, and the loop below cannot run away on a
site with more documents in the chain than expected. Every hop batches its lookups with a single
"in" filter per (child doctype, field) pair rather than querying per document.
"""

import frappe
from frappe import _
from frappe.utils import cstr, flt

_PURCHASE_CHAIN = ["Purchase Order", "Purchase Receipt", "Purchase Invoice"]
_SALES_CHAIN = ["Sales Order", "Delivery Note", "Sales Invoice"]

# doctype -> {
#   "chain": the ordered list of doctypes this one belongs to (its own family),
#   "item_doctype": the child table its rows live in,
#   "links": {fieldname on that child table -> the chain doctype it points to},
#   "party_field": the customer/supplier link field on the parent doctype,
#   "date_field": the parent doctype's own date field (naming differs: orders use
#     transaction_date, everything downstream uses posting_date),
# }
_CHAIN = {
	"Purchase Order": {
		"chain": _PURCHASE_CHAIN,
		"item_doctype": "Purchase Order Item",
		"links": {},
		"party_field": "supplier",
		"date_field": "transaction_date",
	},
	"Purchase Receipt": {
		"chain": _PURCHASE_CHAIN,
		"item_doctype": "Purchase Receipt Item",
		"links": {
			"purchase_order": "Purchase Order",
			# core's backfilled bridge field -- see the module docstring
			"purchase_invoice": "Purchase Invoice",
		},
		"party_field": "supplier",
		"date_field": "posting_date",
	},
	"Purchase Invoice": {
		"chain": _PURCHASE_CHAIN,
		"item_doctype": "Purchase Invoice Item",
		"links": {
			"purchase_order": "Purchase Order",
			"purchase_receipt": "Purchase Receipt",
		},
		"party_field": "supplier",
		"date_field": "posting_date",
	},
	"Sales Order": {
		"chain": _SALES_CHAIN,
		"item_doctype": "Sales Order Item",
		"links": {},
		"party_field": "customer",
		"date_field": "transaction_date",
	},
	"Delivery Note": {
		"chain": _SALES_CHAIN,
		"item_doctype": "Delivery Note Item",
		"links": {
			"against_sales_order": "Sales Order",
			# rare (a return/credit path can raise a DN against a posted invoice instead of an
			# order), but it is the same kind of reference and costs nothing extra to include
			"against_sales_invoice": "Sales Invoice",
		},
		"party_field": "customer",
		"date_field": "posting_date",
	},
	"Sales Invoice": {
		"chain": _SALES_CHAIN,
		"item_doctype": "Sales Invoice Item",
		"links": {
			"sales_order": "Sales Order",
			"delivery_note": "Delivery Note",
		},
		"party_field": "customer",
		"date_field": "posting_date",
	},
}

_MAX_HOPS = 2


def _outbound_neighbors(doctype, names, family):
	"""Chain documents THIS doctype's own item rows point to."""
	links = family[doctype]["links"]
	if not links or not names:
		return {}

	rows = frappe.db.get_all(
		family[doctype]["item_doctype"],
		filters={"parent": ["in", list(names)]},
		fields=list(links.keys()),
	)
	found = {}
	for row in rows:
		for field, target_doctype in links.items():
			value = row.get(field)
			if value:
				found.setdefault(target_doctype, set()).add(value)
	return found


def _inbound_neighbors(doctype, names, family):
	"""Other chain documents whose own item rows point BACK at this one."""
	if not names:
		return {}

	found = {}
	for other_doctype, entry in family.items():
		if other_doctype == doctype:
			continue
		for field, target_doctype in entry["links"].items():
			if target_doctype != doctype:
				continue
			rows = frappe.db.get_all(
				entry["item_doctype"],
				filters={field: ["in", list(names)]},
				fields=["parent"],
			)
			for row in rows:
				found.setdefault(other_doctype, set()).add(row["parent"])
	return found


def _document_summaries(doctype, entry, names):
	"""Display fields for a batch of documents, permission-filtered.

	frappe.get_list (not get_all): a document the caller cannot read is dropped from the result
	rather than named to them with no way to open it.
	"""
	if not names:
		return []

	party_field = entry["party_field"]
	party_name_field = party_field + "_name"
	fields = ["name", entry["date_field"], "status", "docstatus", "grand_total", party_field]
	if frappe.get_meta(doctype).has_field(party_name_field):
		fields.append(party_name_field)

	rows = frappe.get_list(doctype, filters={"name": ["in", list(names)]}, fields=fields)
	summaries = []
	for row in rows:
		summaries.append(
			{
				"name": row["name"],
				"date": row.get(entry["date_field"]),
				"status": row.get("status"),
				"docstatus": row.get("docstatus"),
				"grand_total": flt(row.get("grand_total")),
				"party": row.get(party_field),
				"party_name": row.get(party_name_field, row.get(party_field)),
			}
		)
	summaries.sort(key=lambda d: d["name"])
	return summaries


@frappe.whitelist()
def get_document_trail(doctype: str, docname: str) -> dict:
	"""The full Purchase or Sales chain a document belongs to, upstream and downstream.

	Returns {"chain": [doctype, doctype, doctype], "root": {...}, "documents": {doctype: [rows]}}
	-- every doctype in the same family as `doctype`, each with the documents found in that
	family's chain (a document with no relatives at all still returns itself).
	"""
	doctype = cstr(doctype)
	docname = cstr(docname)

	family_entry = _CHAIN.get(doctype)
	if not family_entry:
		frappe.throw(_("Document Trail does not cover this document type: {0}") % doctype)
	if not docname or not frappe.db.exists(doctype, docname):
		frappe.throw(_("{0} {1} was not found") % (_(doctype), docname))

	frappe.has_permission(doctype, "read", doc=docname, throw=True)

	family = {dt: _CHAIN[dt] for dt in family_entry["chain"]}
	nodes = {dt: set() for dt in family}
	nodes[doctype].add(docname)
	frontier = {doctype: {docname}}

	for _hop in range(_MAX_HOPS):
		discovered = {}
		for dt, names in frontier.items():
			for target_dt, found in _outbound_neighbors(dt, names, family).items():
				discovered.setdefault(target_dt, set()).update(found)
			for target_dt, found in _inbound_neighbors(dt, names, family).items():
				discovered.setdefault(target_dt, set()).update(found)

		next_frontier = {}
		for dt, found in discovered.items():
			fresh = found - nodes[dt]
			if fresh:
				nodes[dt] |= fresh
				next_frontier[dt] = fresh
		if not next_frontier:
			break
		frontier = next_frontier

	documents = {
		dt: _document_summaries(dt, family[dt], sorted(names))
		for dt, names in nodes.items()
		if names
	}
	return {"chain": family_entry["chain"], "root": {"doctype": doctype, "name": docname}, "documents": documents}
