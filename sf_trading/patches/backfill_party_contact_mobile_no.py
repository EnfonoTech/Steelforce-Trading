# sf_trading/patches/backfill_party_contact_mobile_no.py
"""One-time backfill for GS Issue 11's cached phone field.

sync_from_contact/sync_from_address (party_contact_cache.py) only fire going forward, on the next
save of a linked Contact/Address -- they cannot retroactively fill custom_mobile_no for a party
whose Contact/Address hasn't been touched since this feature shipped. Confirmed live on sft-uat,
2026-09-23: 0 of 7,858 Customers had a non-blank cache, and the sampled rows carried 0 Contacts
each -- the real data lives on Address.phone. Two bulk queries per doctype, not a per-party loop,
and only the parties that actually have a phone get a write.
"""

from __future__ import annotations

import frappe

from sf_trading.party_contact_cache import CACHE_FIELD, PARTY_DOCTYPES, ensure_custom_fields


def execute():
	# idempotent regardless of patch-section-vs-fixture-sync ordering -- see the account's own
	# "patches run before after_migrate" trap note
	ensure_custom_fields()

	for doctype in PARTY_DOCTYPES:
		phone_by_party: dict[str, str] = {}

		contact_rows = frappe.db.sql(
			"""
			SELECT dl.link_name AS party, cp.phone AS phone
			FROM `tabDynamic Link` dl
			INNER JOIN `tabContact Phone` cp ON cp.parent = dl.parent
			WHERE dl.parenttype = 'Contact'
				AND dl.link_doctype = %s
				AND COALESCE(cp.phone, '') != ''
			ORDER BY dl.link_name, cp.idx
			""",
			(doctype,),
			as_dict=True,
		)
		for row in contact_rows:
			phone_by_party.setdefault(row.party, row.phone.strip())

		address_rows = frappe.db.sql(
			"""
			SELECT dl.link_name AS party, addr.phone AS phone
			FROM `tabDynamic Link` dl
			INNER JOIN `tabAddress` addr ON addr.name = dl.parent
			WHERE dl.parenttype = 'Address'
				AND dl.link_doctype = %s
				AND COALESCE(addr.phone, '') != ''
			ORDER BY dl.link_name, addr.creation
			""",
			(doctype,),
			as_dict=True,
		)
		for row in address_rows:
			phone_by_party.setdefault(row.party, row.phone.strip())

		if not phone_by_party:
			continue

		existing = frappe.get_all(
			doctype,
			filters={"name": ["in", list(phone_by_party.keys())]},
			fields=["name", CACHE_FIELD],
		)
		current = {row.name: row.get(CACHE_FIELD) for row in existing}

		updated = 0
		for party, phone in phone_by_party.items():
			if current.get(party) == phone:
				continue
			frappe.db.set_value(doctype, party, CACHE_FIELD, phone, update_modified=False)
			updated += 1
			if updated % 500 == 0:
				frappe.db.commit()

		frappe.db.commit()
