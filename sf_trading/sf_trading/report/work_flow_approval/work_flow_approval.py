
# Copyright (c) 2026, enfono and contributors
# For license information, please see license.txt
import frappe


def execute(filters=None):
    return get_columns(), get_data(filters)


def get_columns():
    return [
        { "label": "Company", "fieldname": "company", "fieldtype": "Link", "options": "Company", "width": 150 },
        { "label": "DocType", "fieldname": "doctype", "fieldtype": "Data", "width": 150 },
        { "label": "Document", "fieldname": "name", "fieldtype": "Dynamic Link", "options": "doctype", "width": 200 },
        { "label": "Status", "fieldname": "workflow_state", "fieldtype": "Data", "width": 150 },
        { "label": "Created Date", "fieldname": "creation", "fieldtype": "Datetime", "width": 180 }
    ]


def get_data(filters):
    if not filters.get("doctype"):
        return []

    doctype = filters.get("doctype")

    if not frappe.db.has_column(doctype, "workflow_state"):
        return []

    has_company = frappe.db.has_column(doctype, "company")
    conditions = "workflow_state = 'Pending'"
    values = {}

    if filters.get("company") and has_company:
        conditions += " AND company = %(company)s"
        values["company"] = filters["company"]

    if filters.get("from_date"):
        conditions += " AND creation >= %(from_date)s"
        values["from_date"] = filters["from_date"]

    if filters.get("to_date"):
        conditions += " AND creation <= %(to_date)s"
        values["to_date"] = filters["to_date"]

    fields = ("company, " if has_company else "") + "name, workflow_state, creation"

    data = frappe.db.sql(f"""
        SELECT {fields} FROM `tab{doctype}`
        WHERE {conditions} ORDER BY creation DESC
    """, values, as_dict=True)

    for d in data:
        d["doctype"] = doctype
        if not has_company:
            d["company"] = ""

    return data


@frappe.whitelist()
def get_workflow_actions(doctype):
    wf = frappe.db.get_all("Workflow",
        filters={"document_type": doctype, "is_active": 1},
        fields=["name"], limit=1)

    if not wf:
        return []

    seen, actions = set(), []
    for t in frappe.get_doc("Workflow", wf[0].name).transitions:
        if t.action and t.action not in seen:
            seen.add(t.action)
            actions.append(t.action)
    return actions


@frappe.whitelist()
def apply_bulk_workflow(docs, action):
    import json
    from frappe.model.workflow import apply_workflow

    results = []
    for d in json.loads(docs):
        try:
            apply_workflow(frappe.get_doc(d["doctype"], d["name"]), action)
            results.append(f" {d['name']} — Success")
        except Exception as e:
            results.append(f" {d['name']} — {str(e)}")

    frappe.db.commit()
    return "<br>".join(results)