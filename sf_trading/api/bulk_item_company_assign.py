# sf_trading/api/bulk_item_company_assign.py
"""Bulk-assign items to an additional company's Item Defaults (GS Issue 31).

Item itself is global on this bench; what scopes an item to a company is a row in its own Item
Default child table (parent=item, company=<company>, plus that company's own warehouse/account
defaults). With ~16,000 items already allocated to one company, doing this one Item form at a time
is not realistic -- this is the bulk door the client asked for. Never used for the FIRST company an
item belongs to (that Item Default row already carries real defaults someone chose); only for
extending an item that already has one company's row to an additional company, copying that row's
defaults across rather than leaving the new one blank.
"""

from __future__ import annotations

import frappe
from frappe import _


def items_missing_company(item_codes: list[str], company: str) -> list[str]:
	"""Which of these items have no Item Default row for `company` yet."""
	if not item_codes:
		return []
	existing = frappe.get_all(
		"Item Default",
		filters={"parent": ["in", item_codes], "company": company},
		pluck="parent",
	)
	existing_set = set(existing)
	return [code for code in item_codes if code not in existing_set]


@frappe.whitelist()
def preview_missing_for_group(item_group: str, company: str) -> dict:
	"""How many items in this Item Group (recursively) have no Item Default row for `company`."""
	frappe.has_permission("Item", "read", throw=True)

	lft_rgt = frappe.db.get_value("Item Group", item_group, ["lft", "rgt"])
	if not lft_rgt:
		frappe.throw(_("Item Group %s not found.") % item_group)
	lft, rgt = lft_rgt

	item_codes = frappe.db.sql(
		"""
		SELECT item.name FROM `tabItem` item
		INNER JOIN `tabItem Group` ig ON ig.name = item.item_group
		WHERE ig.lft >= %s AND ig.rgt <= %s AND item.disabled = 0
		""",
		(lft, rgt),
		pluck=True,
	)
	missing = items_missing_company(item_codes, company)
	return {"total_items": len(item_codes), "missing_count": len(missing), "sample": missing[:20]}


@frappe.whitelist()
def bulk_assign_company(item_codes, company: str, source_company: str | None = None) -> dict:
	"""Queue the bulk assignment (GS Issue 31 talks about 16,000 items -- one doc.save() per item,
	synchronously, in an HTTP request would time out well before finishing; see the account's own
	hard rule on anything that can cross 30 seconds). Returns immediately; progress and any
	per-item failures land in the job's own log, read back with frappe.get_doc("RQ Job", ...) or
	the Background Jobs list.
	"""
	frappe.has_permission("Item", "write", throw=True)

	if isinstance(item_codes, str):
		item_codes = frappe.parse_json(item_codes) or []
	if not item_codes:
		frappe.throw(_("No items given."))
	if not frappe.db.exists("Company", company):
		frappe.throw(_("Company %s not found.") % company)

	job = frappe.enqueue(
		"sf_trading.api.bulk_item_company_assign._run_bulk_assign",
		queue="long",
		timeout=3600,
		job_name="bulk_item_company_assign_%s" % company,
		enqueue_after_commit=True,
		item_codes=item_codes,
		company=company,
		source_company=source_company,
		user=frappe.session.user,
	)
	return {"queued": True, "job_id": getattr(job, "id", None), "item_count": len(item_codes)}


def _run_bulk_assign(item_codes: list[str], company: str, source_company: str | None, user: str):
	"""Worker: the actual per-item Item Default insert. Never called directly -- only via the
	queued job above, so it always runs off-request."""
	missing = items_missing_company(item_codes, company)
	if not missing:
		frappe.publish_realtime(
			"sf_bulk_item_company_assign_done",
			{"assigned": 0, "already_had_it": len(item_codes), "company": company},
			user=user,
		)
		return

	source_rows = {}
	if source_company:
		for row in frappe.get_all(
			"Item Default",
			filters={"parent": ["in", missing], "company": source_company},
			fields=[
				"parent", "default_warehouse", "buying_cost_center", "expense_account",
				"selling_cost_center", "income_account", "default_price_list",
			],
		):
			source_rows[row.parent] = row

	created, failed = 0, []
	for item_code in missing:
		try:
			src = source_rows.get(item_code)
			doc = frappe.get_doc("Item", item_code)
			doc.append(
				"item_defaults",
				{
					"company": company,
					"default_warehouse": src.default_warehouse if src else None,
					"buying_cost_center": src.buying_cost_center if src else None,
					"expense_account": src.expense_account if src else None,
					"selling_cost_center": src.selling_cost_center if src else None,
					"income_account": src.income_account if src else None,
					"default_price_list": src.default_price_list if src else None,
				},
			)
			doc.save(ignore_permissions=False)
			created += 1
		except Exception:
			failed.append(item_code)
			frappe.log_error(
				title="bulk_item_company_assign: %s" % item_code,
				message=frappe.get_traceback(),
			)
		if created % 200 == 0:
			frappe.db.commit()

	frappe.publish_realtime(
		"sf_bulk_item_company_assign_done",
		{
			"assigned": created,
			"already_had_it": len(item_codes) - len(missing),
			"failed": len(failed),
			"company": company,
		},
		user=user,
	)
