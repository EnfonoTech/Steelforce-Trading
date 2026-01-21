// Create Customer with ZATCA Address fields for sf_trading
// Adds a button above customer field to create new customer with address

frappe.provide("sf_trading");

sf_trading.add_create_customer_button = function(frm) {
	// Only show button for new documents (not submitted or cancelled)
	if (frm.doc.docstatus !== 0) {
		return;
	}
	
	// Check if customer field exists
	if (!frm.fields_dict.customer) {
		return;
	}
	
	// Find the customer field wrapper
	const $customer_field = frm.fields_dict.customer.$wrapper;
	if (!$customer_field || !$customer_field.length) {
		return;
	}
	
	// Check if button already exists (check in customer field's parent)
	const $parent = $customer_field.parent();
	if ($parent.find(".sf-trading-create-customer-btn").length > 0) {
		return;
	}
	
	// Create button
	const $button = $(`
		<button type="button" class="btn btn-sm btn-secondary sf-trading-create-customer-btn" style="margin-bottom: 5px;">
			<i class="fa fa-plus"></i> ${__('Create New Customer')}
		</button>
	`);
	
	$button.on('click', function() {
		sf_trading.open_create_customer_dialog(frm);
	});
	
	// Insert button before customer field wrapper
	$customer_field.before($button);
};

sf_trading.open_create_customer_dialog = function(frm) {
	// Get company from form
	let company = frm.doc.company || frappe.defaults.get_default("company");
	
	// Create dialog with customer and address fields
	let d = new frappe.ui.Dialog({
		title: __('Create New Customer'),
		fields: [
			{
				fieldtype: "Section Break",
				label: __("Customer Details")
			},
			{
				fieldname: "customer_name",
				fieldtype: "Data",
				label: __("Customer Name"),
				reqd: 1
			},
			{
				fieldname: "tax_id",
				fieldtype: "Data",
				label: __("Tax ID / VAT Registration Number")
			},
			{
				fieldname: "commercial_registration_number",
				fieldtype: "Data",
				label: __("Commercial Registration Number (CRN)")
			},
			{
				fieldtype: "Column Break"
			},
			{
				fieldname: "customer_type",
				fieldtype: "Select",
				label: __("Customer Type"),
				options: "Company\nIndividual",
				default: "Company"
			},
			{
				fieldname: "mobile_no",
				fieldtype: "Data",
				label: __("Mobile No")
			},
			{
				fieldname: "email_id",
				fieldtype: "Data",
				label: __("Email ID")
			},
			{
				fieldtype: "Section Break",
				label: __("Address Details")
			},
			{
				fieldname: "address_line1",
				fieldtype: "Data",
				label: __("Street Address Line 1")
			},
			{
				fieldname: "building_number",
				fieldtype: "Data",
				label: __("Building Number")
			},
			{
				fieldname: "district",
				fieldtype: "Data",
				label: __("District / Area")
			},
			{
				fieldtype: "Column Break"
			},
			{
				fieldname: "city",
				fieldtype: "Data",
				label: __("City")
			},
			{
				fieldname: "state",
				fieldtype: "Data",
				label: __("State / Province")
			},
			{
				fieldname: "country",
				fieldtype: "Link",
				options: "Country",
				label: __("Country"),
				default: "Saudi Arabia"
			},
			{
				fieldname: "pincode",
				fieldtype: "Data",
				label: __("Postal Code")
			}
		],
		primary_action_label: __("Create Customer"),
		primary_action: function() {
			let values = d.get_values();
			
			if (!values.customer_name) {
				frappe.msgprint({
					title: __("Required Field"),
					message: __("Customer Name is required"),
					indicator: "orange"
				});
				return;
			}
			
			// If VAT number is provided, address fields are mandatory for B2B customers
			if (values.tax_id) {
				let missing_fields = [];
				if (!values.address_line1) missing_fields.push(__("Address Line 1"));
				if (!values.city) missing_fields.push(__("City"));
				if (!values.building_number) missing_fields.push(__("Building Number"));
				if (!values.district) missing_fields.push(__("District / Area"));
				if (!values.pincode) missing_fields.push(__("Postal Code"));
				
				if (missing_fields.length > 0) {
					frappe.msgprint({
						title: __("Required Fields"),
						message: __("The following fields are mandatory when VAT Registration Number is provided (B2B customer requirement): {0}", [missing_fields.join(", ")]),
						indicator: "orange"
					});
					return;
				}
			}
			
			// Show loading
			frappe.show_alert({
				message: __("Creating customer..."),
				indicator: "blue"
			});
			
			// Create customer with address
			frappe.call({
				method: "sf_trading.api.customer.create_customer_with_address",
				args: {
					customer_name: values.customer_name,
					tax_id: values.tax_id || null,
					commercial_registration_number: values.commercial_registration_number || null,
					mobile_no: values.mobile_no || null,
					email_id: values.email_id || null,
					address_line1: values.address_line1 || null,
					building_number: values.building_number || null,
					city: values.city || null,
					state: values.state || null,
					country: values.country || "Saudi Arabia",
					pincode: values.pincode || null,
					district: values.district || null,
					company: company
				},
				callback: function(r) {
					if (r.message) {
						// Set customer in form
						frm.set_value("customer", r.message.customer);
						
						// Refresh customer field to load address
						frm.refresh_field("customer");
						
						// Show success message
						frappe.show_alert({
							message: r.message.message || __("Customer created successfully"),
							indicator: "green"
						});
						
						d.hide();
					}
				},
				error: function(r) {
					frappe.show_alert({
						message: __("Error creating customer: {0}", [r.message || r]),
						indicator: "red"
					});
				}
			});
		}
	});
	
	d.show();
};

// Hook into common doctypes with customer field
let customer_doctypes = [
	"Sales Order", "Sales Invoice", "Quotation", "Delivery Note"
];

customer_doctypes.forEach(function(doctype) {
	frappe.ui.form.on(doctype, {
		refresh: function(frm) {
			// Only show button if customer field exists and form is not submitted
			if (frm.fields_dict.customer && frm.doc.docstatus === 0) {
				sf_trading.add_create_customer_button(frm);
			}
		},
		
		customer: function(frm) {
			// Re-add button if it was removed
			if (frm.fields_dict.customer && frm.doc.docstatus === 0) {
				setTimeout(function() {
					sf_trading.add_create_customer_button(frm);
				}, 100);
			}
		}
	});
});
