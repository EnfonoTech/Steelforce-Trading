import frappe
from frappe import _


@frappe.whitelist()
def get_last_selling_rate(item_code, company=None):
	"""
	Get last selling rate for an item from submitted Sales Invoices.
	
	Args:
		item_code: Item code to get last selling rate for
		company: Company name (optional, will use default if not provided)
	
	Returns:
		Dictionary with last selling rate details or None
	"""
	if not item_code:
		frappe.throw(_("Item Code is required"))
	
	# Get company from context or use default
	if not company:
		company = frappe.defaults.get_user_default("company")
	
	# Build filters
	filters = {
		"item_code": item_code,
		"docstatus": 1  # Only submitted invoices
	}
	
	# Add company filter if provided
	if company:
		# Get sales invoices for the company
		sales_invoices = frappe.get_all(
			"Sales Invoice",
			filters={"company": company, "docstatus": 1},
			fields=["name"],
			pluck="name"
		)
		
		if not sales_invoices:
			return None
		
		filters["parent"] = ["in", sales_invoices]
	
	# Get last selling rate from Sales Invoice Items
	last_sale = frappe.db.sql("""
		SELECT 
			sii.rate AS selling_rate,
			sii.base_rate AS base_selling_rate,
			sii.amount AS amount,
			sii.qty AS qty,
			sii.uom AS uom,
			si.posting_date AS posting_date,
			si.name AS sales_invoice,
			si.customer AS customer,
			si.customer_name AS customer_name,
			si.currency AS currency,
			si.company AS company
		FROM `tabSales Invoice Item` sii
		INNER JOIN `tabSales Invoice` si ON si.name = sii.parent
		WHERE sii.item_code = %(item_code)s
			AND si.docstatus = 1
		ORDER BY si.posting_date DESC, si.name DESC, sii.idx ASC
		LIMIT 1
	""", {"item_code": item_code}, as_dict=True)
	
	if last_sale and len(last_sale) > 0:
		return last_sale[0]
	
	return None


@frappe.whitelist()
def get_item_selling_history(item_code=None, company=None, limit=20):
	"""
	Get item selling history (last selling rates only, no purchase rates).
	
	Args:
		item_code: Item code to get history for (optional)
		company: Company name (optional)
		limit: Number of records to return (default 20)
	
	Returns:
		List of dictionaries with selling history
	"""
	limit = int(limit or 20)
	where = ["si.docstatus = 1"]
	params = {"limit": limit}
	
	if item_code:
		where.append("sii.item_code = %(item_code)s")
		params["item_code"] = item_code
	
	if company:
		where.append("si.company = %(company)s")
		params["company"] = company
	
	where_sql = " AND ".join(where)
	
	rows = frappe.db.sql("""
		SELECT
			si.posting_date,
			si.name AS sales_invoice,
			si.customer,
			si.customer_name,
			si.company,
			sii.item_code,
			sii.item_name,
			sii.qty,
			sii.uom,
			sii.rate AS selling_rate,
			sii.base_rate AS base_selling_rate,
			sii.amount AS selling_amount,
			si.currency
		FROM `tabSales Invoice Item` sii
		INNER JOIN `tabSales Invoice` si ON si.name = sii.parent
		WHERE {where_sql}
		ORDER BY si.posting_date DESC, si.name DESC, sii.idx ASC
		LIMIT %(limit)s
	""".format(where_sql=where_sql), params, as_dict=True)
	
	return rows
