# sf_trading/asset_tracking_item.py
"""A courtesy Asset record for items that are bought expensed, not capitalized.

Some items -- a laptop, a phone -- are bought and booked straight to an expense account, the
normal way, with `Item.is_fixed_asset` left unticked. The accounting is already finished the
moment the Purchase Receipt/Invoice posts. What is missing is Asset Movement: nobody can later
answer "who has this laptop" or "which branch is this phone sitting in" the way they can for a
real Asset, because nothing Asset-shaped exists for it.

`Item.custom_track_as_asset` (fixtures/custom_field.json) asks for exactly that: create the
Asset record purely so Asset Movement, location and custodian history exist for it, with **no**
depreciation and **no** GL impact of its own -- the GL entry already happened on the purchase's
own expense account, and posting a second one here would double it. The field's `depends_on`
keeps it off an `is_fixed_asset` item in the UI (that item already gets the native, capitalized
Asset flow) and off a stock item too (see below); `_should_track` below re-checks both server-side,
because a depends_on is cosmetic -- data imported or edited another way can still carry the flags,
and firing this module for an already-fixed-asset row would hand it a second, redundant Asset next
to the one core ERPNext already creates for it.

**Not offered to a stock item either, and for a harder reason than cosmetics.**
`Asset.validate_item()` (erpnext/assets/doctype/asset/asset.py, ~line 273, corpus-checked
2026-09-28) throws "Item {0} must be a non-stock item" whenever `Item.is_stock_item` is truthy --
on *every* save, not only the first. `ignore_validate` (below) only shields our own insert; the
very first time staff open the resulting draft to add a custodian and save it normally, this
throw would leave the record permanently un-savable. Excluding a stock item at the source --
`custom_track_as_asset`'s `depends_on` and `_should_track` both -- is the only way to avoid
handing staff a draft that can never be finished.

**Why `flags.ignore_validate`, not a narrower workaround.** `Asset.validate()` (see
`erpnext/assets/doctype/asset/asset.py`, tridz-dev/erpnext mirror, corpus-checked 2026-09-28)
runs two checks that both refuse the record this module has to create:

  * `validate_item()` (~line 234): refuses when the Item is not `is_fixed_asset`. The whole
    point here is an Item that is *not* a fixed asset, so this always fires.
  * `validate_asset_and_reference()` (~line 213): refuses `is_existing_asset=1` together with a
    populated `purchase_invoice` -- "Purchase Invoice cannot be made against an existing asset".
    We need `is_existing_asset=1` (see below), which collides with linking the Asset back to its
    Purchase Invoice through the core field.

`doc.flags.ignore_validate` (an established Frappe pattern -- see e.g.
`frappe/modules/import_file.py`, `frappe/installer.py`) skips the controller's whole `validate`
method for that one call, so both gates above are bypassed for our own insert. Core mandatory-
field and link checks are a separate code path and still run -- `ignore_validate` does not turn
those off, only the doctype's own `validate()`.

**Why the Purchase Invoice link lives in a custom field, not the core one.** Because that second
throw above is not a one-time gate -- it is `Asset.validate()`, so it fires on *every* save, not
only when the record is first created. `ignore_validate` only covers the one call this module
makes; the very next time a person opens the resulting draft and saves it (to fill in location or
custodian) or submits it, `validate()` runs for real and would throw on exactly the state we just
created. So a Purchase-Invoice-sourced tracking Asset never gets the core `purchase_invoice`
field populated at all -- it goes on `custom_source_purchase_invoice` instead, a plain Link field
carrying no such gate, kept for traceability only. A Purchase-Receipt-sourced one *can* use the
real `purchase_receipt` field: that check only ever looks at `self.purchase_invoice`, never
`self.purchase_receipt`, and using the core field there gives a normal "Assets" entry in the
Purchase Receipt's own Connections/dashboard for free.

**Idempotency is per purchase row, not per document.** A document can carry more than one row for
the same Item (split across warehouses/rates), and this business's normal flow often receives
stock on a Purchase Receipt and bills it later on a Purchase Invoice that references that same PR
line (see sf_trading/sbnd.py and sf_trading/api/purchase_return.py for the same PR<->PI row
linkage relied on here). Both `on_submit` hooks genuinely fire, once each, for that one physical
purchase, and a guard keyed only on the parent voucher would not stop the second one from
creating a duplicate Asset for a row the first one already tracked. So every tracking Asset
stamps the exact source child-row name onto `custom_source_row`, and before creating one we check:

  1. this row hasn't already produced one (a resubmitted/replayed document), and
  2. the row on the *other side* of a receipt<->invoice pair hasn't already produced one for this
     same physical line, via the `pr_detail` / `purchase_invoice_item` back-links core ERPNext
     already carries on the row -- the same fields sf_trading.api.purchase_return already relies
     on for the equivalent PR<->PI matching problem there.

**Left as a draft (docstatus 0), on purpose.** This is a courtesy side effect, not part of the
purchase transaction: creating it never blocks or fails the Purchase Receipt/Invoice submit that
triggered it (wrapped in try/except per row, logged via frappe.log_error rather than raised).

**Location, tried in three steps, none of them invented.** Location is mandatory on core Asset,
and mandatory-field enforcement lives in `frappe.model.document.Document._validate()` -- a
separate call `insert()` always makes alongside (not instead of) the controller's own `validate()`,
so `ignore_validate` does not excuse a blank one. `_resolve_location` tries:

  1. The row's own `asset_location` -- a field core ERPNext already carries on both
     `Purchase Receipt Item` and `Purchase Invoice Item` for exactly this purpose (it is what
     `erpnext.controllers.buying_controller.make_asset` reads for a real fixed-asset row, and
     that flow throws its own "Enter location for the asset item" when it is blank). Core hides
     it behind `depends_on: "is_fixed_asset"`; a Property Setter (fixtures/property_setter.json)
     widens that to `eval:doc.is_fixed_asset || doc.custom_track_as_asset`, reading a row-level
     `custom_track_as_asset` mirror (fixtures/custom_field.json, fetched the way core mirrors
     `is_fixed_asset` onto the same row) -- so a purchaser can name the exact Location at the
     point of purchase, no separate configuration required.
  2. A Warehouse-side mapping, for a row nobody filled in by hand. Nothing in this app (or core
     Warehouse) defines one today, so this lookup is a no-op everywhere -- written as a real
     lookup rather than removed, so a future mapping is picked up without touching this file.
  3. **SF Trading Settings -> Default Tracking Asset Location** -- a real Location an admin
     points this at once, org-wide, not a value this module guesses.

Only if all three are empty does the row's insert fail (caught by the try/except below, logged,
not raised) into the error log for staff to finish by hand -- create/assign a Location, then raise
the Asset themselves.
"""

import frappe
from frappe import _
from frappe.utils import cint, flt

# Item.custom_track_as_asset: fixtures/custom_field.json
TRACK_FIELD = "custom_track_as_asset"

# Asset.custom_source_purchase_invoice / Asset.custom_source_row: fixtures/custom_field.json
SOURCE_PI_FIELD = "custom_source_purchase_invoice"
SOURCE_ROW_FIELD = "custom_source_row"


def create_tracking_asset(doc, method=None):
	"""on_submit for Purchase Receipt and Purchase Invoice.

	Courtesy side effect only: a bad row logs and moves on rather than aborting the submit that
	is already in flight.
	"""
	for row in doc.get("items") or []:
		try:
			_create_for_row(doc, row)
		except Exception:
			title = "Asset tracking creation failed for {0} {1}, row {2}".format(
				doc.doctype, doc.name, row.idx
			)
			frappe.log_error(frappe.get_traceback(), _(title))


def _create_for_row(doc, row):
	if not row.item_code:
		return

	item = frappe.db.get_value(
		"Item",
		row.item_code,
		["is_fixed_asset", "is_stock_item", TRACK_FIELD, "item_name"],
		as_dict=True,
	)
	if not item or not _should_track(item):
		return

	if _already_tracked(doc, row):
		return

	asset = frappe.new_doc("Asset")
	asset.item_code = row.item_code
	asset.asset_name = row.item_name or item.item_name or row.item_code
	asset.company = doc.company
	asset.location = _resolve_location(row)
	asset.purchase_date = doc.posting_date
	asset.asset_quantity = cint(row.qty) or 1
	asset.gross_purchase_amount = flt(row.base_amount)
	asset.purchase_amount = flt(row.base_amount)

	# Not a fresh capitalization: the purchase's own expense account already carries the GL
	# impact, and this Asset must post none of its own.
	asset.is_existing_asset = 1
	asset.calculate_depreciation = 0

	if doc.doctype == "Purchase Receipt":
		asset.purchase_receipt = doc.name
	else:
		# see module docstring: never the core purchase_invoice field, it and
		# is_existing_asset=1 throw together on every save, not only at insert.
		asset.set(SOURCE_PI_FIELD, doc.name)

	asset.set(SOURCE_ROW_FIELD, row.name)

	# Bypasses Asset.validate() for this insert only -- see module docstring for exactly which
	# two checks that skips and why both have to be skipped.
	asset.flags.ignore_validate = True
	asset.insert(ignore_permissions=True)


def _should_track(item) -> bool:
	# Defends the mutual exclusivity the custom field's depends_on only enforces cosmetically:
	# a fixed-asset item already gets a real, capitalized Asset from core ERPNext, and firing
	# this module for it too would hand it a second, redundant one.
	if cint(item.is_fixed_asset):
		return False
	# A stock item's Asset would refuse to save ever again (validate_item(), every save, not
	# just this insert) -- see module docstring. Excluded here, not only in the field's UI.
	if cint(item.is_stock_item):
		return False
	return bool(cint(item.get(TRACK_FIELD)))


def _already_tracked(doc, row) -> bool:
	"""True when a tracking Asset already exists for this exact purchase row.

	Checks the row's own document, and -- because a receipt and the invoice billing it both
	fire this hook for what is one physical purchase -- the row it links to on the other side of
	a Purchase Receipt <-> Purchase Invoice pair (see module docstring).
	"""
	row_names = {row.name}

	if doc.doctype == "Purchase Invoice" and row.get("purchase_receipt") and row.get("pr_detail"):
		row_names.add(row.pr_detail)
	elif doc.doctype == "Purchase Receipt" and row.get("purchase_invoice_item"):
		row_names.add(row.purchase_invoice_item)

	return bool(frappe.db.exists("Asset", {SOURCE_ROW_FIELD: ["in", list(row_names)]}))


def _resolve_location(row):
	"""The row's own Asset Location, a Warehouse mapping, the configured default, or blank.

	Three steps, in order -- see module docstring for why each exists:

	  1. `row.asset_location` -- core's own field on this exact row, revealed for a tracked row
	     by a Property Setter (fixtures/property_setter.json), so a purchaser can name the exact
	     Location at the point of purchase.
	  2. A Warehouse-side mapping. Nothing in this app (or core Warehouse) defines one today, so
	     this lookup currently always misses -- kept as a real lookup, not removed, so a future
	     mapping is picked up without touching this file.
	  3. SF Trading Settings' own default (an admin-chosen, real Location), org-wide.

	Only if all three are empty does this return None and leave the row for staff to finish by
	hand -- see module docstring.
	"""
	direct = row.get("asset_location")
	if direct:
		return direct

	warehouse = row.get("warehouse")
	if warehouse and frappe.get_meta("Warehouse").has_field("custom_asset_location"):
		mapped = frappe.db.get_value("Warehouse", warehouse, "custom_asset_location")
		if mapped:
			return mapped

	return frappe.db.get_single_value("SF Trading Settings", "default_asset_location")
