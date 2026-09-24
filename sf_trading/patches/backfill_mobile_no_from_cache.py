# sf_trading/patches/backfill_mobile_no_from_cache.py
"""One-time backfill: fill core's own `mobile_no` from the G11 phone cache (`custom_mobile_no`)
for every existing Customer/Supplier where mobile_no is currently blank.

party_contact_cache.fill_mobile_no_from_cache only fires going forward, on the next save of each
party -- it cannot retroactively fill mobile_no for parties nobody has re-saved since this shipped.
Confirmed live on prod, 2026-09-24: 6,652 Customers already carry a real number in
custom_mobile_no but have an empty mobile_no. That gap is what let a client-added Property Setter
(Customer-mobile_no-reqd, live on prod only, added 2026-09-20 by the client's own accountant via
Customize Form, never exported to a fixture) start refusing every save on those exact records with
"Mobile No is required", even though the number was already on file.

A single static column-to-column UPDATE per doctype, not a per-party loop or an IN-list filter --
no sqlparse token-cap risk at all (see sf_trading/query.py's own docstring on that trap), since
there is no IN-list here to begin with.
"""

from __future__ import annotations

import frappe

from sf_trading.party_contact_cache import ensure_custom_fields


def execute():
	# idempotent regardless of patch-section-vs-fixture-sync ordering -- see the account's own
	# "patches run before after_migrate" trap note
	ensure_custom_fields()

	frappe.db.sql(
		"""
		UPDATE `tabCustomer`
		SET mobile_no = custom_mobile_no
		WHERE (mobile_no IS NULL OR mobile_no = '')
		AND custom_mobile_no IS NOT NULL AND custom_mobile_no != ''
		"""
	)
	frappe.db.sql(
		"""
		UPDATE `tabSupplier`
		SET mobile_no = custom_mobile_no
		WHERE (mobile_no IS NULL OR mobile_no = '')
		AND custom_mobile_no IS NOT NULL AND custom_mobile_no != ''
		"""
	)
	frappe.db.commit()
