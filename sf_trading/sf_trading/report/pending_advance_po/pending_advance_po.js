// Copyright (c) 2026, Enfono Technologies and contributors
// For license information, please see license.txt

frappe.query_reports["Pending Advance PO"] = {
	filters: [
		{
			fieldname: "company",
			label: __("Company"),
			fieldtype: "Link",
			options: "Company",
			default: frappe.defaults.get_user_default("Company"),
			reqd: 1,
		},
		// The advance is always its balance right now -- the advance ledger carries no
		// posting date, so there is nothing to rebuild an "as on" answer from. These
		// two bound the order date instead, so a single period can be checked on its own.
		{
			fieldname: "from_date",
			label: __("PO From Date"),
			fieldtype: "Date",
		},
		{
			fieldname: "to_date",
			label: __("PO To Date"),
			fieldtype: "Date",
		},
		{
			fieldname: "supplier",
			label: __("Supplier"),
			fieldtype: "Link",
			options: "Supplier",
		},
		{
			fieldname: "include_closed",
			label: __("Include Closed Orders"),
			fieldtype: "Check",
			default: 0,
		},
		{
			// Orders whose advance is still sitting against them after the invoice was
			// booked. They are not pending, but they are why the advance total on the
			// Purchase Order list is larger than this report's.
			fieldname: "include_invoiced",
			label: __("Include Already Invoiced"),
			fieldtype: "Check",
			default: 0,
		},
	],

	// Puts a tick box on every row so an order can be picked and invoiced without
	// leaving the report.
	get_datatable_options: function (options) {
		return Object.assign({}, options, { checkboxColumn: true });
	},

	onload: function (report) {
		report.page.add_inner_button(__("Create Purchase Invoice"), function () {
			// the total row comes back as a plain array, so it has no `purchase_order`
			const picked = (frappe.query_report.get_checked_items() || []).filter(
				(row) => row && row.purchase_order
			);

			if (!picked.length) {
				frappe.msgprint({
					title: __("Nothing Selected"),
					indicator: "orange",
					message: __("Tick the order you want to invoice, then press the button again."),
				});
				return;
			}

			if (picked.length > 1) {
				frappe.msgprint({
					title: __("One Order at a Time"),
					indicator: "orange",
					message: __(
						"A Purchase Invoice is mapped from a single Purchase Order, so please tick one row. Invoice them one after another, or raise the Purchase Invoice first and pull several orders into it from the invoice itself."
					),
				});
				return;
			}

			if (picked[0].draft_invoice) {
				frappe.msgprint({
					title: __("Draft Already Exists"),
					indicator: "orange",
					message: __("{0} is already drafted against this order. Open and submit that one instead of raising a second.", [
						frappe.utils.get_form_link("Purchase Invoice", picked[0].draft_invoice, true),
					]),
				});
				return;
			}

			// The same mapper the Create > Purchase Invoice button on the order uses, so
			// what it carries over is decided by ERPNext, not by us.
			frappe.model.open_mapped_doc({
				method: "erpnext.buying.doctype.purchase_order.purchase_order.make_purchase_invoice",
				source_name: picked[0].purchase_order,
				freeze_message: __("Creating Purchase Invoice..."),
			});
		});
	},

	formatter: function (value, row, column, data, default_formatter) {
		value = default_formatter(value, row, column, data);

		// A remark on a row means the figures on it disagree with something, so it is
		// worth reading before anyone acts on the advance.
		if (column.fieldname === "remarks" && data && data.remarks) {
			value = `<span style="color: var(--red-600)">${value}</span>`;
		}

		return value;
	},
};
