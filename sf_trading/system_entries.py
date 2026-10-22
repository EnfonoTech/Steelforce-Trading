# sf_trading/system_entries.py
"""Journals ERPNext makes by itself are not put through the approval workflow.

permission_manager routes Journal Entry through an approval chain and refuses a direct submit ("... is at
Draft and cannot be submitted directly -- it still needs approval"). That is right for a journal a person
writes. It is wrong for one ERPNext writes and submits in the same breath, because there is nobody to send it
for approval and nobody to approve it:

* the monthly asset depreciation entry -- raised by the scheduler, or by *Make Depreciation Entry* on a row of
  the Asset Depreciation Schedule -- is saved and then `submit()`ed by core at once. On this site it died with
  "Approval Required", left a draft journal behind and a schedule row pointing at it;
* the entry that reverses a depreciation (asset sold early or cancelled) and the entry behind *Scrap Asset* are
  built and submitted the same way.

ERPNext marks the journals it raises inside another document's transaction (exchange gain or loss, the credit
note behind a Payment Reconciliation) with `is_system_generated`, and permission_manager already lets those past
its submit guard and its backdate rule. The asset ones carry no such mark, so this module gives it to them, and
tells permission_manager's `pm_workflow_applicability` hook that a journal carrying it is not governed by a
workflow at all -- so it is not routed, no approval request is opened for an entry that is already posted, and
nothing is refused.

SF Trading Settings > System-generated Entries switches it, ON until somebody unticks it. Switched off, nothing
here does anything and the entries are routed (and refused) as before.

Why the mark and not a flag on the document: permission_manager looks a saved document up by name
(`get_workflow_name(doctype, name)`), which loads a fresh copy from the database -- an in-memory flag would be
gone by then, a stored field is not.
"""

import frappe
from frappe.utils import cint

from sf_trading import switches

SWITCH = "skip_approval_for_system_entries"
JOURNAL_ENTRY = "Journal Entry"

# Whitelisted methods that build and submit a journal on the user's behalf. A user can only reach the journal
# through the method, and `form_dict.cmd` is the method actually being run, so it cannot be spoofed from the
# document the way a remark or a voucher type could.
SYSTEM_COMMANDS = ("erpnext.assets.doctype.asset.depreciation.scrap_asset",)


def switch_on() -> bool:
	return switches.is_on(SWITCH, default=True)


def made_by_erpnext(doc) -> bool:
	"""True for a journal core is raising itself and does not mark.

	* `flags.planned_depr_entry` -- set by erpnext.assets.doctype.asset.depreciation on the depreciation journal,
	  for the scheduler and for Make Depreciation Entry alike;
	* `frappe.flags.is_reverse_depr_entry` -- set around the journal that reverses a depreciation;
	* the request is Scrap Asset.
	Nothing a user can type on a form sets any of these.
	"""
	if doc.flags.get("planned_depr_entry"):
		return True
	if frappe.flags.get("is_reverse_depr_entry"):
		return True
	form_dict = getattr(frappe.local, "form_dict", None) or {}
	return form_dict.get("cmd") in SYSTEM_COMMANDS


def mark_system_generated(doc, _method=None):
	"""Journal Entry before_validate: give core's own asset journals the `is_system_generated` mark."""
	if cint(doc.get("is_system_generated")) or not made_by_erpnext(doc):
		return
	if not switch_on():
		return
	doc.is_system_generated = 1


def workflow_applicability(doctype: str, doc=None) -> dict | None:
	"""Answer permission_manager's `pm_workflow_applicability` hook.

	A system-generated journal is governed by no workflow. No opinion about anything else: the doctype question
	(`doc is None`) stays unanswered, so Journal Entry keeps its place on the boot-time list of routed doctypes
	and every ordinary journal is routed exactly as before.
	"""
	if doctype != JOURNAL_ENTRY or doc is None:
		return None
	if not cint(doc.get("is_system_generated")) or not switch_on():
		return None
	return {"applies": False}
