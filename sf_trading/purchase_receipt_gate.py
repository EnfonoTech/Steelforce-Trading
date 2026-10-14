# sf_trading/purchase_receipt_gate.py
"""Purchase Receipt (GRN), switched off for everyone but a named few.

SF Trading Settings > Purchase Receipt (GRN):
  * Disable Purchase Receipt (GRN) -- when ticked, Purchase Receipt is closed to every role and
    user: it cannot be opened, made, saved or submitted, and the list shows nothing. Goods then
    come in through the Purchase Invoice with Update Stock.
  * Allowed to Use Purchase Receipt -- roles or users who still see and use it, as the other
    bypass rosters in the settings work (credit_customer_approval.may_bypass). Administrator
    always may.

Enforced on the server (has_permission + permission_query_conditions), so no route gets past it;
the desk is told as well (boot_session) so the doctype drops out of the sidebar, search and
links, and the Purchase Order stops offering Create > Purchase Receipt
(public/js/purchase_order_grn.js).

System code that makes a Purchase Receipt itself with ignore_permissions -- e.g. the automatic
return of a Purchase Invoice's receipt (api/purchase_return.py) -- is not a user using GRN and is
left alone.
"""

import frappe
from frappe.utils import cint

SETTINGS = "SF Trading Settings"
SWITCH = "disable_purchase_receipt"
BYPASS_FIELD = "purchase_receipt_bypass"
DOCTYPE = "Purchase Receipt"


def _settings():
	try:
		return frappe.get_cached_doc(SETTINGS)
	except frappe.DoesNotExistError:
		return None


def grn_disabled() -> bool:
	s = _settings()
	return bool(s and cint(s.get(SWITCH)))


def may_use(user: str | None = None) -> bool:
	"""Whether this user may use Purchase Receipt while it is disabled."""
	user = user or frappe.session.user
	if user == "Administrator":
		return True

	rows = (_settings() or {}).get(BYPASS_FIELD) or []
	user_roles = set(frappe.get_roles(user))
	for row in rows:
		if not row.override:
			continue
		if row.override_type == "User" and row.override == user:
			return True
		if row.override_type == "Role" and row.override in user_roles:
			return True
	return False


def is_blocked(user: str | None = None) -> bool:
	return grn_disabled() and not may_use(user)


def has_permission(doc, ptype=None, user=None):
	"""has_permission hook: a blocked user may do nothing with any Purchase Receipt."""
	if is_blocked(user):
		return False
	return None


def permission_query_conditions(user=None):
	"""permission_query_conditions hook: a blocked user's list, link search and reports are empty."""
	if is_blocked(user):
		return "1=0"
	return ""


def clear_boot_cache(doc, _method=None):
	"""SF Trading Settings on_update: the desk keeps each user's boot cached, so a change to the
	switch or the roster would otherwise show only after they log in again."""
	before = doc.get_doc_before_save()
	roster = lambda d: sorted((r.override_type, r.override) for r in (d.get(BYPASS_FIELD) or []))
	if before and cint(before.get(SWITCH)) == cint(doc.get(SWITCH)) and roster(before) == roster(doc):
		return
	frappe.cache.delete_key("bootinfo")


def boot_session(bootinfo):
	"""Tell the desk a blocked user has no Purchase Receipt, so it drops out of the sidebar,
	the awesome bar, link fields and Create menus that ask frappe.model.can_create."""
	if not is_blocked():
		return
	user = bootinfo.get("user") or {}
	for key, value in list(user.items()):
		if key.startswith("can_") and isinstance(value, list) and DOCTYPE in value:
			value.remove(DOCTYPE)
	bootinfo["sf_grn_disabled"] = 1
