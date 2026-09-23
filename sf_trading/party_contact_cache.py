# sf_trading/party_contact_cache.py
"""Cache the party's own phone onto Customer/Supplier list views (GS Issue 11).

Neither doctype carries a phone field of its own on this bench -- it lives on the linked Contact
only, which is exactly why list views show it blank. Hooking Contact's own on_update/on_trash
(never Customer's/Supplier's after_insert, which cannot see a Contact core has not created yet --
see party_completeness.py's module docstring for the same trap) walks back through the Dynamic
Link table to every party the Contact is linked to and refreshes a cached field there.

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
					"description": "Kept in sync from this customer's linked Contact. Edit the Contact, not this field.",
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
					"description": "Kept in sync from this supplier's linked Contact. Edit the Contact, not this field.",
				}
			],
		},
		ignore_validate=True,
		update=True,
	)


def party_phone_numbers(party_doctype: str, party_name: str) -> list[str]:
	"""Every phone number on file across every Contact linked to this party, de-duplicated."""
	if not party_name:
		return []

	contacts = frappe.get_all(
		"Dynamic Link",
		filters={"parenttype": "Contact", "link_doctype": party_doctype, "link_name": party_name},
		pluck="parent",
	)
	if not contacts:
		return []

	numbers = frappe.get_all(
		"Contact Phone",
		filters={"parent": ["in", contacts], "phone": ["is", "set"]},
		pluck="phone",
	)
	# order-preserving de-dupe -- two Contacts sharing a landline should count once, not twice
	seen = []
	for n in numbers:
		n = (n or "").strip()
		if n and n not in seen:
			seen.append(n)
	return seen


def _primary_phone(contact_doc) -> str:
	rows = contact_doc.get("phone_nos") or []
	primary = next(
		(r.phone for r in rows if r.get("is_primary_mobile_no") or r.get("is_primary_phone")), None
	)
	return primary or (rows[0].phone if rows else "")


def _refresh_cache(party_doctype: str, party_name: str):
	numbers = party_phone_numbers(party_doctype, party_name)
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
	"""Contact on_trash: recompute, in case another linked Contact still carries a phone."""
	links = frappe.get_all(
		"Dynamic Link",
		filters={"parenttype": "Contact", "parent": doc.name, "link_doctype": ["in", PARTY_DOCTYPES]},
		fields=["link_doctype", "link_name"],
	)
	for link in links:
		# refresh AFTER excluding the Contact being deleted -- party_phone_numbers reads Dynamic
		# Link fresh from the DB, and on_trash fires before the Contact/its links are removed, so
		# do the exclusion here rather than trust the helper to already see it gone.
		other_contacts = [
			c
			for c in frappe.get_all(
				"Dynamic Link",
				filters={
					"parenttype": "Contact",
					"link_doctype": link.link_doctype,
					"link_name": link.link_name,
				},
				pluck="parent",
			)
			if c != doc.name
		]
		value = ""
		for c in other_contacts:
			numbers = frappe.get_all(
				"Contact Phone", filters={"parent": c, "phone": ["is", "set"]}, pluck="phone"
			)
			if numbers:
				value = numbers[0]
				break
		if frappe.db.get_value(link.link_doctype, link.link_name, CACHE_FIELD) != value:
			frappe.db.set_value(link.link_doctype, link.link_name, CACHE_FIELD, value, update_modified=False)
