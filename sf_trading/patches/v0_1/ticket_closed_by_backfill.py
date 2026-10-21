# sf_trading/patches/v0_1/ticket_closed_by_backfill.py
"""Who closed each ticket that closed before Ticket.closed_by existed.

The Ticket Closing Efficiency report ranks closers, and a ticket records its closer from now on, but the
ones closed earlier have only the version log: the owner of the version that moved the status to Closed.
A ticket with no such version keeps no closer. Idempotent -- it fills only a blank closed_by, and never
touches one the system closed (auto_closed).
"""

import json

import frappe


def execute():
	todo = frappe.get_all("Ticket", filters={"status": "Closed", "closed_by": ["is", "not set"], "auto_closed": 0}, pluck="name")
	if not todo:
		return
	versions = frappe.db.sql(
		"select docname, owner, data from `tabVersion` where ref_doctype = 'Ticket' and data like %s order by creation",
		('%"Closed"%',),
		as_dict=True,
	)
	closer = {}
	for version in versions:
		try:
			changed = json.loads(version.data).get("changed") or []
		except ValueError:
			continue
		if any(len(change) == 3 and change[0] == "status" and change[2] == "Closed" for change in changed):
			closer[version.docname] = version.owner  # the latest such change wins: a reopened ticket closed again
	for name in todo:
		if name in closer:
			frappe.db.set_value("Ticket", name, "closed_by", closer[name], update_modified=False)
