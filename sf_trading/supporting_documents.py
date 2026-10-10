# sf_trading/supporting_documents.py
"""Accounting entries posted without their supporting document.

Which entries need one is policy, kept on SF Trading Settings > Supporting Documents: a row per
document type, optionally narrowed (a Payment Entry's direction, a minimum amount, journals the
system raised by itself). With no rows there, DEFAULT_RULES applies. An entry needs a document
once it is submitted; it is "pending" while nothing is attached to it, and leaves the list the
moment a file is attached -- nobody has to clear it by hand, because the list is read live from
the entries and their attachments (`pending_entries`), never stored.

Submitting such an entry without a file says so at once (`remind_on_submit`), so the person who
posted it knows it is now on the list.
"""

import frappe
from frappe import _
from frappe.utils import cint, date_diff, flt, getdate, nowdate

SETTINGS = "SF Trading Settings"
RULES_FIELD = "supporting_document_rules"

#: what an entry is worth, its party, and how to read both, per document type
DOCTYPES = {
	"Journal Entry": {"amount": "total_debit", "party_type": None, "party": None, "remark": "user_remark"},
	"Payment Entry": {"amount": "base_paid_amount", "party_type": "party_type", "party": "party", "remark": "remarks"},
	"Purchase Invoice": {"amount": "base_grand_total", "party_type": "Supplier", "party": "supplier", "remark": "remarks"},
	"Sales Invoice": {"amount": "base_grand_total", "party_type": "Customer", "party": "customer", "remark": "remarks"},
	"Purchase Receipt": {"amount": "base_grand_total", "party_type": "Supplier", "party": "supplier", "remark": "remarks"},
	"Landed Cost Voucher": {"amount": "total_taxes_and_charges", "party_type": None, "party": None, "remark": None},
	"Expense Claim": {"amount": "total_sanctioned_amount", "party_type": "Employee", "party": "employee", "remark": "remark"},
	"Stock Entry": {"amount": "total_outgoing_value", "party_type": None, "party": None, "remark": "remarks"},
}

#: journals ERPNext raises for itself -- revaluations, depreciation, deferred postings, openings
SYSTEM_JOURNAL_TYPES = ("Exchange Gain Or Loss", "Exchange Rate Revaluation", "Depreciation Entry",
	"Deferred Revenue", "Deferred Expense", "Opening Entry")

#: the policy when the settings carry no rows of their own
DEFAULT_RULES = (
	{"document_type": "Journal Entry"},
	{"document_type": "Payment Entry", "payment_type": "Pay"},
	{"document_type": "Purchase Invoice"},
	{"document_type": "Landed Cost Voucher"},
	{"document_type": "Expense Claim"},
)

PENDING = "Pending"
ATTACHED = "Attached"
CHUNK = 1000


def settings() -> frappe._dict:
	"""The supporting-document switches, safe on a site that has not migrated them yet."""
	values = frappe._dict(frappe.db.get_singles_dict(SETTINGS) or {})
	alert = values.get("supporting_document_alert")
	values.alert = 1 if alert in (None, "") else cint(alert)
	return values


def rules() -> list:
	"""The enabled rules, each a _dict with document_type, payment_type, minimum_amount and
	include_system_generated. Rule rows for a doctype this site does not have are skipped."""
	rows = []
	if frappe.db.table_exists("SF Supporting Document Rule"):
		rows = frappe.get_all(
			"SF Supporting Document Rule",
			filters={"parent": SETTINGS, "parenttype": SETTINGS, "parentfield": RULES_FIELD},
			fields=["document_type", "payment_type", "minimum_amount", "include_system_generated", "enabled"],
			order_by="idx asc",
		)
	if not rows:
		rows = [frappe._dict(dict(rule, enabled=1)) for rule in DEFAULT_RULES]
	return [
		frappe._dict(r)
		for r in rows
		if cint(r.get("enabled")) and r.get("document_type") in DOCTYPES and frappe.db.exists("DocType", r.get("document_type"))
	]


def _rule_filters(rule, meta) -> list:
	"""The list filters one rule adds on top of company / date / submitted."""
	filters = []
	if rule.document_type == "Payment Entry" and rule.get("payment_type"):
		filters.append(["payment_type", "=", rule.payment_type])
	if rule.document_type == "Journal Entry" and not cint(rule.get("include_system_generated")):
		filters.append(["voucher_type", "not in", SYSTEM_JOURNAL_TYPES])
		if meta.has_field("is_system_generated"):
			filters.append(["is_system_generated", "=", 0])
	amount_field = DOCTYPES[rule.document_type]["amount"]
	if flt(rule.get("minimum_amount")) and meta.has_field(amount_field):
		filters.append([amount_field, ">=", flt(rule.minimum_amount)])
	return filters


def rule_applies(doc) -> bool:
	"""Does any rule ask this submitted document for a supporting document?"""
	for rule in rules():
		if rule.document_type != doc.doctype:
			continue
		if rule.document_type == "Payment Entry" and rule.get("payment_type") and doc.get("payment_type") != rule.payment_type:
			continue
		if rule.document_type == "Journal Entry" and not cint(rule.get("include_system_generated")):
			if doc.get("voucher_type") in SYSTEM_JOURNAL_TYPES or cint(doc.get("is_system_generated")):
				continue
		amount_field = DOCTYPES[rule.document_type]["amount"]
		if flt(rule.get("minimum_amount")) and flt(doc.get(amount_field)) < flt(rule.minimum_amount):
			continue
		return True
	return False


def attachment_counts(doctype, names) -> dict:
	"""{name: number of files attached} for the given documents (folders excluded)."""
	counts = {}
	names = list(names)
	for i in range(0, len(names), CHUNK):
		for row in frappe.get_all(
			"File",
			filters={"attached_to_doctype": doctype, "attached_to_name": ["in", names[i:i + CHUNK]], "is_folder": 0},
			fields=["attached_to_name", "count(name) as files"],
			group_by="attached_to_name",
		):
			counts[row.attached_to_name] = cint(row.files)
	return counts


def _list_fields(meta, spec, date_field) -> list:
	fields = ["name", date_field + " as posting_date", "owner"]
	candidates = [spec["amount"], "branch", "cost_center", "voucher_type", "payment_type", "title", spec["remark"],
		spec["party"]]
	if spec["party_type"] and meta.has_field(spec["party_type"]):
		candidates.append(spec["party_type"])
	for optional in candidates:
		if optional and meta.has_field(optional) and optional not in fields:
			fields.append(optional)
	return fields


def _conditions(rule, meta, spec, date_field, filters, start):
	conditions = [["docstatus", "=", 1]] + _rule_filters(rule, meta)
	if filters.get("company") and meta.has_field("company"):
		conditions.append(["company", "=", filters.company])
	if start:
		conditions.append([date_field, ">=", start])
	if filters.get("to_date"):
		conditions.append([date_field, "<=", getdate(filters.to_date)])
	if filters.get("branch") and meta.has_field("branch"):
		conditions.append(["branch", "=", filters.branch])
	if filters.get("posted_by"):
		conditions.append(["owner", "=", filters.posted_by])
	if flt(filters.get("min_amount")) and meta.has_field(spec["amount"]):
		conditions.append([spec["amount"], ">=", flt(filters.min_amount)])
	if filters.get("party") and spec["party"] and meta.has_field(spec["party"]):
		conditions.append([spec["party"], "like", "%" + filters.party + "%"])
	return conditions


def pending_entries(filters) -> list:
	"""Every entry a rule covers, with its attachment count and status -- what the user may read.

	Read through frappe.get_list, so a branch-restricted user sees only their own branch's entries.
	"""
	filters = frappe._dict(filters or {})
	wanted_types = set(filters.get("document_type") or [])
	tracked = settings().get("supporting_documents_from")
	starts = [getdate(d) for d in (filters.get("from_date"), tracked) if d]
	start = max(starts) if starts else None
	today = getdate(nowdate())
	out = []
	for rule in rules():
		doctype = rule.document_type
		spec = DOCTYPES[doctype]
		if wanted_types and doctype not in wanted_types:
			continue
		if filters.get("party") and not spec["party"]:
			continue
		if not frappe.has_permission(doctype, "read"):
			continue
		meta = frappe.get_meta(doctype)
		date_field = "posting_date" if meta.has_field("posting_date") else "transaction_date"
		rows = frappe.get_list(
			doctype,
			filters=_conditions(rule, meta, spec, date_field, filters, start),
			fields=_list_fields(meta, spec, date_field),
			order_by=date_field + " asc",
			limit_page_length=0,
		)
		if not rows:
			continue
		files = attachment_counts(doctype, [r.name for r in rows])
		for r in rows:
			count = files.get(r.name, 0)
			party_type = spec["party_type"]
			if party_type and meta.has_field(party_type):
				party_type = r.get(party_type)
			party = r.get(spec["party"]) if spec["party"] else None
			out.append(frappe._dict(
				document_type=doctype,
				document=r.name,
				posting_date=r.posting_date,
				days_pending=date_diff(today, r.posting_date) if r.posting_date and not count else 0,
				amount=flt(r.get(spec["amount"])),
				party_type=party_type if party else None,
				party=party,
				branch=r.get("branch"),
				cost_center=r.get("cost_center"),
				detail=r.get("voucher_type") or r.get("payment_type") or "",
				remarks=((r.get(spec["remark"]) if spec["remark"] else None) or r.get("title") or "")[:140],
				posted_by=r.owner,
				attachments=count,
				status=ATTACHED if count else PENDING,
			))
	return out


def remind_on_submit(doc, method=None):
	"""on_submit: an entry the rules cover, submitted with nothing attached, says it is now listed."""
	if doc.doctype not in DOCTYPES or frappe.flags.in_import or frappe.flags.in_migrate:
		return
	try:
		if not settings().alert or not rule_applies(doc):
			return
		if frappe.db.exists("File", {"attached_to_doctype": doc.doctype, "attached_to_name": doc.name}):
			return
	except Exception:
		# a reminder must never stand in the way of a posting
		frappe.log_error(title="sf_trading: supporting document reminder failed")
		return
	frappe.msgprint(
		_("{0} {1} was posted without a supporting document. It stays on the Pending Supporting "
		  "Documents report until a file is attached to it.").format(_(doc.doctype), frappe.bold(doc.name)),
		title=_("Supporting Document Pending"),
		indicator="orange",
	)
