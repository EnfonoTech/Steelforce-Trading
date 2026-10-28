# sf_trading/sf_trading/report/party_rule_set_diff/party_rule_set_diff.py
"""Party Rule Set Diff: does the Party Master Rule Set agree with the code's own approval gate?

For each party, what the code says it lacks before it can go for approval (master_approval.missing_requirements)
beside what the rule set says at the approval stage (party_rules.evaluate). A row that differs is a rule the
seed or an admin's edit has moved away from the code; Phase 1 switches a consumer to the rule set only when
this report is empty for it.

Customers: the credit customers by default, or all of them. Suppliers: every supplier. One document read per
party, so the report stops at "Most Parties Read".
"""

import frappe
from frappe import _
from frappe.utils import cint

from sf_trading import party_rules as pr


def execute(filters=None):
	filters = frappe._dict(filters or {})
	party_type = filters.get("party_type") or "Customer and Supplier"
	limit = cint(filters.get("limit")) or 1000
	rows, read = [], 0
	for doctype in ("Customer", "Supplier"):
		if party_type not in ("Customer and Supplier", doctype) or not frappe.has_permission(doctype, "read"):
			continue
		rules = pr.get_rules(doctype)
		if not rules:
			continue
		for name in names(doctype, filters):
			if read >= limit:
				break
			read += 1
			row = compare(doctype, name, rules)
			if row.differs or cint(filters.get("show_same")):
				rows.append(row)
	differing = len([r for r in rows if r.differs])
	cards = [
		{"label": _("Parties Read"), "value": read, "datatype": "Int", "indicator": "Blue"},
		{"label": _("Differing"), "value": differing, "datatype": "Int", "indicator": "Red" if differing else "Green"},
	]
	return columns(), rows, note(read, limit), None, cards


def names(doctype, filters) -> list:
	if doctype == "Customer" and filters.get("customers") != "All Customers":
		return frappe.get_all("Customer Credit Limit", filters={"parenttype": "Customer", "credit_limit": [">", 0]},
			pluck="parent", distinct=True, order_by="parent")
	return frappe.get_all(doctype, filters={"disabled": 0}, pluck="name", order_by="name")


def compare(doctype, name, rules):
	doc = frappe.get_doc(doctype, name)
	legacy = pr.legacy_approval_missing(doc)
	engine = [f.text for f in pr.evaluate(doc, "approval", rules)]
	only_legacy = [t for t in legacy if t not in engine]
	only_rules = [t for t in engine if t not in legacy]
	return frappe._dict(
		party_type=doctype, party=name, party_name=doc.get(doctype.lower() + "_name"),
		legacy=", ".join(legacy), rule_set=", ".join(engine), only_legacy=", ".join(only_legacy),
		only_rule_set=", ".join(only_rules), same_order=1 if legacy == engine else 0,
		differs=1 if (only_legacy or only_rules) else 0,
	)


def note(read, limit):
	text = _("Compared at the approval stage, against master_approval.missing_requirements.")
	if read >= limit:
		text += " " + _("Stopped at {0} parties: raise Most Parties Read to see more.").format(limit)
	return text


def columns():
	return [
		{"label": _("Party Type"), "fieldname": "party_type", "fieldtype": "Data", "width": 90},
		{"label": _("Party"), "fieldname": "party", "fieldtype": "Dynamic Link", "options": "party_type", "width": 170},
		{"label": _("Name"), "fieldname": "party_name", "fieldtype": "Data", "width": 200},
		{"label": _("Code Says Missing"), "fieldname": "legacy", "fieldtype": "Data", "width": 300},
		{"label": _("Rule Set Says Missing"), "fieldname": "rule_set", "fieldtype": "Data", "width": 300},
		{"label": _("Only in the Code"), "fieldname": "only_legacy", "fieldtype": "Data", "width": 220},
		{"label": _("Only in the Rule Set"), "fieldname": "only_rule_set", "fieldtype": "Data", "width": 220},
		{"label": _("Same Order"), "fieldname": "same_order", "fieldtype": "Check", "width": 80},
		{"label": _("Differs"), "fieldname": "differs", "fieldtype": "Check", "width": 70},
	]
