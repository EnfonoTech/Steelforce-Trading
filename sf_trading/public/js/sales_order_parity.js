// The Sales Order behaves like the Sales Invoice for the three fields this site added to it.
//
//   * Sales Person fills itself from the user's own Sales Person permission. The field carries
//     ignore_user_permissions, so Frappe will not default it for us — the dropdown stays
//     unrestricted on purpose, and this only picks the obvious answer for the common case.
//   * Delivery Person offers the drivers of the order's branch, and is cleared when the payment
//     mode is anything but Cash: the field merely hides otherwise, and a hidden field keeps its
//     value. The server clears it as well (api/sales_invoice_override.clear_driver_for_non_cash),
//     because a form is not the only way in.

frappe.ui.form.on("Sales Order", {
	setup(frm) {
		frm.set_query("custom_driver", function (doc) {
			if (doc.branch) return { filters: { custom_branch: doc.branch } };
			return {};
		});
	},

	onload(frm) {
		sf_so_set_sales_person(frm);
	},

	refresh(frm) {
		sf_so_set_sales_person(frm);
	},

	custom_payment_mode(frm) {
		sf_so_clear_driver_for_non_cash(frm);
	},

	branch(frm) {
		// the driver list is branch-scoped, so a driver from the old branch has to go
		if (frm.doc.custom_driver) {
			frappe.db.get_value("Driver", frm.doc.custom_driver, "custom_branch").then(function (r) {
				const driver_branch = r && r.message && r.message.custom_branch;
				if (driver_branch && frm.doc.branch && driver_branch !== frm.doc.branch) {
					frm.set_value("custom_driver", null);
					frappe.show_alert(
						{ message: __("Delivery Person cleared — they belong to another branch."), indicator: "orange" },
						4
					);
				}
			});
		}
	},
});

function sf_so_set_sales_person(frm) {
	if (frm.doc.docstatus !== 0 || frm.doc.custom_sales_person) return;

	frappe.call({
		method: "frappe.client.get_list",
		args: {
			doctype: "User Permission",
			filters: { user: frappe.session.user, allow: "Sales Person" },
			fields: ["for_value"],
			limit_page_length: 2,
		},
		callback(r) {
			const rows = (r && r.message) || [];
			// exactly one permission is an answer; several is a choice, and the user makes it
			if (rows.length === 1 && !frm.doc.custom_sales_person) {
				frm.set_value("custom_sales_person", rows[0].for_value);
			}
		},
	});
}

function sf_so_clear_driver_for_non_cash(frm) {
	if (frm.doc.custom_payment_mode === "Cash" || !frm.doc.custom_driver) return;
	frm.set_value("custom_driver", null);
	frappe.show_alert(
		{ message: __("Delivery Person cleared — it applies to a cash sale only."), indicator: "orange" },
		4
	);
}
