// Warehouse Stock Popup for sf_trading
// Shows a popup with warehouse stock when adding an item to item table

frappe.provide("sf_trading");

sf_trading.show_warehouse_stock_popup = function(frm, item_row, force_show) {
	// Check if item_code is set
	if (!item_row.item_code) {
		return;
	}
	
	// Check if warehouse field exists
	if (!frappe.meta.has_field(item_row.doctype, "warehouse")) {
		return;
	}
	
	// Get fresh reference to check current warehouse value
	// This is important because ERPNext may have set it asynchronously
	let current_row = locals[item_row.doctype][item_row.name];
	if (!current_row) {
		return;
	}
	
	// Only show popup if warehouse is not already set (unless forced)
	if (current_row.warehouse && !force_show) {
		return;
	}
	
	// Get company from form
	let company = frm.doc.company;
	if (!company) {
		return;
	}
	
	// Prevent multiple popups from showing for the same item
	if (current_row._sf_trading_popup_shown) {
		return;
	}
	
	// Mark that popup is being shown
	current_row._sf_trading_popup_shown = true;
	
	// Fetch warehouse stock data (removed loading alert to reduce lag)
	frappe.call({
		method: "sf_trading.api.warehouse_stock.get_item_warehouse_stock",
		args: {
			item_code: item_row.item_code,
			company: company
		},
		callback: function(r) {
			// Clear flag on callback
			let callback_row = locals[item_row.doctype][item_row.name];
			if (callback_row) {
				delete callback_row._sf_trading_popup_shown;
			}
			
			if (r.message && r.message.length > 0) {
				// Show dialog with warehouse stock
				sf_trading.show_warehouse_dialog(frm, item_row, r.message);
			} else {
				// Only show alert if no warehouses found
				frappe.show_alert({
					message: __("No warehouses found"),
					indicator: "orange"
				});
			}
		},
		error: function(r) {
			// Clear flag on error
			let error_row = locals[item_row.doctype][item_row.name];
			if (error_row) {
				delete error_row._sf_trading_popup_shown;
			}
			
			frappe.show_alert({
				message: __("Error loading warehouse stock"),
				indicator: "red"
			});
		}
	});
};

sf_trading.show_warehouse_dialog = function(frm, item_row, stock_data) {
	// Check if warehouse field exists in the item doctype
	if (!frappe.meta.has_field(item_row.doctype, "warehouse")) {
		// Warehouse field doesn't exist, skip popup
		return;
	}
	
	// Get current warehouse if set
	let current_row = locals[item_row.doctype][item_row.name];
	let current_warehouse = current_row ? (current_row.warehouse || "") : "";
	
	// Create dialog
	let dialog = new frappe.ui.Dialog({
		title: __("Select Warehouse - {0}", [item_row.item_code]),
		size: "large",
		fields: [
			{
				fieldtype: "HTML",
				fieldname: "stock_table",
				options: sf_trading.get_warehouse_stock_html(stock_data, current_warehouse)
			}
		],
		primary_action_label: current_warehouse ? __("Keep Current") : __("Cancel"),
		primary_action: function() {
			dialog.hide();
		}
	});
	
	// Add click handlers to warehouse rows
	dialog.$wrapper.find(".warehouse-row").on("click", function() {
		let warehouse = $(this).data("warehouse");
		let stock_qty = $(this).data("stock-qty");
		
		// Get fresh reference to item row
		let current_row = locals[item_row.doctype][item_row.name];
		if (current_row) {
			// Set warehouse on the item row
			frappe.model.set_value(item_row.doctype, item_row.name, "warehouse", warehouse);
			
			// Clear the popup flag so it can show again if needed
			delete current_row._sf_trading_popup_shown;
			
			// Refresh the field
			frm.refresh_field("items");
			
			// Show confirmation
			frappe.show_alert({
				message: __("Warehouse {0} selected (Stock: {1})", [warehouse, format_number(stock_qty, null, {precision: 2})]),
				indicator: "green"
			});
		}
		
		dialog.hide();
	});
	
	// Clear flag when dialog is closed without selection
	dialog.onhide = function() {
		let current_row = locals[item_row.doctype][item_row.name];
		if (current_row && !current_row.warehouse) {
			// Only clear flag if warehouse is still not set
			delete current_row._sf_trading_popup_shown;
		}
	};
	
	dialog.show();
};

sf_trading.get_warehouse_stock_html = function(stock_data, current_warehouse) {
	current_warehouse = current_warehouse || "";
	
	let html = `
		<div class="warehouse-stock-list" style="max-height: 400px; overflow-y: auto;">
			<table class="table table-bordered table-hover" style="margin-bottom: 0;">
				<thead>
					<tr style="background-color: #f5f5f5;">
						<th style="padding: 8px; width: 60%;">${__("Warehouse")}</th>
						<th style="padding: 8px; text-align: right; width: 40%;">${__("Stock Qty")}</th>
					</tr>
				</thead>
				<tbody>
	`;
	
	stock_data.forEach(function(item) {
		let stock_color = item.stock_qty > 0 ? "green" : "gray";
		let stock_indicator = item.stock_qty > 0 ? "●" : "○";
		let is_current = item.warehouse === current_warehouse;
		let row_style = is_current 
			? "cursor: pointer; transition: background-color 0.2s; background-color: #e3f2fd; border-left: 3px solid #2196f3;"
			: "cursor: pointer; transition: background-color 0.2s;";
		let hover_bg = is_current ? "#bbdefb" : "#f0f0f0";
		let default_bg = is_current ? "#e3f2fd" : "white";
		
		html += `
			<tr class="warehouse-row" 
				data-warehouse="${item.warehouse}" 
				data-stock-qty="${item.stock_qty}"
				style="${row_style}"
				onmouseover="this.style.backgroundColor='${hover_bg}'"
				onmouseout="this.style.backgroundColor='${default_bg}'">
				<td style="padding: 10px;">
					<span style="color: ${stock_color}; margin-right: 8px;">${stock_indicator}</span>
					<strong>${item.warehouse_name || item.warehouse}</strong>
					${is_current ? '<span style="color: #2196f3; margin-left: 8px; font-size: 11px;">(Current)</span>' : ''}
				</td>
				<td style="padding: 10px; text-align: right;">
					<span style="color: ${stock_color}; font-weight: bold;">
						${format_number(item.stock_qty, null, {precision: 2})}
					</span>
				</td>
			</tr>
		`;
	});
	
	html += `
				</tbody>
			</table>
		</div>
		<div style="margin-top: 15px; padding: 10px; background-color: #f9f9f9; border-radius: 4px;">
			<small style="color: #666;">
				${current_warehouse 
					? __("Current warehouse is highlighted. Click on any warehouse row to change it.")
					: __("Click on a warehouse row to select it")}
			</small>
		</div>
	`;
	
	return html;
};

// Debounce helper to prevent multiple rapid calls
sf_trading.debounce = function(func, wait) {
	let timeout;
	return function() {
		let context = this;
		let args = arguments;
		clearTimeout(timeout);
		timeout = setTimeout(function() {
			func.apply(context, args);
		}, wait);
	};
};

// Store pending popup timeouts to cancel them if needed
sf_trading.pending_popups = {};

// Hook into item_code onchange for common child doctypes with warehouse fields
// This will work for Sales Order Item, Sales Invoice Item, Purchase Order Item, etc.
let item_doctypes = [
	"Sales Order Item", "Sales Invoice Item", "Purchase Order Item", 
	"Purchase Invoice Item", "Quotation Item", "Delivery Note Item", 
	"Purchase Receipt Item", "Material Request Item", "Stock Entry Detail",
	"Work Order Item"
];

item_doctypes.forEach(function(child_doctype) {
	frappe.ui.form.on(child_doctype, {
		item_code: function(frm, cdt, cdn) {
			let item_row = locals[cdt][cdn];
			
			// Only show popup if:
			// 1. item_code is set
			// 2. warehouse field exists
			// 3. company is set
			if (item_row.item_code && 
				frappe.meta.has_field(item_row.doctype, "warehouse") &&
				frm.doc.company) {
				
				// Cancel any pending popup for this row
				let row_key = item_row.name || cdn;
				if (sf_trading.pending_popups[row_key]) {
					clearTimeout(sf_trading.pending_popups[row_key]);
					delete sf_trading.pending_popups[row_key];
				}
				
				// Mark that item_code was just set - this helps us detect auto-set warehouses
				item_row._sf_trading_item_just_added = true;
				item_row._sf_trading_item_added_time = Date.now();
				
				// Single check after ERPNext finishes processing (reduced delay)
				let showPopupOnce = function() {
					let current_row = locals[cdt][cdn];
					
					if (!current_row || !current_row.item_code) {
						return; // Row was deleted or item_code cleared
					}
					
					// Clear the flag
					delete current_row._sf_trading_item_just_added;
					delete current_row._sf_trading_item_added_time;
					delete sf_trading.pending_popups[row_key];
					
					// Show popup (will handle both empty and auto-set warehouse cases)
					sf_trading.show_warehouse_stock_popup(frm, current_row, current_row.warehouse ? true : false);
				};
				
				// Single timeout - reduced from multiple checks
				sf_trading.pending_popups[row_key] = setTimeout(showPopupOnce, 1200);
			}
		},
		
		warehouse: function(frm, cdt, cdn) {
			let item_row = locals[cdt][cdn];
			
			// If warehouse was auto-set right after item_code was added (within 2 seconds), show popup
			if (item_row._sf_trading_item_just_added && 
				item_row.warehouse && 
				item_row.item_code &&
				frm.doc.company &&
				frappe.meta.has_field(item_row.doctype, "warehouse") &&
				item_row._sf_trading_item_added_time &&
				(Date.now() - item_row._sf_trading_item_added_time) < 2000) {
				
				// Cancel the pending popup from item_code handler
				let row_key = item_row.name || cdn;
				if (sf_trading.pending_popups[row_key]) {
					clearTimeout(sf_trading.pending_popups[row_key]);
					delete sf_trading.pending_popups[row_key];
				}
				
				// Clear the flag
				delete item_row._sf_trading_item_just_added;
				delete item_row._sf_trading_item_added_time;
				
				// Show popup immediately (reduced delay)
				setTimeout(function() {
					let current_row = locals[cdt][cdn];
					if (current_row && current_row.warehouse && current_row.item_code) {
						// Show popup with current warehouse, but allow changing it
						sf_trading.show_warehouse_stock_popup(frm, current_row, true);
					}
				}, 300);
			}
		}
	});
});
