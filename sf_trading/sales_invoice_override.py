"""
Sales Invoice overrides: remove empty item rows before validation (barcode scanner scan row).
"""

from __future__ import annotations

import frappe


def before_validate(doc, method=None):
	"""Remove item rows that have no item_code (leftover scan row from barcode). Runs before validation."""
	if not doc.get("items"):
		return
	# Remove in reverse so indices stay valid
	to_remove = [row for row in doc.items if not (row.get("item_code") or "").strip()]
	for row in to_remove:
		doc.remove(row)
	for i, row in enumerate(doc.items, start=1):
		row.idx = i
