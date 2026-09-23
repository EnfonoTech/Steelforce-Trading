# sf_trading/party_contact_cache.py
"""Cache the party's own phone onto Customer/Supplier list views (GS Issue 11).

Checks two sources, Contact first: this bench's real data does not agree with a Contact-only
reading. Live check on sft-uat, 2026-09-23: 0 of 7,858 Customers had a non-blank cache, and every
sampled row carried 0 linked Contacts -- the phone was on the linked Address's own `phone` field
instead, exactly the "copied manually out of the address" the client's own bug report named.
Hooking Contact's and Address's own on_update/on_trash (never Customer's/Supplier's after_insert,
which cannot see either one before core has created it -- see party_completeness.py's module
docstring for the same trap) walks back through the Dynamic Link table to every party either one
is linked to and refreshes a cached field there.

Also used by sales_order_governance.py's contact-completeness checks (GS Issues 13/20), so both the
list-view fix and the billing-time gate always agree on what "this party's phone(s)" means.
"""

from __future__ import annotations

import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields

CACHE_FIELD = "custom_mobile_no"
PARTY_DOCTYPES = ("Customer", "Supplier")


def ensure_custom_fields():
	"""after_migrate: the cached phone field on Customer and Supplier."""
	create_custom_fields(
		{
			"Customer": [
				{
					"fieldname": CACHE_FIELD,
					"label": "Mobile No",
					"fieldtype": "Data",
					"insert_after": "customer_name",
					"read_only": 1,
					"in_list_view": 1,
					"in_standard_filter": 1,
					"no_copy": 1,
					"description": "Kept in sync from this customer's linked Contact/Address. Edit those, not this field.",
				}
			],
			"Supplier": [
				{
					"fieldname": CACHE_FIELD,
					"label": "Mobile No",
					"fieldtype": "Data",
					"insert_after": "supplier_name",
					"read_only": 1,
					"in_list_view": 1,
					"in_standard_filter": 1,
					"no_copy": 1,
					"description": "Kept in sync from this supplier's linked Contact/Address. Edit those, not this field.",
				}
			],
		},
		ignore_validate=True,
		update=True,
	)


def _phone_numbers(
	party_doctype: str,
	party_name: str,
	*,
	exclude_contact: str | None = None,
	exclude_address: str | None = None,
) -> list[str]:
	"""Contact phone_nos, then Address.phone, de-duplicated, Contact numbers first."""
	numbers = []

	contacts = frappe.get_all(
		"Dynamic Link",
		filters={"parenttype": "Contact", "link_doctype": party_doctype, "link_name": party_name},
		pluck="parent",
	)
	contacts = [c for c in contacts if c != exclude_contact]
	if contacts:
		numbers += frappe.get_all(
			"Contact Phone",
			filters={"parent": ["in", contacts], "phone": ["is", "set"]},
			pluck="phone",
		)

	addresses = frappe.get_all(
		"Dynamic Link",
		filters={"parenttype": "Address", "link_doctype": party_doctype, "link_name": party_name},
		pluck="parent",
	)
	addresses = [a for a in addresses if a != exclude_address]
	if addresses:
		numbers += frappe.get_all(
			"Address",
			filters={"name": ["in", addresses], "phone": ["is", "set"]},
			pluck="phone",
		)

	# order-preserving de-dupe -- a Contact and an Address repeating the same number should count once
	seen = []
	for n in numbers:
		n = (n or "").strip()
		if n and n not in seen:
			seen.append(n)
	return seen


def party_phone_numbers(party_doctype: str, party_name: str) -> list[str]:
	"""Every phone number on file for this party, across every linked Contact and Address."""
	if not party_name:
		return []
	return _phone_numbers(party_doctype, party_name)


def _refresh_cache(
	party_doctype: str,
	party_name: str,
	*,
	exclude_contact: str | None = None,
	exclude_address: str | None = None,
):
	numbers = _phone_numbers(
		party_doctype, party_name, exclude_contact=exclude_contact, exclude_address=exclude_address
	)
	value = numbers[0] if numbers else ""
	if frappe.db.get_value(party_doctype, party_name, CACHE_FIELD) != value:
		frappe.db.set_value(party_doctype, party_name, CACHE_FIELD, value, update_modified=False)


def sync_from_contact(doc, _method=None):
	"""Contact on_update: refresh the cached phone on every party this Contact links to."""
	links = frappe.get_all(
		"Dynamic Link",
		filters={"parenttype": "Contact", "parent": doc.name, "link_doctype": ["in", PARTY_DOCTYPES]},
		fields=["link_doctype", "link_name"],
	)
	for link in links:
		_refresh_cache(link.link_doctype, link.link_name)


def clear_on_contact_trash(doc, _method=None):
	"""Contact on_trash: recompute, excluding this Contact, in case another Contact or an Address
	still carries a phone (on_trash fires before this Contact's own links are removed, so
	party_phone_numbers would still count it otherwise)."""
	links = frappe.get_all(
		"Dynamic Link",
		filters={"parenttype": "Contact", "parent": doc.name, "link_doctype": ["in", PARTY_DOCTYPES]},
		fields=["link_doctype", "link_name"],
	)
	for link in links:
		_refresh_cache(link.link_doctype, link.link_name, exclude_contact=doc.name)


def sync_from_address(doc, _method=None):
	"""Address on_update: refresh the cached phone on every party this Address links to."""
	links = frappe.get_all(
		"Dynamic Link",
		filters={"parenttype": "Address", "parent": doc.name, "link_doctype": ["in", PARTY_DOCTYPES]},
		fields=["link_doctype", "link_name"],
	)
	for link in links:
		_refresh_cache(link.link_doctype, link.link_name)


def clear_on_address_trash(doc, _method=None):
	"""Address on_trash: recompute, excluding this Address, in case a Contact or another Address
	still carries a phone (same on_trash-fires-before-links-removed trap as clear_on_contact_trash)."""
	links = frappe.get_all(
		"Dynamic Link",
		filters={"parenttype": "Address", "parent": doc.name, "link_doctype": ["in", PARTY_DOCTYPES]},
		fields=["link_doctype", "link_name"],
	)
	for link in links:
		_refresh_cache(link.link_doctype, link.link_name, exclude_address=doc.name)
