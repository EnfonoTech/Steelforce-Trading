# sf_trading/purchase_receipt_gate.py
"""Purchase Receipt (GRN): no new ones, no changes to old ones, for everyone but a named few.

SF Trading Settings > Purchase Receipt (GRN):
  * Disable Purchase Receipt (GRN) -- when ticked, a Purchase Receipt cannot be made, edited, submitted,
    cancelled, amended or deleted. Goods then come in through the Purchase Invoice with Update Stock.
    Every receipt already made stays where it was: it is listed, found in link fields and reports, shown
    in a Purchase Order's connections and trail, printed, and used as the source of a Purchase Invoice
    or a Landed Cost Voucher. Cancelling one is closed too -- its stock and ledger entries, and any
    voucher costed against it, are not to be undone by whoever happens to hold the button.
  * Allowed to Use Purchase Receipt -- roles or users who still make, change and cancel them, as the other
    bypass rosters in the settings work (credit_customer_approval.may_bypass). Administrator always may.

Enforced on the server (has_permission), so no route gets past it; the desk is told as well
(boot_session) so the doctype drops out of Create menus, and the Purchase Order stops offering
Create > Purchase Receipt (public/js/purchase_order_grn.js). Reading is never closed -- there is no
permission_query_conditions on Purchase Receipt any more -- so the connections dashboard, the document
trail and every link field see the receipts that exist.

System code that makes a Purchase Receipt itself with ignore_permissions -- e.g. the automatic
return of a Purchase Invoice's receipt (api/purchase_return.py) -- is not a user using GRN and is
left alone, and so is a Landed Cost Voucher, which re-values a receipt without saving it.
"""

import frappe
from frappe.utils import cint

SETTINGS = "SF Trading Settings"
SWITCH = "disable_purchase_receipt"
BYPASS_FIELD = "purchase_receipt_bypass"
DOCTYPE = "Purchase Receipt"
#: the lists in the desk's boot that say which doctypes the user may make, change, cancel, delete or import
CLOSED_BOOT_KEYS = ("can_create", "can_write", "can_cancel", "can_delete", "can_import")


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


#: what a blocked user may not do to a Purchase Receipt: bring one into being or change one that exists.
#: Reading, selecting, searching, reporting, exporting, printing and emailing stay open.
CLOSED = ("create", "write", "submit", "cancel", "amend", "delete", "import")


def has_permission(doc, ptype=None, user=None):
	"""has_permission hook: a blocked user may look at every Purchase Receipt and change none."""
	if ptype in CLOSED and is_blocked(user):
		return False
	return None


def permission_query_conditions(user=None):
	"""No longer registered: reading a Purchase Receipt is never closed. It stays, returning no condition,
	for the one moment it can still be asked: a bench that has pulled this file but not yet reloaded its hooks
	(the old hooks.py, cached in redis, still names it) must not fail on every receipt list in the meantime."""
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
	"""Tell the desk a blocked user cannot make or change a Purchase Receipt, so the Create menus, the
	"+" in a document's connections and the Cancel / Delete buttons that ask frappe.model.can_create and
	its siblings drop it. The lists the user may read are left alone: they still show the doctype."""
	if not is_blocked():
		return
	user = bootinfo.get("user") or {}
	for key in CLOSED_BOOT_KEYS:
		value = user.get(key)
		if isinstance(value, list) and DOCTYPE in value:
			value.remove(DOCTYPE)
	bootinfo["sf_grn_disabled"] = 1
