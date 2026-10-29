# sf_trading/sf_trading/report/masters_pending_approval/masters_pending_approval.py
"""Masters Pending Approval: the customers and suppliers the approval is holding back, and what each still lacks.

One row per master that the Customer / Supplier approval covers and has not approved (master_approval.py):
a credit customer (a Customer Credit Limit above zero) and a supplier, new or turned credit, in Draft (not
sent yet), Pending Approval (with the approver) or Rejected -- plus, under Strict Validation, an older master
nobody has sent. Orders, deliveries, invoices and payments are refused for each of them until it is approved.

Existing masters. The approval holds a master back only while it covers it: the switch for that master is on
and the master is new, or Strict Validation is ticked, or it is waiting. An older credit customer or supplier
that lacks what the master rules ask for is therefore never held -- yet it is what the clerk has to complete.
With `Include Existing Masters` ticked (the default) such a master is listed too, as `Existing Master`,
whatever the settings say, but only while it still lacks something. Nothing is refused for it: it is a to-do
list, not a block. Customers stay limited to credit customers, as the approval is. Existing masters are read
up to EXISTING_LIMIT; the note says when the list was cut.

`Still Missing` is what the approval's own requirement gate would say (`master_approval.missing_requirements`),
so a row that reads empty can be sent for approval and approved. It is read per master, so the first
EXACT_LIMIT held rows carry it; the rest are listed without.
"""

import frappe
from frappe import _
from frappe.utils import cint, getdate

from sf_trading import master_approval as ma

EXACT_LIMIT = 500
EXISTING_LIMIT = 1500
LEGACY = "Approved Before the Workflow"
EXISTING = "Existing Master"
#: the order of the list: what waits on the clerk first, then what waits on an approver, then the masters the
#: approval does not hold
ORDER = {ma.DRAFT: 0, ma.REJECTED: 1, ma.APPROVED: 2, ma.PENDING: 3, EXISTING: 4}


def execute(filters=None):
	filters = frappe._dict(filters or {})
	party_type = filters.get("party_type") or "Customer and Supplier"
	rows = []
	if party_type in ("Customer and Supplier", "Customer") and frappe.has_permission("Customer", "read"):
		rows += customers(filters)
	if party_type in ("Customer and Supplier", "Supplier") and frappe.has_permission("Supplier", "read"):
		rows += suppliers(filters)
	rows = [r for r in rows if wanted(r, filters)]
	rows.sort(key=lambda r: (ORDER.get(r.stage_key, 9), r.party_type, r.party_name or ""))
	held = [r for r in rows if r.stage_key != EXISTING]
	existing = [r for r in rows if r.stage_key == EXISTING]
	cut = len(existing) > EXISTING_LIMIT
	read_requirements(held, EXACT_LIMIT)
	read_requirements(existing, EXISTING_LIMIT)
	# an existing master is a to-do only while it lacks something (one past the limit is not known)
	rows = held + [r for r in existing[:EXISTING_LIMIT] if r.gaps]
	if cint(filters.get("incomplete_only")):
		rows = [r for r in rows if r.gaps]
	return columns(), rows, note(cut), None, cards(rows)


def wanted(row, filters) -> bool:
	status = filters.get("status")
	if row.stage_key == EXISTING and not (cint(filters.get("include_existing", 1)) or status == EXISTING):
		return False
	return not status or row.stage_key == status


def _stage(status):
	"""(the status as shown, the next step in words, the filter key) of a master the approval holds."""
	if status == ma.PENDING:
		return ma.PENDING, _("With the approver"), ma.PENDING
	if status == ma.REJECTED:
		return ma.REJECTED, _("Rejected: correct it and send it again"), ma.REJECTED
	if status == ma.APPROVED:
		return ma.APPROVED, _("Approved before the workflow: send it for approval"), LEGACY
	return ma.DRAFT, _("Complete it and send it for approval"), ma.DRAFT


def _state(master, credit_limit=None):
	state = frappe._dict(master)
	if credit_limit is not None:
		state["credit_limits"] = [frappe._dict(credit_limit=credit_limit)]
	return state


def _row(party_type, master, credit_limit, status, stage, key):
	scrub = party_type.lower()
	return frappe._dict(
		party_type=party_type, party=master.name, party_name=master.get(scrub + "_name"),
		group=master.get(scrub + "_group"), credit_limit=credit_limit, status=status, stage=stage, stage_key=key,
		approvers=" / ".join(_(r) for r in ma.workflow_approver_roles(party_type)),
		created_on=getdate(master.creation), updated_on=getdate(master.modified), created_by=master.owner,
		disabled=cint(master.disabled), missing="", gaps=0,
	)


def _held(party_type, master, credit_limit=None):
	"""The row for this master, or None when the approval does not hold it back."""
	state = _state(master, credit_limit)
	if not ma.is_covered(party_type, state) or ma.is_approved(state):
		return None
	return _row(party_type, master, credit_limit, *_stage(state.get(ma.STATUS_FIELD)))


def _existing(party_type, master, credit_limit=None):
	"""The row for a master the approval does not hold -- an older one, or one whose switch is off -- that is
	neither approved nor bypassed. Whether it lacks anything is read afterwards."""
	state = _state(master, credit_limit)
	if ma.is_bypassed(state) or ma.is_approved(state) or ma.is_covered(party_type, state):
		return None
	if not ma.in_scope(party_type, state):
		return None
	if not ma.is_enabled(party_type):
		stage = _("The approval is switched off for this master: complete what is missing")
	else:
		stage = _("Older than the approval: complete what is missing")
	return _row(party_type, master, credit_limit, _(EXISTING), stage, EXISTING)


def _listed(party_type, master, credit_limit=None):
	return _held(party_type, master, credit_limit) or _existing(party_type, master, credit_limit)


def _conditions(filters) -> dict:
	return {} if cint(filters.get("include_disabled")) else {"disabled": 0}


def credit_limits() -> dict:
	"""{customer: its highest Customer Credit Limit}, for the customers with one above zero."""
	rows = frappe.get_all("Customer Credit Limit", filters={"parenttype": "Customer", "credit_limit": [">", 0]},
		fields=["parent", "max(credit_limit) as credit_limit"], group_by="parent")
	return {r.parent: r.credit_limit for r in rows}


def customers(filters) -> list:
	if not ma.has_fields("Customer"):
		return []
	limits = credit_limits()
	if not limits:
		return []
	masters = frappe.get_list("Customer", filters=_conditions(filters), fields=["name", "customer_name",
		"customer_group", ma.STATUS_FIELD, ma.APPROVED_ON, ma.BYPASSED, "creation", "modified", "owner", "disabled"],
		limit_page_length=0)
	return [r for r in (_listed("Customer", m, limits[m.name]) for m in masters if m.name in limits) if r]


def suppliers(filters) -> list:
	if not ma.has_fields("Supplier"):
		return []
	masters = frappe.get_list("Supplier", filters=_conditions(filters), fields=["name", "supplier_name",
		"supplier_group", ma.STATUS_FIELD, ma.APPROVED_ON, ma.BYPASSED, "creation", "modified", "owner", "disabled"],
		limit_page_length=0)
	return [r for r in (_listed("Supplier", m) for m in masters) if r]


def read_requirements(rows, limit):
	"""What the approval's gate says each master still lacks (one document read per master), for the first
	`limit` rows."""
	for row in rows[:limit]:
		try:
			missing = ma.missing_requirements(frappe.get_doc(row.party_type, row.party))
		except Exception:
			frappe.log_error(title="Masters Pending Approval: requirements of " + row.party)
			missing = []
		row.missing = ", ".join(missing)
		row.gaps = len(missing)


def cards(rows):
	def count(key):
		return len([r for r in rows if r.stage_key == key])

	return [
		{"label": _("Waiting for Approval"), "value": len(rows) - count(EXISTING), "datatype": "Int", "indicator": "Blue"},
		{"label": _("Not Sent Yet"), "value": count(ma.DRAFT) + count(LEGACY), "datatype": "Int", "indicator": "Orange"},
		{"label": _("With the Approver"), "value": count(ma.PENDING), "datatype": "Int", "indicator": "Blue"},
		{"label": _("Rejected"), "value": count(ma.REJECTED), "datatype": "Int", "indicator": "Red"},
		{"label": _("Existing, Incomplete"), "value": count(EXISTING), "datatype": "Int", "indicator": "Orange"},
		{"label": _("Incomplete"), "value": len([r for r in rows if r.gaps]), "datatype": "Int", "indicator": "Red"},
	]


def note(cut=False):
	off = [_(dt) for dt in ("Customer", "Supplier") if not ma.is_enabled(dt)]
	text = _("Customers and suppliers listed here cannot be used on orders, deliveries, invoices or payments "
		"until they are approved.")
	text += " " + _("Existing Master rows are not held: they are older masters (or masters whose approval is "
		"switched off) that still lack what the master rules ask for.")
	if off:
		text += " " + _("The approval is switched off for: {0} (SF Trading Settings).").format(", ".join(off))
	if ma.is_strict():
		text += " " + _("Strict Validation is on: older masters nobody has sent are listed too.")
	if cut:
		text += " " + _("Only the first {0} existing masters were checked: filter by Party Type to see the rest.").format(
			EXISTING_LIMIT)
	return text


def columns():
	return [
		{"label": _("Party Type"), "fieldname": "party_type", "fieldtype": "Data", "width": 90},
		{"label": _("Party"), "fieldname": "party", "fieldtype": "Dynamic Link", "options": "party_type", "width": 170},
		{"label": _("Name"), "fieldname": "party_name", "fieldtype": "Data", "width": 220},
		{"label": _("Group"), "fieldname": "group", "fieldtype": "Data", "width": 130},
		{"label": _("Credit Limit"), "fieldname": "credit_limit", "fieldtype": "Currency", "width": 110},
		{"label": _("Status"), "fieldname": "status", "fieldtype": "Data", "width": 130},
		{"label": _("Next Step"), "fieldname": "stage", "fieldtype": "Data", "width": 260},
		{"label": _("Still Missing"), "fieldname": "missing", "fieldtype": "Data", "width": 360},
		{"label": _("Gaps"), "fieldname": "gaps", "fieldtype": "Int", "width": 60},
		{"label": _("Approver"), "fieldname": "approvers", "fieldtype": "Data", "width": 200},
		{"label": _("Created On"), "fieldname": "created_on", "fieldtype": "Date", "width": 100},
		{"label": _("Last Updated"), "fieldname": "updated_on", "fieldtype": "Date", "width": 100},
		{"label": _("Created By"), "fieldname": "created_by", "fieldtype": "Link", "options": "User", "width": 160},
	]
