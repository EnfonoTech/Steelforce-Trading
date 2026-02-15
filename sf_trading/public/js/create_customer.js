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
	// Company: form default, else first permitted from user permissions (default first), else user default
	let company = frm.doc.company;
	if (!company) {
		let permitted = (frappe.boot.user.user_permissions || {})["Company"] || [];
		let sorted = permitted.slice().sort((a, b) => (b.is_default || 0) - (a.is_default || 0));
		company = sorted.length ? sorted[0].doc : frappe.defaults.get_default("company");
	}
	frappe.db.get_value("Company", company, ["country", "default_currency"], function(r) {
		let country = (r && r.country) || null;
		let default_currency = (r && r.default_currency) || null;
		sf_trading._show_create_customer_dialog(frm, country, default_currency);
	});
};

sf_trading._show_create_customer_dialog = function(frm, country, default_currency) {
	// Create dialog with customer fields
	let d = new frappe.ui.Dialog({
		title: __('Create New Customer'),
		fields: [
			{
				fieldname: "customer_name",
				fieldtype: "Data",
				label: __("Customer Name"),
				reqd: 1
			},
			{
				fieldname: "mobile_no",
				fieldtype: "Data",
				label: __("Mobile No"),
				reqd: 1
			},
			{
				fieldname: "email_id",
				fieldtype: "Data",
				label: __("Email ID")
			}
		],
		primary_action_label: __("Create Customer"),
		primary_action: function() {
			let values = d.get_values();
			
			if (!values.customer_name || !values.mobile_no) {
				frappe.msgprint({
					title: __("Required Fields"),
					message: __("Customer Name and Mobile No are required"),
					indicator: "orange"
				});
				return;
			}
			
			// Show loading
			frappe.show_alert({
				message: __("Creating customer..."),
				indicator: "blue"
			});
			
			// Create customer
			frappe.call({
				method: "sf_trading.api.customer.create_customer_with_address",
				args: {
					customer_name: values.customer_name,
					mobile_no: values.mobile_no,
					email_id: values.email_id || null,
					country: country,
					default_currency: default_currency
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
