# sf_trading/duplicate_masters.py
"""Find duplicate Customers by CR number, ahead of enforcing uniqueness (GS Issue 4).

The client is explicit: the CR number cannot be made unique "until Bahrain duplicate customers
are cleaned" -- and a Property Setter unique=1 on a field that already holds duplicate values
would fail the very next `bench migrate` with a database error, not politely warn. This module is
the read-only half that makes the cleanup possible; the hard constraint itself is deliberately NOT
added here.

Matches on CR number, not name+phone -- the account's own customer-merge history shows a
name-minus-phone heuristic produced 714 false matches against 99 real ones on this exact kind of
job. The client has already designated CR as the authoritative key, so this uses that.
"""

from __future__ import annotations

import frappe

from sf_trading.party_completeness import CR_FIELD


def duplicate_customers_by_cr(company: str | None = None) -> list[dict]:
	"""One row per CR number shared by 2+ customers: the CR, how many, and their names."""
	filters = {CR_FIELD: ["not in", ("", None)]}
	if company:
		filters["custom_company"] = company

	rows = frappe.get_all(
		"Customer",
		filters=filters,
		fields=["name", "customer_name", CR_FIELD + " as cr_number", "disabled"],
		order_by="cr_number asc",
	)

	groups: dict[str, list] = {}
	for row in rows:
		cr = (row.cr_number or "").strip()
		if not cr:
			continue
		groups.setdefault(cr, []).append(row)

	return [
		{
			"cr_number": cr,
			"count": len(members),
			"customers": ", ".join(m.name for m in members),
			"names": ", ".join(m.customer_name for m in members),
			# a duplicate where every side is already disabled is lower-priority to clean up
			# than one where both are still active and actually colliding day to day
			"any_active": any(not m.disabled for m in members),
		}
		for cr, members in groups.items()
		if len(members) > 1
	]
