import frappe
from frappe import _
from frappe.utils import cstr
from frappe.core.doctype.user_permission.user_permission import get_permitted_documents


def _get_default_customer_group():
	permitted = get_permitted_documents("Customer Group")
	return (permitted[0] if permitted else None) or frappe.db.get_single_value("Selling Settings", "customer_group") or "All Customer Groups"


def _get_default_territory():
	permitted = get_permitted_documents("Territory")
	return (permitted[0] if permitted else None) or frappe.db.get_single_value("Selling Settings", "territory") or "All Territories"


@frappe.whitelist()
def create_customer_with_address(
	customer_name,
	mobile_no=None,
	customer_type="Individual",
	email_id=None,
	country=None,
	default_currency=None,
	tax_id=None,
	commercial_registration_number=None,
	address_line1=None,
	building_number=None,
	city=None,
	state=None,
	pincode=None,
	district=None
):
	"""
	Create a new customer with address in one go (for ZATCA compliance).
	
	Args:
		customer_name: Customer name (required)
		tax_id: Tax ID / VAT Registration Number (saved to custom_vat_registration_number)
		commercial_registration_number: Commercial Registration Number (CRN) - saved to custom_commercial_registration_number field
		mobile_no: Mobile number
		email_id: Email ID
		address_line1: Street address line 1
		building_number: Building number
		city: City
		state: State/Province
		country: Country (defaults to Saudi Arabia)
		pincode: Postal code
		district: District/Area
		company: Company name
	
	Returns:
		Dictionary with customer name and address name
	"""
	if not customer_name:
		frappe.throw(_("Customer Name is required"))
	if not mobile_no:
		frappe.throw(_("Mobile No is required"))
	
	# If VAT number is provided, address fields are mandatory for B2B customers
	if tax_id:
		missing_fields = []
		if not address_line1:
			missing_fields.append(_("Address Line 1"))
		if not city:
			missing_fields.append(_("City"))
		if not building_number:
			missing_fields.append(_("Building Number"))
		if not district:
			missing_fields.append(_("District / Area"))
		if not pincode:
			missing_fields.append(_("Postal Code"))
		
		if missing_fields:
			frappe.throw(_("The following fields are mandatory when VAT Registration Number is provided (B2B customer requirement): {0}").format(", ".join(missing_fields)))
	
	# Get country and default_currency from default company if not provided
	if not country or not default_currency:
		permitted_companies = get_permitted_documents("Company")
		company = (permitted_companies[0] if permitted_companies else None) or frappe.defaults.get_user_default("company")
		if not company:
			frappe.throw(_("Please set a default company"))
		company_doc = frappe.get_cached_doc("Company", company)
		if not country:
			country = company_doc.country or "Saudi Arabia"
		if not default_currency:
			default_currency = company_doc.default_currency
	
	# Create Customer
	customer_doc = frappe.get_doc({
		"doctype": "Customer",
		"customer_name": customer_name,
		"customer_type": customer_type or "Individual",
		"customer_group": _get_default_customer_group(),
		"territory": _get_default_territory(),
		"default_currency": default_currency,
		"tax_id": tax_id,
		"mobile_no": mobile_no,
		"email_id": email_id
	})
	
	# Set custom_vat_registration_number if field exists
	if tax_id and frappe.db.has_column("Customer", "custom_vat_registration_number"):
		customer_doc.custom_vat_registration_number = tax_id
	
	# Set custom_commercial_registration_number if field exists
	if commercial_registration_number and frappe.db.has_column("Customer", "custom_commercial_registration_number"):
		customer_doc.custom_commercial_registration_number = str(commercial_registration_number).strip()
	
	customer_doc.insert(ignore_permissions=True)
	customer_name_id = customer_doc.name
	
	# Create Address if address fields are provided
	address_name = None
	if address_line1 or city:
		address_doc = frappe.get_doc({
			"doctype": "Address",
			"address_title": customer_name,
			"address_type": "Billing",
			"address_line1": address_line1 or "",
			"city": city or "",
			"state": state or "",
			"country": country,
			"pincode": pincode or "",
			"is_primary_address": 1,
			"is_shipping_address": 1
		})
		
		# Add custom fields for ZATCA if they exist
		if building_number:
			if frappe.db.has_column("Address", "custom_building_number"):
				address_doc.custom_building_number = building_number
		
		if district:
			if frappe.db.has_column("Address", "custom_area"):
				address_doc.custom_area = district
		
		address_doc.append("links", {
			"link_doctype": "Customer",
			"link_name": customer_name_id
		})
		
		address_doc.insert(ignore_permissions=True)
		address_name = address_doc.name
		
		# Set as default address
		frappe.db.set_value("Customer", customer_name_id, "customer_primary_address", address_name)
	
	frappe.db.commit()
	
	return {
		"customer": customer_name_id,
		"customer_name": customer_name,
		"address": address_name,
		"message": _("Customer {0} created successfully").format(customer_name)
	}
