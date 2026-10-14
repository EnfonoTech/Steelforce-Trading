# sf_trading/strict_stock_reservation.py
"""Strict Stock Reservation, switched per Company and per Warehouse.

The client (BRD "Strict Stock Reservation Control"): stock reserved for a submitted Sales Order
belongs to that order; every other order, invoice, delivery, stock entry or transfer may use only
the unreserved quantity of that item in that warehouse.

ERPNext v15 already enforces exactly that once a Stock Reservation Entry exists: the stock ledger
subtracts every open reservation of the item+warehouse from what any other voucher may take
(stock_ledger.update_entries_after.validate_negative_stock / validate_reserved_stock), and a
Delivery Note or stock-updating Sales Invoice against the owning order consumes that order's own
reservation first (SellingController.update_stock_reservation_entries). What it lacks is the scope:
reservation is one switch in Stock Settings for every company and warehouse, and reserving is opt-in
per order.

So this module decides only WHERE reservations exist, and leaves the enforcement to core:

  * Company `custom_enforce_strict_stock_reservation` and Warehouse `custom_stock_reservation_control`
    (Use Company Setting / Enabled / Disabled; an explicit warehouse value wins).
  * A Sales Order reserves its strict-warehouse rows on submit by itself; rows in other warehouses
    are never reserved.
  * No Stock Reservation Entry can be made in a warehouse that is not strict -- whether from the
    Sales Order's Reserve button, a Pick List or a Purchase Receipt. A warehouse without
    reservations is ordinary ERPNext.
  * The core Stock Settings switch must be on for any of it to run, and a company or warehouse
    cannot be switched off while it still holds open reservations (they would go on blocking stock
    with nobody able to see why).
"""

from __future__ import annotations

import frappe
from frappe import _
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields
from frappe.utils import cint

COMPANY_FIELD = "custom_enforce_strict_stock_reservation"
WAREHOUSE_FIELD = "custom_stock_reservation_control"
USE_COMPANY = "Use Company Setting"
ENABLED = "Enabled"
DISABLED = "Disabled"


def ensure_custom_fields():
	create_custom_fields(
		{
			"Company": [
				{
					"fieldname": COMPANY_FIELD,
					"label": "Enforce Strict Stock Reservation",
					"fieldtype": "Check",
					"default": "0",
					"insert_after": "enable_perpetual_inventory",
					"description": "A submitted Sales Order reserves its stock, and no other document may "
					"use that reserved stock. Each Warehouse can follow this or override it.",
				}
			],
			"Warehouse": [
				{
					"fieldname": WAREHOUSE_FIELD,
					"label": "Stock Reservation Control",
					"fieldtype": "Select",
					"options": "\n".join((USE_COMPANY, ENABLED, DISABLED)),
					"default": USE_COMPANY,
					"insert_after": "company",
					"description": "Strict Stock Reservation for this warehouse. Use Company Setting follows "
					"the Company's Enforce Strict Stock Reservation.",
				}
			],
		},
		update=True,
	)


# ── the switch ────────────────────────────────────────────────────────────────


def stock_reservation_enabled() -> bool:
	return bool(cint(frappe.db.get_single_value("Stock Settings", "enable_stock_reservation")))


def is_strict(warehouse: str | None) -> bool:
	"""Whether Strict Stock Reservation applies to this warehouse: its own Enabled / Disabled wins,
	otherwise its company's setting."""
	if not warehouse or not stock_reservation_enabled():
		return False

	control, company = frappe.get_cached_value("Warehouse", warehouse, [WAREHOUSE_FIELD, "company"]) or (
		None,
		None,
	)
	if control == ENABLED:
		return True
	if control == DISABLED:
		return False
	return bool(company and cint(frappe.get_cached_value("Company", company, COMPANY_FIELD)))


# ── Sales Order ───────────────────────────────────────────────────────────────


def reserve_strict_rows_on_submit(doc, _method=None):
	"""Sales Order before_submit: rows in a strict warehouse are reserved by core's own on_submit
	(it reads these flags); rows anywhere else are not."""
	if not stock_reservation_enabled():
		return

	any_strict = False
	for item in doc.items:
		# only stock is reserved; a service row is never reserved, wherever it sits
		strict = bool(
			frappe.get_cached_value("Item", item.item_code, "is_stock_item") and is_strict(item.warehouse)
		)
		item.reserve_stock = 1 if strict else 0
		any_strict = any_strict or strict

	# core reserves on submit only when the header is ticked; tick it for a strict row, never
	# untick a user's own tick (its rows are already decided above)
	if any_strict:
		doc.reserve_stock = 1


# ── Stock Reservation Entry ───────────────────────────────────────────────────


def validate_reservation_warehouse(doc, _method=None):
	"""Stock Reservation Entry validate: a reservation only where Strict Stock Reservation applies,
	however it is being made (Sales Order Reserve button, Pick List, Purchase Receipt)."""
	if doc.docstatus == 2 or is_strict(doc.warehouse):
		return

	frappe.throw(
		_("Stock cannot be reserved in Warehouse {0}: Strict Stock Reservation is not enabled for it "
		  "(Warehouse: Stock Reservation Control, or Company: Enforce Strict Stock Reservation).").format(
			frappe.bold(doc.warehouse)
		),
		title=_("Stock Reservation Not Enabled"),
	)


# ── switching on and off ──────────────────────────────────────────────────────


def _require_core_switch(label):
	if not stock_reservation_enabled():
		frappe.throw(
			_("Turn on Enable Stock Reservation in Stock Settings (Stock Reservation tab) before enabling {0}.").format(
				label
			),
			title=_("Stock Reservation Disabled"),
		)


def _open_reservations(warehouses) -> int:
	if not warehouses:
		return 0
	return frappe.db.count(
		"Stock Reservation Entry",
		{"docstatus": 1, "status": ["not in", ["Delivered", "Cancelled"]], "warehouse": ["in", list(warehouses)]},
	)


def _refuse_while_reserved(warehouses, what):
	count = _open_reservations(warehouses)
	if count:
		frappe.throw(
			_("{0} cannot be switched off: {1} open Stock Reservation Entries still hold stock there. "
			  "Deliver or unreserve them first.").format(what, count),
			title=_("Open Stock Reservations"),
		)


def validate_company(doc, _method=None):
	"""Company validate."""
	if doc.is_new():
		return

	before = doc.get_doc_before_save()
	was_on = bool(before and cint(before.get(COMPANY_FIELD)))
	is_on = bool(cint(doc.get(COMPANY_FIELD)))

	if is_on and not was_on:
		_require_core_switch(_("Enforce Strict Stock Reservation"))
	elif was_on and not is_on:
		# only the warehouses that were following the company lose their strictness
		following = frappe.get_all(
			"Warehouse",
			filters={"company": doc.name, WAREHOUSE_FIELD: ["in", [USE_COMPANY, "", None]]},
			pluck="name",
		)
		_refuse_while_reserved(following, _("Enforce Strict Stock Reservation on Company {0}").format(doc.name))


def validate_warehouse(doc, _method=None):
	"""Warehouse validate."""
	if doc.is_new() or not doc.has_value_changed(WAREHOUSE_FIELD):
		return

	before = doc.get_doc_before_save()
	was_strict = _resolve(before)
	now_strict = _resolve(doc)

	if now_strict and not was_strict:
		_require_core_switch(_("Strict Stock Reservation on Warehouse {0}").format(doc.name))
	elif was_strict and not now_strict:
		_refuse_while_reserved([doc.name], _("Strict Stock Reservation on Warehouse {0}").format(doc.name))


def _resolve(warehouse_doc) -> bool:
	"""is_strict for an in-memory Warehouse, without the cache or the core switch."""
	if not warehouse_doc:
		return False
	control = warehouse_doc.get(WAREHOUSE_FIELD)
	if control == ENABLED:
		return True
	if control == DISABLED:
		return False
	company = warehouse_doc.get("company")
	return bool(company and cint(frappe.get_cached_value("Company", company, COMPANY_FIELD)))
