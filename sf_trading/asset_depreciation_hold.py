# sf_trading/asset_depreciation_hold.py
"""An Asset bought and submitted now, whose depreciation starts later: Depreciation On Hold.

The purchase of an asset is booked when it is bought, but it is often depreciated only from the day
it is put to use, which nobody knows yet when the purchase is entered. Core has no answer for that
once the Asset is submitted: "Calculate Depreciation", the finance books and the Available-for-use
Date are all frozen at submit, and an Asset Depreciation Schedule refuses to submit for an Asset that
does not calculate depreciation. An Asset submitted without depreciation could never get it.

So:

* `custom_depreciation_on_hold` on the Asset, offered while Calculate Depreciation is off. A
  submitted Asset that carries it shows "On Hold" in the list and on the form. Its stored status
  stays core's own ("Submitted"): core gates cancel, Transfer / Scrap / Sell and the Sales Invoice
  asset picker on a fixed list of statuses, and an "On Hold" status would lock the Asset out of all
  of them.
* Start Depreciation, on an On Hold Asset (`start_depreciation`): the Available-for-use Date and the
  depreciation details are entered then, and the Asset gets an active schedule exactly as core gives
  one on submit -- core's own validations, core's own schedule. From there core posts the entries.
* the report Assets Pending Depreciation lists what is on hold, and what is scheduled but has not
  had its first depreciation yet.
"""

import frappe
from frappe import _
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields
from frappe.utils import cint, flt, get_last_day, getdate, nowdate

FIELD = "custom_depreciation_on_hold"
ON_HOLD = "On Hold"
#: what core calls an Asset on hold: "Partially Depreciated" is an existing Asset brought in with
#: opening depreciation
HOLD_STATUSES = ("Submitted", "Partially Depreciated")


def ensure_custom_fields():
	"""after_migrate: the On Hold tick on the Asset."""
	create_custom_fields(
		{
			"Asset": [
				{
					"fieldname": FIELD,
					"label": "Depreciation On Hold",
					"fieldtype": "Check",
					"insert_after": "calculate_depreciation",
					# a tracking-only Asset (asset_tracking_item.py) is never depreciated at all
					"depends_on": "eval:!doc.calculate_depreciation && !doc.custom_source_row",
					"description": "Book the purchase now and start depreciation later, with Start Depreciation on the submitted Asset.",
				}
			]
		},
		ignore_validate=True,
	)


def require_depreciation_choice(doc):
	"""On submit: a fixed asset says when it depreciates -- now (Calculate Depreciation) or later (On Hold).

	Submit, not save: an Item with Auto Create Assets has its draft Asset raised by the Purchase Invoice
	or Receipt with neither ticked, and a save-time rule would refuse the purchase itself. Not asked of
	an Asset that has nothing left to depreciate (fully depreciated, or a non-depreciable category such
	as land). A tracking-only Asset (asset_tracking_item.py) never reaches here -- see asset_class.py."""
	if doc.calculate_depreciation or doc.get(FIELD) or doc.is_fully_depreciated:
		return
	if doc.asset_category and frappe.get_cached_value("Asset Category", doc.asset_category, "non_depreciable_category"):
		return
	frappe.throw(
		_("Tick either Calculate Depreciation (depreciation starts with this Asset) or Depreciation On Hold "
			"(it starts later, with Start Depreciation) before submitting."),
		title=_("Depreciation Not Chosen"),
	)


def is_on_hold(doc) -> bool:
	"""A submitted Asset whose depreciation is on hold and has not been started."""
	return bool(doc.docstatus == 1 and doc.get(FIELD) and not doc.calculate_depreciation
		and doc.status in HOLD_STATUSES)


@frappe.whitelist()
def get_depreciation_defaults(asset):
	"""The Start Depreciation dialog's first values: the Asset Category's own depreciation details."""
	from erpnext.assets.doctype.asset.asset import get_item_details

	doc = frappe.get_doc("Asset", asset)
	doc.check_permission("read")
	rows = get_item_details(doc.item_code, doc.asset_category, doc.gross_purchase_amount) or [{}]
	row = rows[0]
	return {
		"available_for_use_date": nowdate(),
		"depreciation_start_date": get_last_day(nowdate()),
		"depreciation_method": row.get("depreciation_method") or "Straight Line",
		"total_number_of_depreciations": row.get("total_number_of_depreciations"),
		"frequency_of_depreciation": row.get("frequency_of_depreciation") or 1,
		"finance_book": row.get("finance_book"),
	}


@frappe.whitelist()
def start_depreciation(asset, available_for_use_date, depreciation_start_date, depreciation_method,
                       total_number_of_depreciations, frequency_of_depreciation,
                       expected_value_after_useful_life=0, finance_book=None):
	"""Turn depreciation on for a submitted On Hold Asset and give it its active schedule."""
	from erpnext.assets.doctype.asset.asset import add_asset_activity
	from erpnext.assets.doctype.asset_depreciation_schedule.asset_depreciation_schedule import (
		convert_draft_asset_depr_schedules_into_active,
		make_draft_asset_depr_schedules,
	)

	doc = frappe.get_doc("Asset", asset)
	doc.check_permission("submit")
	if not is_on_hold(doc):
		frappe.throw(_("Depreciation can be started only on a submitted Asset that is On Hold."))
	# a depreciation entry made by hand has already moved the value: a schedule from the full
	# value would depreciate it twice
	if flt(doc.value_after_depreciation, 3) != flt(
		flt(doc.gross_purchase_amount) - flt(doc.opening_accumulated_depreciation), 3
	):
		frappe.throw(_("Asset {0} already has depreciation booked by hand.").format(doc.name))
	# an Asset that booked its own GL (CWIP accounting) booked it on its Available-for-use Date
	if (
		doc.available_for_use_date
		and getdate(available_for_use_date) != getdate(doc.available_for_use_date)
		and frappe.db.exists("GL Entry", {"voucher_type": "Asset", "voucher_no": doc.name, "is_cancelled": 0})
	):
		frappe.throw(_("Asset {0} booked its value on {1}; the Available-for-use Date must stay {1}.").format(
			doc.name, frappe.format(doc.available_for_use_date, "Date")))

	doc.calculate_depreciation = 1
	doc.set(FIELD, 0)
	doc.available_for_use_date = getdate(available_for_use_date)
	doc.set("finance_books", [])
	doc.append("finance_books", {
		"finance_book": finance_book,
		"depreciation_method": depreciation_method,
		# the dialog sends every value as text
		"total_number_of_depreciations": cint(total_number_of_depreciations),
		"frequency_of_depreciation": cint(frequency_of_depreciation),
		"depreciation_start_date": getdate(depreciation_start_date),
		"expected_value_after_useful_life": flt(expected_value_after_useful_life),
	})

	# what core's validate and on_submit check before a schedule is made; saving a submitted
	# document runs none of them
	doc.validate_asset_values()
	doc.validate_category()
	doc.validate_finance_books()
	doc.prepare_depreciation_data()
	doc.validate_in_use_date()
	doc.validate_expected_value_after_useful_life()

	doc.flags.ignore_validate_update_after_submit = True
	doc.save()
	make_draft_asset_depr_schedules(doc)
	convert_draft_asset_depr_schedules_into_active(doc)
	doc.reload()
	doc.set_status()
	add_asset_activity(doc.name, _("Depreciation started, available for use from {0}").format(
		frappe.format(doc.available_for_use_date, "Date")))
	return doc.status
