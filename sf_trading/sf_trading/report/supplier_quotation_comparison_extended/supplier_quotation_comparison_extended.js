// Copyright (c) 2026, Enfono Technologies and contributors
// For license information, please see license.txt

frappe.query_reports["Supplier Quotation Comparison Extended"] = {
	filters: [
		{
			fieldname: "company",
			label: __("Company"),
			fieldtype: "Link",
			options: "Company",
			default: frappe.defaults.get_user_default("Company"),
			reqd: 1,
		},
		{
			fieldname: "from_date",
			label: __("From Date"),
			fieldtype: "Date",
			default: frappe.datetime.add_months(frappe.datetime.get_today(), -3),
			reqd: 1,
		},
		{
			fieldname: "to_date",
			label: __("To Date"),
			fieldtype: "Date",
			default: frappe.datetime.get_today(),
			reqd: 1,
		},
		{
			// a Select with a word for every choice: query_report.js drops a filter whose value is empty,
			// so a "show everything" blank option would never reach the server
			fieldname: "view",
			label: __("View"),
			fieldtype: "Select",
			options: ["Item-wise Comparison", "Supplier-wise Summary", "RFQ Gaps"].join("\n"),
			default: "Item-wise Comparison",
			reqd: 1,
		},
		{
			fieldname: "request_for_quotation",
			label: __("Request for Quotation"),
			fieldtype: "Link",
			options: "Request for Quotation",
			get_query: () => {
				return {
					filters: {
						docstatus: ["<", 2],
						company: frappe.query_report.get_filter_value("company"),
					},
				};
			},
		},
		{
			fieldname: "item_code",
			label: __("Item"),
			fieldtype: "Link",
			options: "Item",
		},
		{
			fieldname: "material_request",
			label: __("Material Request"),
			fieldtype: "Link",
			options: "Material Request",
			get_query: () => {
				return {
					filters: {
						docstatus: 1,
						material_request_type: "Purchase",
						company: frappe.query_report.get_filter_value("company"),
					},
				};
			},
		},
		{
			fieldname: "supplier",
			label: __("Supplier"),
			fieldtype: "MultiSelectList",
			options: "Supplier",
			get_data: function (txt) {
				return erpnext.utils.get_link_options("Supplier", txt);
			},
		},
		{
			fieldname: "supplier_quotation",
			label: __("Supplier Quotation"),
			fieldtype: "MultiSelectList",
			options: "Supplier Quotation",
			get_data: function (txt) {
				return erpnext.utils.get_link_options("Supplier Quotation", txt, { docstatus: ["<", 2] });
			},
		},
		{
			// A quotation is Import when it is in a currency other than the company's, or its
			// supplier's own country is set and is not the company's: see the report .py docstring.
			fieldname: "purchase_origin",
			label: __("Purchase Origin"),
			fieldtype: "Select",
			options: ["Local and Import", "Local Only", "Import Only"].join("\n"),
			default: "Local and Import",
			reqd: 1,
		},
		{
			// The landed charges on each quotation (the rows of its Taxes and Charges whose Category is
			// Valuation, set up as a Purchase Taxes and Charges Template and applied by a Tax Rule) are added
			// to its price before the quotations are compared. No Charges compares the price alone.
			// A Select with a word for every choice: an unticked or zero filter never reaches the server.
			fieldname: "import_charges",
			label: __("Landed Charges"),
			fieldtype: "Select",
			options: ["Quotation Charges", "No Charges"].join("\n"),
			default: "Quotation Charges",
			reqd: 1,
		},
		{
			// A percent of the price added to IMPORT quotations on top, for charges no quotation carries
			// (customs duty, clearance). 0 adds nothing.
			fieldname: "import_charges_pct",
			label: __("Extra % on Imports"),
			fieldtype: "Float",
			default: 0,
		},
		{
			// Lowest Cost picks the lowest landed cost; Weighted Score weighs price, lead time and validity.
			// A Select with a word for every choice: an empty or zero filter never reaches the server.
			fieldname: "recommend_by",
			label: __("Recommend By"),
			fieldtype: "Select",
			options: ["Lowest Cost", "Weighted Score"].join("\n"),
			default: "Lowest Cost",
			reqd: 1,
		},
		{
			fieldname: "weight_price",
			label: __("Weight: Price"),
			fieldtype: "Int",
			default: 70,
			description: __("Used when Recommend By is Weighted Score."),
		},
		{
			fieldname: "weight_lead_time",
			label: __("Weight: Lead Time"),
			fieldtype: "Int",
			default: 20,
		},
		{
			fieldname: "weight_validity",
			label: __("Weight: Validity"),
			fieldtype: "Int",
			default: 10,
		},
		{
			fieldname: "status",
			label: __("Quotation Status"),
			fieldtype: "Select",
			options: ["Submitted", "Draft", "Draft and Submitted"].join("\n"),
			default: "Submitted",
			reqd: 1,
		},
		{
			fieldname: "include_expired",
			label: __("Include Expired"),
			fieldtype: "Check",
			default: 0,
		},
	],

	formatter: function (value, row, column, data, default_formatter) {
		value = default_formatter(value, row, column, data);
		if (!data) return value;

		if (column.fieldname === "valid_till" && data.valid_till) {
			const days = frappe.datetime.get_diff(data.valid_till, frappe.datetime.nowdate());
			if (days <= 1) {
				value = `<div style="color:red">${value}</div>`;
			} else if (days <= 7) {
				value = `<div style="color:darkorange">${value}</div>`;
			}
		}

		// the recommended line is the answer, so it is the one thing on the page that is green and bold
		if (data.recommended && ["landed_unit_cost", "landed_total", "rank", "supplier_name", "remarks"].includes(column.fieldname)) {
			return `<span style="color: var(--green-600); font-weight: 600">${value}</span>`;
		}

		// an import is worth spotting without reading the column: its price is an estimate of landed cost
		if (column.fieldname === "origin" && data.origin === "Import") {
			return `<span style="color: var(--blue-600); font-weight: 500">${value}</span>`;
		}

		if (column.fieldname === "rule_label" && data.rule_label) {
			const short = data.rule_label.startsWith(__("Short")) || data.rule_label.startsWith(__("Not raised"));
			return `<span style="color: var(${short ? "--red-600" : "--green-600"})">${value}</span>`;
		}

		// a quotation that is out of the running, or a supplier that never answered, reads as a problem
		if (["remarks", "response"].includes(column.fieldname)) {
			const out = [__("Expired"), __("Stopped"), __("No price"), __("No response"), __("No quotation received")];
			if (out.includes(data[column.fieldname])) {
				return `<span style="color: var(--red-600)">${value}</span>`;
			}
		}

		if (column.fieldname === "recommendation" && data.recommendation) {
			return `<span style="color: var(--green-600); font-weight: 500">${value}</span>`;
		}
		return value;
	},

	onload: (report) => {
		report.page.add_inner_button(
			__("Create Purchase Orders"),
			() => frappe.query_reports["Supplier Quotation Comparison Extended"].make_orders(report),
			__("Actions")
		);
	},

	// One draft Purchase Order per recommended quotation, from the recommended lines only. The server works the
	// recommendation out again from these filters: what is ticked here is only a choice among what it offers.
	make_orders: (report) => {
		const SQCE_METHOD =
			"sf_trading.sf_trading.report.supplier_quotation_comparison_extended.supplier_quotation_comparison_extended";
		const filters = report.get_values();
		frappe.call({
			method: `${SQCE_METHOD}.get_recommended_orders`,
			args: { filters },
			freeze: true,
			freeze_message: __("Working out the recommended orders"),
			callback: (r) => {
				const orders = r.message || [];
				if (!orders.length) {
					frappe.msgprint(__("Nothing recommended that is not already on a Purchase Order."));
					return;
				}
				const escape = frappe.utils.escape_html;
				const fields = [
					{
						fieldtype: "HTML",
						options: `<p class="text-muted">${__(
							"One draft Purchase Order per quotation, with the recommended items only."
						)}</p>`,
					},
				];
				orders.forEach((order, index) => {
					const total = format_currency(order.total, order.currency);
					fields.push({
						fieldtype: "Check",
						fieldname: `order_${index}`,
						label: `${escape(order.quotation)} · ${escape(order.supplier_name || order.supplier)} · ${__(
							"{0} item(s)",
							[order.item_count]
						)} · ${total}`,
						default: order.submitted ? 1 : 0,
						read_only: order.submitted ? 0 : 1,
						description: order.submitted ? "" : __("Draft quotation: submit it before ordering."),
					});
				});

				const dialog = new frappe.ui.Dialog({
					title: __("Create Purchase Orders"),
					fields: fields,
					primary_action_label: __("Create Drafts"),
					primary_action: (values) => {
						const chosen = orders
							.filter((order, index) => values[`order_${index}`])
							.map((order) => order.quotation);
						if (!chosen.length) {
							frappe.msgprint(__("Tick at least one quotation."));
							return;
						}
						dialog.hide();
						frappe.call({
							method: `${SQCE_METHOD}.make_purchase_orders`,
							args: { filters, quotations: chosen },
							freeze: true,
							freeze_message: __("Creating draft Purchase Orders"),
							callback: (res) => {
								const lines = (res.message || []).map((row) =>
									row.purchase_order
										? `<li>${escape(row.quotation)}: <a href="/app/purchase-order/${encodeURIComponent(
												row.purchase_order
										  )}">${escape(row.purchase_order)}</a>${
												row.note ? ` <span class="text-muted">(${escape(row.note)})</span>` : ""
										  }</li>`
										: `<li>${escape(row.quotation)}: <span class="text-danger">${row.error}</span></li>`
								);
								frappe.msgprint({
									title: __("Purchase Orders"),
									message: `<ul>${lines.join("")}</ul>`,
									indicator: "green",
								});
								report.refresh();
							},
						});
					},
				});
				dialog.show();
			},
		});
	},
};
