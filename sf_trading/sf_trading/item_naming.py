# sf_trading/item_naming.py
"""Item naming, split by whether the item is stock-tracked (GS Issue 14).

The client's ask reads like two rules, but only one of them needs new code: a stock-tracked item
must keep its current MANUAL code entry (Item Naming By = "Item Code" in Stock Settings already
gives that, unchanged by this file), and only a non-stock / service item must auto-generate a code
that shows its category.

Item's own naming is a single, bench-wide Stock Settings choice, not a per-item switch -- so this
cannot be done with a Property Setter alone. Frappe calls ``doc.run_method("autoname")``
unconditionally, for every Item, before it ever looks at Stock Settings (frappe/model/naming.py
``set_new_name``); a hooked ``autoname`` that sets ``doc.name`` for the non-stock case and simply
returns without setting it for the stock case lets Stock Settings' existing (already-correct)
manual-entry behaviour take over exactly as it does today. No change reaches stock items at all.
"""

from __future__ import annotations

import frappe
from frappe.model.naming import make_autoname

#: naming series template per non-stock Item Group. Extend as new service categories appear;
#: an Item Group not listed here falls back to a generic SVC- series rather than erroring, so a new
#: category never blocks item creation while waiting for this map to be updated.
_SERVICE_GROUP_SERIES = {
	"Services": "SVC-.#####",
	"Freight and Forwarding Charges": "SVC-FRT-.#####",
}
_DEFAULT_SERVICE_SERIES = "SVC-GEN-.#####"


def autoname(doc, _method=None):
	"""Item autoname hook: name non-stock items ourselves, leave stock items to Stock Settings."""
	if doc.get("is_stock_item"):
		# Mis-set is_stock_item on an item that should be a service is a data-entry error the
		# naming hook cannot fix -- validate() below is where that gets caught instead.
		return

	series = _SERVICE_GROUP_SERIES.get(doc.get("item_group"), _DEFAULT_SERVICE_SERIES)
	doc.name = make_autoname(series, doc.doctype, doc=doc)
