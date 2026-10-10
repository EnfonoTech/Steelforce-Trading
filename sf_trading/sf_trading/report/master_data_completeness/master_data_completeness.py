# sf_trading/sf_trading/report/master_data_completeness/master_data_completeness.py
"""Master Data Completeness: which customers and suppliers still lack what they should carry.

One row per B2B customer (VAT Registration Number on file) and per supplier -- by default the
suppliers with a Tax ID, or every supplier -- with what is missing: a supporting document, a file
attached to one, Payment Terms, a mobile number, an email, an address, Tax ID or CR number, and any
document already expired. Masters created on or after SF Trading Settings > New-Master Rules From
are flagged New: the system already refuses business with those until they are complete
(sf_trading.party_documents); older masters are listed here so they can be completed.

Every lookup is one query per kind across all parties (documents, files, contacts, addresses, last
transaction), never one per party.
"""

from collections import defaultdict

import frappe
from frappe import _
from frappe.query_builder.functions import Count, Max
from frappe.utils import add_days, cint, cstr, get_datetime, getdate, nowdate

from sf_trading import party_documents as pd

SCOPE_TAXED = "B2B Customers and Suppliers with Tax ID"
SCOPE_ALL = "B2B Customers and All Suppliers"
CHECKS = ("Supporting Document", "Document Attachment", "Payment Terms", "Mobile", "Email", "Address",
	"Tax ID", "CR Number", "Expired Document")
CHUNK = 1000


def execute(filters=None):
	filters = frappe._dict(filters or {})
	rows = []
	party_type = filters.get("party_type") or "Customer and Supplier"
	if party_type in ("Customer and Supplier", "Customer") and frappe.has_permission("Customer", "read"):
		rows += customers(filters)
	if party_type in ("Customer and Supplier", "Supplier") and frappe.has_permission("Supplier", "read"):
		rows += suppliers(filters)
	# the summary counts complete masters too; the detail list leaves them out unless asked
	counted = [r for r in rows if wanted(r, filters, include_complete=True)]
	rows = [r for r in counted if wanted(r, filters)]
	rows.sort(key=lambda r: (not r.new_master, -len(r.missing_list), r.party_type, r.party_name or ""))
	summary = cards(counted)
	if filters.get("view") == "Summary":
		return summary_columns(), summary_rows(counted), None, None, summary
	for r in rows:
		r.missing = ", ".join(_(m) for m in r.missing_list)
		r.missing_count = len(r.missing_list)
	return columns(), rows, note(), None, summary


def wanted(row, filters, include_complete=False) -> bool:
	if cint(filters.get("new_masters_only")) and not row.new_master:
		return False
	if filters.get("missing") and filters.missing not in row.missing_list:
		return False
	if not include_complete and not cint(filters.get("show_complete")) and not row.missing_list:
		return False
	return True


def _common_filters(filters, conditions):
	start, end = filters.get("created_from"), filters.get("created_to")
	if start and end:
		# Frappe stretches a date upper bound to the end of that day
		conditions["creation"] = ["between", [getdate(start), getdate(end)]]
	elif start:
		conditions["creation"] = [">=", getdate(start)]
	elif end:
		conditions["creation"] = ["<", add_days(getdate(end), 1)]
	if not cint(filters.get("include_disabled")):
		conditions["disabled"] = 0


def customers(filters) -> list:
	conditions = {"custom_vat_registration_number": ["is", "set"]}
	_common_filters(filters, conditions)
	masters = frappe.get_list("Customer", filters=conditions, fields=["name", "customer_name as party_name",
		"custom_vat_registration_number as tax_id", "custom_commercial_registration_number as cr", "payment_terms",
		"mobile_no", "custom_mobile_no", "email_id", "customer_primary_address as primary_address", "creation",
		"disabled"], limit_page_length=0)
	return build("Customer", [m for m in masters if cstr(m.tax_id).strip()], "Sales Invoice", "customer")


def suppliers(filters) -> list:
	conditions = {}
	if (filters.get("scope") or SCOPE_TAXED) == SCOPE_TAXED:
		conditions["tax_id"] = ["is", "set"]
	_common_filters(filters, conditions)
	masters = frappe.get_list("Supplier", filters=conditions, fields=["name", "supplier_name as party_name",
		"supplier_type", "tax_id", "payment_terms", "mobile_no", "custom_mobile_no", "email_id",
		"supplier_primary_address as primary_address", "creation", "disabled"], limit_page_length=0)
	return build("Supplier", masters, "Purchase Invoice", "supplier")


def _chunks(names):
	names = list(names)
	for i in range(0, len(names), CHUNK):
		yield names[i:i + CHUNK]


def document_rows(party_type, names) -> dict:
	"""{party: {"rows": n, "attached": n, "expired": n}} from the Supporting Documents grid."""
	out = defaultdict(lambda: frappe._dict(rows=0, attached=0, expired=0))
	today = getdate(nowdate())
	for chunk in _chunks(names):
		for r in frappe.get_all(pd.ROW_DOCTYPE, filters={"parenttype": party_type, "parentfield": pd.TABLE,
				"parent": ["in", chunk]}, fields=["parent", "attachment", "validate_expiry", "expiry_date", "grace_days"]):
			p = out[r.parent]
			p.rows += 1
			if cstr(r.attachment).strip():
				p.attached += 1
			if cint(r.validate_expiry) and r.expiry_date and add_days(getdate(r.expiry_date), cint(r.grace_days)) < today:
				p.expired += 1
	return out


def files(party_type, names) -> set:
	found = set()
	for chunk in _chunks(names):
		found.update(frappe.get_all("File", filters={"attached_to_doctype": party_type,
			"attached_to_name": ["in", chunk], "is_folder": 0}, pluck="attached_to_name", distinct=True))
	return found


def contact_details(party_type, names) -> tuple:
	"""(parties with a phone on a linked Contact, parties with an email on one)."""
	link = frappe.qb.DocType("Dynamic Link")
	phone = frappe.qb.DocType("Contact Phone")
	email = frappe.qb.DocType("Contact Email")
	phones, emails = set(), set()
	for chunk in _chunks(names):
		base = (link.parenttype == "Contact") & (link.link_doctype == party_type) & link.link_name.isin(chunk)
		phones.update(r[0] for r in frappe.qb.from_(link).join(phone).on(phone.parent == link.parent)
			.select(link.link_name).where(base & (phone.phone != "")).distinct().run())
		emails.update(r[0] for r in frappe.qb.from_(link).join(email).on(email.parent == link.parent)
			.select(link.link_name).where(base & (email.email_id != "")).distinct().run())
	return phones, emails


def addresses(party_type, names) -> set:
	found = set()
	for chunk in _chunks(names):
		found.update(frappe.get_all("Dynamic Link", filters={"parenttype": "Address", "link_doctype": party_type,
			"link_name": ["in", chunk]}, pluck="link_name", distinct=True))
	return found


def last_transactions(doctype, party_field, names) -> dict:
	table = frappe.qb.DocType(doctype)
	field = table[party_field]
	out = {}
	for chunk in _chunks(names):
		for party, last, count in (frappe.qb.from_(table)
				.select(field, Max(table.posting_date), Count(table.name))
				.where((table.docstatus == 1) & field.isin(chunk))
				.groupby(field).run()):
			out[party] = (last, count)
	return out


def build(party_type, masters, invoice_doctype, party_field) -> list:
	if not masters:
		return []
	names = [m.name for m in masters]
	docs = document_rows(party_type, names)
	attached_files = files(party_type, names)
	phones, emails = contact_details(party_type, names)
	linked_addresses = addresses(party_type, names)
	activity = last_transactions(invoice_doctype, party_field, names)
	start = pd.rules_from()
	rows = []
	for m in masters:
		d = docs.get(m.name) or frappe._dict(rows=0, attached=0, expired=0)
		has_mobile = bool(cstr(m.custom_mobile_no).strip() or cstr(m.mobile_no).strip() or m.name in phones)
		has_email = bool(cstr(m.email_id).strip() or m.name in emails)
		has_address = bool(m.primary_address or m.name in linked_addresses)
		new_master = bool(start and get_datetime(m.creation) >= start)
		missing = []
		if not d.rows:
			missing.append("Supporting Document")
		# a new master is held to the gate's own test (a grid row with its file); an older one is
		# credited for any file attached to it
		if not d.attached and (new_master or m.name not in attached_files):
			missing.append("Document Attachment")
		if not m.payment_terms:
			missing.append("Payment Terms")
		if not has_mobile:
			missing.append("Mobile")
		if not has_email:
			missing.append("Email")
		if not has_address:
			missing.append("Address")
		if party_type == "Supplier" and m.get("supplier_type") == "Company" and not cstr(m.tax_id).strip():
			missing.append("Tax ID")
		if party_type == "Customer" and not cstr(m.cr).strip():
			missing.append("CR Number")
		if d.expired:
			missing.append("Expired Document")
		last, count = activity.get(m.name, (None, 0))
		rows.append(frappe._dict(
			party_type=party_type, party=m.name, party_name=m.party_name, tax_id=m.tax_id,
			created_on=getdate(m.creation), new_master=1 if new_master else 0,
			missing_list=missing, supporting_documents=d.rows,
			attached=d.attached or (1 if m.name in attached_files else 0), expired=d.expired,
			payment_terms=m.payment_terms, has_mobile=1 if has_mobile else 0, has_email=1 if has_email else 0,
			has_address=1 if has_address else 0, last_invoice=last, invoices=count, disabled=cint(m.disabled),
		))
	return rows


def cards(rows):
	out = [{"label": _("Masters Listed"), "value": len(rows), "datatype": "Int", "indicator": "Blue"},
		{"label": _("New Masters Incomplete"), "value": len([r for r in rows if r.new_master and r.missing_list]),
			"datatype": "Int", "indicator": "Red"}]
	for check in ("Document Attachment", "Payment Terms", "Mobile"):
		out.append({"label": _("No") + " " + _(check), "value": len([r for r in rows if check in r.missing_list]),
			"datatype": "Int", "indicator": "Orange"})
	return out


def summary_rows(rows):
	out = []
	for party_type in ("Customer", "Supplier"):
		mine = [r for r in rows if r.party_type == party_type]
		if not mine:
			continue
		row = {"party_type": _(party_type), "total": len(mine), "complete": len([r for r in mine if not r.missing_list])}
		for check in CHECKS:
			row[frappe.scrub(check)] = len([r for r in mine if check in r.missing_list])
		out.append(row)
	return out


def note():
	start = pd.rules_from()
	if not start:
		return _("New-master rules are off (SF Trading Settings > New-Master Rules From is blank).")
	return (_("Masters created at or after") + " " + frappe.format_value(start, "Datetime") + " "
		+ _("are marked New: invoices, orders and payments are refused for them until they are complete."))


def columns():
	return [
		{"label": _("Party Type"), "fieldname": "party_type", "fieldtype": "Data", "width": 90},
		{"label": _("Party"), "fieldname": "party", "fieldtype": "Dynamic Link", "options": "party_type", "width": 170},
		{"label": _("Name"), "fieldname": "party_name", "fieldtype": "Data", "width": 220},
		{"label": _("Tax ID / VAT"), "fieldname": "tax_id", "fieldtype": "Data", "width": 140},
		{"label": _("Missing"), "fieldname": "missing", "fieldtype": "Data", "width": 320},
		{"label": _("Gaps"), "fieldname": "missing_count", "fieldtype": "Int", "width": 60},
		{"label": _("New"), "fieldname": "new_master", "fieldtype": "Check", "width": 55},
		{"label": _("Created On"), "fieldname": "created_on", "fieldtype": "Date", "width": 100},
		{"label": _("Documents"), "fieldname": "supporting_documents", "fieldtype": "Int", "width": 85},
		{"label": _("Attached"), "fieldname": "attached", "fieldtype": "Int", "width": 75},
		{"label": _("Expired"), "fieldname": "expired", "fieldtype": "Int", "width": 70},
		{"label": _("Payment Terms"), "fieldname": "payment_terms", "fieldtype": "Link", "options": "Payment Terms Template", "width": 140},
		{"label": _("Mobile"), "fieldname": "has_mobile", "fieldtype": "Check", "width": 65},
		{"label": _("Email"), "fieldname": "has_email", "fieldtype": "Check", "width": 60},
		{"label": _("Address"), "fieldname": "has_address", "fieldtype": "Check", "width": 70},
		{"label": _("Last Invoice"), "fieldname": "last_invoice", "fieldtype": "Date", "width": 100},
		{"label": _("Invoices"), "fieldname": "invoices", "fieldtype": "Int", "width": 75},
	]


def summary_columns():
	cols = [
		{"label": _("Party Type"), "fieldname": "party_type", "fieldtype": "Data", "width": 110},
		{"label": _("Listed"), "fieldname": "total", "fieldtype": "Int", "width": 80},
		{"label": _("Complete"), "fieldname": "complete", "fieldtype": "Int", "width": 85},
	]
	for check in CHECKS:
		cols.append({"label": _("No") + " " + _(check), "fieldname": frappe.scrub(check), "fieldtype": "Int", "width": 120})
	return cols
