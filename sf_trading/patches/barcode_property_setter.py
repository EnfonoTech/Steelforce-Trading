import frappe


def execute():
	"""Show Barcode column in Sales Invoice Item grid list view."""
	if frappe.db.exists("Property Setter", "Sales Invoice Item-barcode-in_list_view"):
		return
	ps = frappe.get_doc(
		{
			"doctype": "Property Setter",
			"doc_type": "Sales Invoice Item",
			"doctype_or_field": "DocField",
			"field_name": "barcode",
			"property": "in_list_view",
			"property_type": "Check",
			"value": "1",
			"module": "Sf Trading",
		}
	)
	ps.insert(ignore_permissions=True)
	frappe.db.commit()
